// ABOUTME: API-price-equivalent cost accounting from Claude Code's own transcripts, for every model including the
// ABOUTME: gateway's GPT and Grok rows, which Claude Code itself prices with the wrong table.
'use strict';
// Why this exists: Claude Code reports a cost for a gateway model (it is not zero), but it prices an unrecognised id
// with its own fallback table — an Astra turn came back at roughly Opus-5 rates, about half Astra's real $10/$50. A
// plausible-looking wrong number is worse than none when the point is comparing cost against results across many
// agent runs, so we recompute from the raw token counts with a table we can audit and refresh.
//
// The transcripts are the right source: every session on the machine writes one, each assistant turn carries
// `message.model` and a full `message.usage` (input, output, cache read, cache creation, thinking), and they are
// already on disk — so this is retroactive over work that has already happened, with no instrumentation to keep alive.
// The corpus is ~4 GB over ~4000 files, so a full re-read per invocation is not an option: state is cached per file
// and only the bytes appended since last time are parsed.
const fs = require('fs');
const path = require('path');
const os = require('os');

const HOME = os.homedir();
const CFG = process.env.CLAUDE_CONFIG_DIR ? path.resolve(process.env.CLAUDE_CONFIG_DIR) : path.join(HOME, '.claude');
const PROJECTS = path.join(CFG, 'projects');
const ROOT = path.join(HOME, '.claude', 'claude-usage');
const CACHE = path.join(ROOT, 'cost-cache.json');
const PRICES_LIVE = path.join(ROOT, 'prices.json');       // refreshed from the network
const PRICES_OVERRIDE = path.join(ROOT, 'prices.override.json'); // hand-curated, always wins
const PRICES_SHIPPED = path.join(__dirname, 'prices.default.json'); // committed fallback

const readJson = (f) => JSON.parse(fs.readFileSync(f, 'utf8'));
function writeJsonAtomic(file, obj, mode = 0o600) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(obj), { mode });
  fs.renameSync(tmp, file);
}

// ---------- prices ----------
// Three layers so a refresh never loses a curated value: shipped defaults, then whatever the last refresh fetched,
// then the override file. `sourcedAt` travels with each entry so a report can say how old its rates are.
function loadPrices() {
  const out = { models: {}, sources: {} };
  for (const [layer, file] of [['shipped', PRICES_SHIPPED], ['fetched', PRICES_LIVE], ['override', PRICES_OVERRIDE]]) {
    let d = null;
    try { d = readJson(file); } catch { continue; }
    out.sources[layer] = { at: d.fetchedAt || d.sourcedAt || null, from: d.source || null, count: Object.keys(d.models || {}).length };
    for (const [id, p] of Object.entries(d.models || {})) out.models[id] = { ...p, layer, sourcedAt: p.sourcedAt || d.fetchedAt || d.sourcedAt || null };
  }
  return out;
}
// Gateway ids are the backend id with a `claude-` prefix and sometimes a `[1m]` picker suffix; strip both before
// looking a model up, and try the canonical id last so `claude-gpt-6-astra[1m]` and `gpt-6-astra` share one entry.
// Ids arrive in several shapes and each needs a different strip before the table can answer:
//   claude-gpt-6-astra[1m]  -> gpt-6-astra      (gateway prefix + picker suffix)
//   claude-haiku-4-5-20251001 -> claude-haiku-4-5 (a dated snapshot bills as its family)
//   claude-gpt-5.6-sol-fast -> gpt-5.6-sol      (same model served faster; no separate published rate)
// The candidates are tried in order of specificity so an exact entry always wins over a fallback.
function normalizeModel(id) {
  if (!id) return null;
  const base = String(id).replace(/\[1m\]$/, '');
  const cands = [base];
  const push = (v) => { if (v && !cands.includes(v)) cands.push(v); };
  const undated = base.replace(/-20\d{6}$/, '');
  push(undated);
  const ungated = undated.replace(/^claude-(?=gpt-|grok-)/, '');
  push(ungated);
  push(ungated.replace(/-fast$/, ''));
  return { id: base, canonical: ungated, candidates: cands };
}
function rateFor(model, prices) {
  const n = normalizeModel(model);
  if (!n) return null;
  for (const c of n.candidates) if (prices.models[c]) return prices.models[c];
  return null;
}

// ---------- reading a transcript ----------
// cacheWrite is kept as the total for display; cw5m/cw1h are what get priced, because Anthropic charges a 1-hour
// cache write at 2x input and a 5-minute one at 1.25x - a 60% difference that a single bucket silently averaged away.
// An aggregate holds two different kinds of number, and conflating them is how a report starts lying.
// The first ten are RAW OBSERVATIONS: what the transcripts actually said, kept whole for diagnosis. `cwConflict` is
// the size of a disagreement between a turn's flat cache-write count and its own per-TTL split - a diagnostic
// quantity, never a billable token class, because nobody is charged for a contradiction.
// The `withheld*` counters are the part of those observations that is NOT PRICE-ELIGIBLE: the contribution of turns
// whose own counts contradict each other. Pricing subtracts them, so such a turn is observed in full and billed not
// at all. It is the WHOLE turn and not merely its write, because the two counts also disagree about the prompt SIZE,
// which is what decides whether the turn sits above a long-context threshold - there is no reading of that turn we
// can defend, so we defend none of it.
const EMPTY = () => ({
  turns: 0, in: 0, out: 0, cacheRead: 0, cacheWrite: 0, cw5m: 0, cw1h: 0, cwUnknownTtl: 0, cwConflict: 0, think: 0,
  withheldTurns: 0, withheldIn: 0, withheldOut: 0, withheldCacheRead: 0, withheldCw5m: 0, withheldCw1h: 0,
});
// Above a prompt-size threshold the providers charge a second, higher rate for EVERYTHING - Astra goes $10/$50 ->
// $20/$75 above 272k, Grok doubles above 200k - and a 920k window means that is routine, not an edge case. Summing
// tokens per model would smear the two rates together, so each turn lands in a bucket by its prompt size and the
// buckets are priced separately. These edges cover both thresholds exactly: a bucket whose LOWER bound is at or above
// a model's threshold is entirely tiered, and no bucket ever straddles one.
const BUCKET_EDGES = [200000, 272000];
const NB = BUCKET_EDGES.length + 1;
// One version for BOTH cache paths, bumped together whenever the representation changes: the per-session cache now
// carries it too, so an old status-line entry can no longer outlive the meaning of its own buckets.
// v5: ids are a file's full PRESENCE set (ownership is re-derived per scan, not frozen into the aggregates), each
// entry carries the contributions of its contested ids, and the write split keeps its unclassified residue.
// v6: every bucket separates raw observations from price-eligible quantities (the `withheld*` counters), so a turn
// whose own counts contradict each other can be reported without being billed.
const CACHE_VERSION = 6; // v4: recursive discovery (subagents), cross-file id ownership, unknown-TTL bucket
const EMPTY_BUCKETS = () => Array.from({ length: NB }, EMPTY);
const bucketOf = (prompt) => { let i = 0; while (i < BUCKET_EDGES.length && prompt > BUCKET_EDGES[i]) i += 1; return i; };
const promptOf = (u) => (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
function addUsage(agg, u) {
  const inTok = u.input_tokens || 0;
  const outTok = u.output_tokens || 0;
  const crTok = u.cache_read_input_tokens || 0;
  const cwTotal = u.cache_creation_input_tokens || 0;
  agg.turns += 1;
  agg.in += inTok;
  agg.out += outTok;
  agg.cacheRead += crTok;
  agg.cacheWrite += cwTotal;
  // The split is authoritative when present. When it is NOT present the TTL is unknown: the flat field predates the
  // split, but absence cannot prove 5-minute if a harness simply dropped the metadata, and the difference is 60%. So
  // it goes to a third bucket that prices as a floor rather than silently assuming the cheaper rate. Measured over
  // 600 sampled transcripts (~31k writes, top-level and nested): the split is ALWAYS present and non-zero, so this
  // bucket is expected to stay empty — it exists so that if that ever changes, the report says so.
  const split = u.cache_creation || null;
  const h1 = split ? (split.ephemeral_1h_input_tokens || 0) : 0;
  const m5 = split ? (split.ephemeral_5m_input_tokens || 0) : 0;
  agg.cw1h += h1; agg.cw5m += m5;
  // The flat count and the split are two statements about the same tokens, and they do not always agree. Whatever the
  // split never classified is a REMAINDER, not zero: billing only the classified part quietly dropped it (a 1M write
  // split 400k/100k lost 500k and still reported an exact price). A positive remainder joins the unknown-TTL bucket,
  // which prices as a floor.
  // A NEGATIVE one - a split claiming MORE than the flat total - is not a remainder at all but a contradiction, and
  // there is no honest way to pick a winner. Believing the split billed 2M tokens where the transcript's own flat
  // count said 1M were written, and then called the result a floor: a 2x OVER-estimate wearing an under-estimate's
  // label. So the turn's money is withheld in full - every class of it, because the same disagreement moves the
  // prompt size that decides the long-context tier - while its counters stay on the books as observations.
  const residue = cwTotal - h1 - m5;
  if (residue > 0) agg.cwUnknownTtl += residue;
  else if (residue < 0) {
    agg.cwConflict += -residue;
    agg.withheldTurns += 1;
    agg.withheldIn += inTok;
    agg.withheldOut += outTok;
    agg.withheldCacheRead += crTok;
    agg.withheldCw5m += m5;
    agg.withheldCw1h += h1;
  }
  agg.think += (u.output_tokens_details || {}).thinking_tokens || 0;
}
// One file's aggregate, parsed from `offset` onward. Returns the new offset and what it learned, so a growing
// transcript is only ever read once per byte.
// What comes back describes THIS FILE ALONE: its own billed turns, deduped against its own replays, as if it owned
// everything it contains. Nothing outside the file influences it, which is what makes a file's cached entry a stable
// fact rather than a snapshot of whatever else had been scanned that day. Deciding which file a shared id is counted
// under is a separate step, taken over the whole corpus, in scanAll.
// `want` names ids whose single-turn contribution to record alongside the aggregate, so that a file which turns out
// NOT to own one of them can hand it back later without the corpus being re-read.
function scanFile(file, offset = 0, seen = null, want = null) {
  const st = fs.statSync(file);
  // Truncated or rewritten (a compaction can shrink a transcript): start over AND say so, because the aggregates the
  // caller is holding describe content that no longer exists. Merging into them would keep billing the vanished turns
  // for good - the one failure mode of an incremental cache that never corrects itself.
  let restarted = false;
  if (st.size < offset) { offset = 0; seen = null; restarted = true; }
  const fd = fs.openSync(file, 'r');
  let text = '';
  try {
    const len = st.size - offset;
    if (len > 0) { const buf = Buffer.alloc(len); fs.readSync(fd, buf, 0, len, offset); text = buf.toString('utf8'); }
  } finally { fs.closeSync(fd); }
  // A partial last line means the session is mid-write: stop at the last newline and leave the rest for next time.
  const cut = text.lastIndexOf('\n');
  const usable = cut < 0 ? '' : text.slice(0, cut + 1);
  const nextOffset = offset + Buffer.byteLength(usable, 'utf8');

  const models = {};
  const byDay = {};
  const dup = {};
  const ids = seen ? new Set(seen) : new Set();
  let cwd = null, first = null, last = null;
  const cwds = new Set();
  for (const line of usable.split('\n')) {
    if (!line || line.indexOf('"usage"') === -1) continue; // cheap gate before JSON.parse over millions of lines
    let d = null; try { d = JSON.parse(line); } catch { continue; }
    const m = d.message;
    if (!m || !m.usage || !m.model) continue;
    if (m.model === '<synthetic>') continue; // a limit refusal, not a billed turn
    // The same assistant message can appear twice (a resumed or replayed session); message.id is stable, so it
    // decides. Without this a resume would double every token it replays.
    // `ids` is this file's PRESENCE set: every billed id it contains, whether or not another file contains it too
    // (2 of 49,289 subagent message ids also appear in a parent transcript). Recording presence rather than a claim
    // is what lets the corpus decide ownership afresh each scan, so a compacted or deleted owner cannot take a turn
    // that still exists elsewhere down with it.
    if (m.id) { if (ids.has(m.id)) continue; ids.add(m.id); }
    if (d.cwd) { if (!cwd) cwd = d.cwd; cwds.add(d.cwd); }
    const ts = d.timestamp || null;
    if (ts) { if (!first || ts < first) first = ts; if (!last || ts > last) last = ts; }
    const day = ts ? ts.slice(0, 10) : 'unknown';
    const key = m.model;
    const b = bucketOf(promptOf(m.usage));
    (models[key] || (models[key] = EMPTY_BUCKETS()));
    addUsage(models[key][b], m.usage);
    const dd = byDay[day] || (byDay[day] = {});
    (dd[key] || (dd[key] = EMPTY_BUCKETS()));
    addUsage(dd[key][b], m.usage);
    if (want && m.id && want.has(m.id)) { const one = EMPTY(); addUsage(one, m.usage); dup[m.id] = { model: key, day, b, agg: one }; }
  }
  return { offset: nextOffset, size: st.size, mtime: st.mtimeMs, models, byDay, dup, cwd, cwds: [...cwds], first, last, ids: [...ids], restarted };
}
const mergeAgg = (a, b) => { for (const k of Object.keys(b)) a[k] = (a[k] || 0) + b[k]; return a; };
function mergeModels(into, from) {
  for (const [m, bs] of Object.entries(from)) {
    into[m] || (into[m] = EMPTY_BUCKETS());
    for (let i = 0; i < NB; i++) mergeAgg(into[m][i], bs[i] || EMPTY());
  }
  return into;
}
const flatten = (bs) => bs.reduce((a, b) => mergeAgg(a, b), EMPTY());

// ---------- one cache contract, shared by both paths ----------
// The corpus cache and the per-session cache hold the SAME kind of entry: a byte offset, a dedupe set and bucketed
// aggregates. They therefore share one version and one shape check. An entry written by a build whose buckets meant
// something else is rejected and re-derived from the bytes, never merged into and never migrated by inventing the
// fields it lacks - a pre-split entry has no way to say how much of its flat cache write was a 1-hour write, and
// guessing is the error this engine exists to avoid. The transcript is still on disk, so a rebuild always exists.
const AGG_KEYS = Object.keys(EMPTY());
const aggOk = (a) => !!a && typeof a === 'object' && AGG_KEYS.every((k) => Number.isFinite(a[k]));
const bucketsOk = (bs) => Array.isArray(bs) && bs.length === NB && bs.every(aggOk);
const modelsOk = (m) => !!m && typeof m === 'object' && Object.values(m).every(bucketsOk);
const byDayOk = (d) => !!d && typeof d === 'object' && Object.values(d).every(modelsOk);
const dupOk = (d) => !!d && typeof d === 'object' && Object.values(d).every((c) => !!c && typeof c === 'object'
  && typeof c.model === 'string' && Number.isInteger(c.b) && c.b >= 0 && c.b < NB && aggOk(c.agg));
function entryOk(e) {
  if (!e || typeof e !== 'object') return false;
  if (!Number.isFinite(e.size) || !Number.isFinite(e.mtime) || !Number.isFinite(e.offset) || e.offset > e.size) return false;
  if (!Array.isArray(e.ids) || !modelsOk(e.models)) return false;
  if (e.byDay !== undefined && !byDayOk(e.byDay)) return false;
  if (e.dup !== undefined && !dupOk(e.dup)) return false;
  return true;
}
// The single place where a cached entry and a fresh scan are combined. Both paths go through it, so the rule that a
// restarted (truncated or rewritten) file has no valid history to merge into cannot hold on one path and not the
// other. `extra` carries the fields only the corpus walk keeps.
function foldEntry(prev, got, extra = null) {
  const base = got.restarted ? null : prev;
  const entry = {
    v: CACHE_VERSION, size: got.size, mtime: got.mtime, offset: got.offset, ids: got.ids,
    models: mergeModels({ ...(base?.models || {}) }, got.models),
  };
  return extra ? { ...entry, ...extra(base) } : entry;
}

// ---------- one session, fast enough for a status line ----------
// The status line runs on EVERY prompt render, so it must not touch the 3 MB corpus cache or re-read a 59 MB
// transcript. This keeps a tiny per-session file and parses only the bytes appended since the last render.
const SESSION_DIR = path.join(ROOT, 'session-cost');
function sessionCost(sessionId, prices) {
  if (!sessionId || !/^[\w-]{6,80}$/.test(sessionId)) return null;
  const file = transcriptOf(sessionId);
  if (!file) return null;
  const cacheFile = path.join(SESSION_DIR, `${sessionId}.json`);
  let prev = null;
  // Same contract as the corpus cache: a wrong version or a malformed entry is discarded, and the next line below
  // rebuilds from offset 0. Without this an entry written before the TTL split merged in and priced its cache writes
  // at $0 while still reporting `partial: false` - a missing-information hole that looked like an exact answer.
  try { const p = readJson(cacheFile); if (p && p.v === CACHE_VERSION && entryOk(p)) prev = p; } catch { /* first render of this session */ }
  let st = null; try { st = fs.statSync(file); } catch { return null; }
  let entry;
  if (prev && prev.size === st.size && prev.mtime === st.mtimeMs) entry = prev;
  else {
    // The dedupe set is kept past EOF here for exactly the reason it is in scanAll: reaching the end of the file does
    // not finish the session, and the next render's appended bytes routinely replay a turn already counted. Dropping
    // it billed the replay again, so a long session's status line drifted upward all day ($15 where $10 was right).
    entry = foldEntry(prev, scanFile(file, prev?.offset || 0, prev?.ids));
    try { writeJsonAtomic(cacheFile, entry); } catch { /* a status line must never fail on a write */ }
  }
  // The same pricing rules as the report, because the status line is the figure Louis reads all day: a turn whose own
  // counts contradict each other is withheld here too, rather than quietly showing $20 for a 1M write.
  let usd = 0, withheldTurns = 0;
  const reasons = new Set();
  const unpriced = [];
  for (const [model, buckets] of Object.entries(entry.models)) {
    const rate = rateFor(model, prices);
    const bs = Array.isArray(buckets) ? buckets : [buckets];
    const wh = bs.reduce((a, b) => a + ((b && b.withheldTurns) || 0), 0);
    withheldTurns += wh;
    if (!rate) { unpriced.push(normalizeModel(model).canonical); reasons.add(REASON_RATE); if (wh) reasons.add(REASON_CONFLICT); continue; }
    const c = costOf(bs, rate);
    usd += c.usd;
    for (const r of c.reasons) reasons.add(r);
  }
  return { usd, partial: reasons.size > 0, reasons: [...reasons].sort(), withheldTurns, unpriced, models: Object.keys(entry.models) };
}
function transcriptOf(sessionId) {
  let dirs = [];
  try { dirs = fs.readdirSync(PROJECTS); } catch { return null; }
  for (const d of dirs) {
    const f = path.join(PROJECTS, d, `${sessionId}.jsonl`);
    if (fs.existsSync(f)) return f;
  }
  return null;
}

// ---------- the corpus, incrementally ----------
// Subagent transcripts live BELOW the session that spawned them, at more than one depth:
//   <project>/<session>.jsonl                                  the session itself
//   <project>/<session>/subagents/agent-<id>.jsonl              a subagent it dispatched
//   <project>/<session>/subagents/workflows/.../*.jsonl         deeper still
// This walked one level only, so every subagent was absent from every total — measured here, 1196 nested files,
// 2.0 GB, 49,320 billed turns carrying 7.9 BILLION cache-read tokens, more than the whole top level. A fixed depth
// would just move the bug, so the walk is fully recursive.
function sessionFiles() {
  try {
    return fs.readdirSync(PROJECTS, { recursive: true, withFileTypes: true })
      .filter((e) => e.isFile() && e.name.endsWith('.jsonl'))
      .map((e) => path.join(e.parentPath || e.path, e.name))
      .sort(); // stable order, so cross-file id ownership below is deterministic
  } catch { return []; }
}
// What a transcript is, from its path alone. `subagents/` in the path is the filesystem's own statement of ancestry;
// it is NOT metadata-verified lineage, so `parent` is exposed for a consumer that wants to attribute a run, and
// nothing here claims a turn belongs to a campaign root.
function identify(file) {
  const rel = path.relative(PROJECTS, file);
  const parts = rel.split(path.sep);
  const id = path.basename(file, '.jsonl');
  if (parts.length <= 2) return { id, kind: 'session', parent: null };
  return { id, kind: 'agent', parent: parts[1].replace(/\.jsonl$/, '') };
}
// The dedupe set is kept for EVERY file, forever. It was originally dropped once a scan reached EOF, on the theory
// that a finished session cannot gain a replayed message - which is false: reaching EOF does not finish a session,
// and Claude Code appends replays of earlier turns later. Scan to EOF, append a replay, rescan, and the replay was
// billed a second time (reproduced: 207 where 107 was correct). It is ~40 bytes per billed message.
function scanAll({ cacheFile = CACHE, quiet = true } = {}) {
  let cache = { version: CACHE_VERSION, files: {} };
  // The document's version gates the whole file; each entry is then shape-checked on its own, so one corrupt row
  // cannot poison a total (and cannot be silently read as an exact zero).
  try {
    const c = readJson(cacheFile);
    if (c && c.version === CACHE_VERSION && c.files && typeof c.files === 'object') {
      for (const [f, e] of Object.entries(c.files)) if (entryOk(e)) cache.files[f] = e;
    }
  } catch { /* first run, or a schema we no longer read */ }
  const files = sessionFiles();
  const sessions = {};
  let read = 0, skipped = 0;
  const corpusExtra = (got) => (base) => ({
    cwd: got.cwd || base?.cwd || null,
    cwds: [...new Set([...(base?.cwds || []), ...got.cwds])], // a session that moved reports more than one
    first: base?.first && (!got.first || base.first < got.first) ? base.first : got.first,
    last: got.last || base?.last || null,
    byDay: (() => { const b = JSON.parse(JSON.stringify(base?.byDay || {})); for (const [day, ms] of Object.entries(got.byDay)) { b[day] || (b[day] = {}); mergeModels(b[day], ms); } return b; })(),
    dup: { ...(base?.dup || {}), ...got.dup },
  });

  // Pass 1 - bring every file's own account up to date, reading only the bytes it has gained. Nothing here looks at
  // any other file, so what lands in the cache is a fact about that transcript rather than a snapshot of the order
  // the corpus happened to be ingested in.
  const present = [];
  for (const file of files) {
    let st = null; try { st = fs.statSync(file); } catch { continue; }
    const prev = cache.files[file];
    if (prev && prev.size === st.size && prev.mtime === st.mtimeMs) { cache.files[file] = prev; skipped += 1; }
    else {
      const got = scanFile(file, prev?.offset || 0, prev?.ids);
      cache.files[file] = foldEntry(prev, got, corpusExtra(got));
      read += 1;
    }
    present.push(file);
  }
  for (const known of Object.keys(cache.files)) if (!files.includes(known)) delete cache.files[known]; // session deleted

  // Pass 2 - ownership, derived from the corpus as it stands now. A billed id is counted under the FIRST file, in the
  // sorted walk, that still contains it. That is deterministic SOURCE ALLOCATION and nothing more: it says which file
  // a turn is counted under, never which agent produced it. Because it is recomputed from presence rather than frozen
  // into the aggregates, a compacted or deleted owner hands its shared ids back to whatever copy survives, and a file
  // that appears late but sorts early takes the ids it should have owned all along.
  const owner = new Map();
  const contested = new Map(); // file -> the ids it holds but does not own
  for (const file of present) {
    for (const id of cache.files[file].ids) {
      if (!owner.has(id)) owner.set(id, file);
      else { let s = contested.get(file); if (!s) contested.set(file, (s = new Set())); s.add(id); }
    }
  }
  // Handing an id back costs no reading: each entry carries the contributions of the ids it is contesting. The first
  // scan in which an id becomes contested is the exception - that ONE file is re-derived from its own bytes, which is
  // bounded by the file, never a replay of the corpus, and never needed again for that id.
  for (const [file, ids] of contested) {
    if ([...ids].every((id) => cache.files[file].dup?.[id])) continue;
    const got = scanFile(file, 0, null, ids);
    cache.files[file] = foldEntry(null, got, corpusExtra(got));
    read += 1;
  }

  for (const file of present) {
    const entry = cache.files[file];
    const drop = contested.get(file);
    const { models, byDay } = ownOnly(entry, drop);
    // `allocatedElsewhere` records, for each id this file holds but does not own, WHICH file owns it. A selection can
    // then tell whether the messages it gave up went to a source it also selected (nothing missing) or to one outside
    // it (the subtotal is allocation-limited). It is tiny: only genuinely duplicated ids ever appear in it.
    const allocatedElsewhere = {};
    if (drop) for (const id of drop) allocatedElsewhere[id] = owner.get(id);
    // Keyed by PATH, not by session id: a repo move leaves the same session id in two project directories (two such
    // pairs on this machine), and keying by id let whichever was read last silently displace the other's cost.
    sessions[file] = { file, ...identify(file), cwd: entry.cwd, cwds: entry.cwds || (entry.cwd ? [entry.cwd] : []), first: entry.first, last: entry.last, models, byDay, allocatedElsewhere };
  }
  writeJsonAtomic(cacheFile, cache);
  if (!quiet) process.stderr.write(`cost: ${read} transcripts read, ${skipped} unchanged\n`);
  return { sessions, read, skipped };
}
// What a file contributes once the ids it does not own are taken back out of it. The entry itself is left untouched -
// it is the durable fact; this is the projection of it that the current corpus asks for.
function ownOnly(entry, drop) {
  if (!drop || !drop.size) return { models: entry.models, byDay: entry.byDay || {} };
  const models = JSON.parse(JSON.stringify(entry.models));
  const byDay = JSON.parse(JSON.stringify(entry.byDay || {}));
  for (const id of drop) {
    const c = entry.dup?.[id];
    if (!c) continue; // pass 2 guarantees a contribution for every contested id it leaves behind
    for (const scope of [models, byDay[c.day]]) {
      const bs = scope?.[c.model];
      if (bs && bs[c.b]) for (const k of AGG_KEYS) bs[c.b][k] -= c.agg[k];
    }
  }
  return { models, byDay };
}

// ---------- money ----------
// Cache writes cost more than input and cache reads far less; both are per-provider multipliers of the input rate
// unless the table states absolute values, so a missing multiplier must not silently price a cache read as input.
// Why a figure is incomplete matters as much as that it is: "a rate is missing" means the subtotal is BELOW the
// truth, while "these counts contradict each other" means we declined to guess in either direction. Reporting the
// first when the second happened is how the engine came to describe a 2x over-charge as a floor.
const REASON_RATE = 'rate-missing';
const REASON_TTL = 'unknown-ttl';
const REASON_CONFLICT = 'count-conflict';
function costOneBucket(agg, rate) {
  const perM = (n, r) => (n / 1e6) * r;
  let usd = 0;
  const reasons = new Set();
  // Every term is guarded the same way: a rate we do not have never contributes a number. Multiplying zero tokens by
  // a missing rate used to yield NaN, which silently poisoned the whole report's total rather than flagging one row.
  const term = (n, r) => { if (!n) return; if (r == null || !Number.isFinite(r)) { reasons.add(REASON_RATE); return; } usd += perM(n, r); };
  // Money is derived from PRICE-ELIGIBLE quantities: the observations minus whatever belongs to a turn whose own
  // counts contradict each other. Valid turns in the same bucket, model and day are unaffected and price normally.
  const ok = (observed, withheld) => Math.max(0, (agg[observed] || 0) - (agg[withheld] || 0));
  term(ok('in', 'withheldIn'), rate.input);
  term(ok('out', 'withheldOut'), rate.output);
  // A missing cache rate must never be priced as input - cache reads are the dominant term in this corpus, so a
  // silent substitution would be the largest error in the report.
  // 5-minute writes use the published cache-write rate. A 1-hour write has its own rate: Anthropic charges 2x input
  // (the claude-api reference), and models.dev publishes only the 5m one, so it is derived rather than guessed - and
  // only for Anthropic, because no other provider here has a TTL to choose.
  term(ok('cw5m', 'withheldCw5m'), rate.cacheWrite);
  const eligible1h = ok('cw1h', 'withheldCw1h');
  if (eligible1h) {
    // Falling back to the 5-minute rate here would understate a 1-hour write by 60% and say nothing. Only two
    // sources are accepted: an explicit rate in the table, or Anthropic's documented 2x input. Anything else is
    // flagged - no other provider here has a TTL, so a 1h write under an unknown provider is a surprise, not a
    // default. (This is the same rule as cache reads: never substitute a rate we do not have.)
    const r1h = rate.cacheWrite1h != null ? rate.cacheWrite1h : (rate.provider === 'anthropic' ? rate.input * 2 : null);
    term(eligible1h, r1h);
  }
  if (agg.cwUnknownTtl) reasons.add(REASON_TTL); // a write whose TTL we cannot establish: never priced at a guess
  if (agg.withheldTurns || agg.cwConflict) reasons.add(REASON_CONFLICT); // a turn we declined to price in either direction
  term(ok('cacheRead', 'withheldCacheRead'), rate.cacheRead);
  return { usd, partial: reasons.size > 0, reasons };
}
// A tier is the same rate card at a different price, but the published source spells its cache fields in snake_case
// while the pricer reads camelCase, so `{...rate, ...tier}` alone left every tiered cache rate at the standard price.
// The mapping is by PRESENCE, not by truthiness: a tier that carries `cache_read: null` is stating it has no rate for
// that class above the threshold, and must stay a floor rather than silently inheriting the cheaper base one, while a
// tier that omits the field is saying "unchanged", and inherits.
function tierRate(rate, tier) {
  const use = { ...rate, ...tier };
  if ('cache_read' in tier) use.cacheRead = tier.cache_read;
  if ('cache_write' in tier) use.cacheWrite = tier.cache_write;
  return use;
}
function costOf(buckets, rate) {
  if (!rate) return null;
  const bs = Array.isArray(buckets) ? buckets : [buckets];
  const tier = (rate.tiers || []).find((t) => t?.tier?.type === 'context') || null;
  const threshold = tier ? tier.tier.size : Infinity;
  let usd = 0, tiered = 0, withheld = 0;
  const reasons = new Set();
  bs.forEach((agg, i) => {
    if (!agg || !agg.turns) return;
    const lower = i === 0 ? 0 : BUCKET_EDGES[i - 1];
    const use = lower >= threshold ? tierRate(rate, tier) : rate; // whole bucket sits above the threshold
    // A withheld turn is not a turn priced at a tier. Its two counts disagree about the prompt size, so which side of
    // the threshold it belongs on is exactly one of the things we do not know about it.
    if (lower >= threshold) tiered += Math.max(0, agg.turns - (agg.withheldTurns || 0));
    withheld += agg.withheldTurns || 0;
    const c = costOneBucket(agg, use);
    usd += c.usd;
    for (const r of c.reasons) reasons.add(r);
  });
  return { usd, partial: reasons.size > 0, tieredTurns: tiered, withheldTurns: withheld, reasons };
}
// A directory filter has to respect path boundaries: `--repo ~/Work/Alakazam` must include ~/Work/Alakazam/mira and
// must NOT match ~/Work/AlakazamOther. It also has to consider EVERY cwd a session reported, because a repo that moved
// leaves the old path in the transcript (4 of 120 sampled transcripts report more than one cwd).
// The selector is normalised first because a shell completion hands over a trailing slash, and `/w/A/mira/` then
// failed to contain `/w/A/mira` itself - the root directory silently dropped out of its own filter while everything
// beneath it stayed in.
const underPath = (cwd, root) => {
  if (!cwd || !root) return false;
  const r = String(root).replace(/\/+$/, '') || '/';
  return cwd === r || cwd.startsWith(r.endsWith('/') ? r : `${r}/`);
};
// Roll the per-session aggregates up along whichever axis was asked for, pricing each model separately.
// Selecting by an explicit SET of root session ids, with their descendants, is a different question from "what did
// this repo cost": a campaign root is a session, not a directory. Descendants come from path nesting, which for this
// layout is complete at any depth - a nested transcript's enclosing session directory IS its root, whether it sits in
// subagents/ or deeper under subagents/workflows/. Roots that match nothing are reported, never silently dropped.
function resolveRoots(sessions, roots) {
  const want = new Set(roots);
  const selected = new Set();
  const matchedRoots = new Set();
  for (const s of Object.values(sessions)) {
    if (want.has(s.id)) { selected.add(s.file); matchedRoots.add(s.id); }
    else if (s.parent && want.has(s.parent)) { selected.add(s.file); matchedRoots.add(s.parent); }
  }
  return { selected, resolved: [...matchedRoots].sort(), unknown: roots.filter((r) => !matchedRoots.has(r)).sort() };
}
function summarize({ sessions, prices, since = null, until = null, group = 'model', cwdFilter = null, rootFilter = null }) {
  const rows = new Map();
  const unpriced = new Set();
  const bump = (key, model, buckets) => {
    // Flatten for the DISPLAY counters only. Money is derived from the buckets themselves, because the prompt-size
    // bucket is what decides whether a turn is above the provider's long-context threshold - flattening first threw
    // that away and priced every tiered turn at the standard rate (a 300k Astra turn came out $53 instead of $81).
    const bs = Array.isArray(buckets) ? buckets : [buckets];
    const agg = flatten(bs);
    const r = rows.get(key) || { key, turns: 0, in: 0, out: 0, cacheRead: 0, cacheWrite: 0, cw5m: 0, cw1h: 0, cwUnknownTtl: 0, cwConflict: 0, think: 0, usd: 0, partial: false, tieredTurns: 0, withheldTurns: 0, reasons: new Set(), models: new Set() };
    r.turns += agg.turns; r.in += agg.in; r.out += agg.out; r.cacheRead += agg.cacheRead; r.cacheWrite += agg.cacheWrite;
    r.cw5m += agg.cw5m; r.cw1h += agg.cw1h; r.cwUnknownTtl += agg.cwUnknownTtl; r.cwConflict += agg.cwConflict; r.think += agg.think;
    r.withheldTurns += agg.withheldTurns;
    r.models.add(model);
    const rate = rateFor(model, prices);
    // A model with no rate at all is a different incompleteness from a turn we declined to price, and the row now
    // carries which one happened - the report used to announce a missing rate whatever the cause.
    if (!rate) { unpriced.add(normalizeModel(model).canonical); r.partial = true; r.reasons.add(REASON_RATE); if (agg.withheldTurns) r.reasons.add(REASON_CONFLICT); }
    else {
      const c = costOf(bs, rate);
      r.usd += c.usd; if (c.partial) r.partial = true; r.tieredTurns += c.tieredTurns;
      for (const x of c.reasons) r.reasons.add(x);
    }
    rows.set(key, r);
  };
  const rootScope = rootFilter ? resolveRoots(sessions, rootFilter) : null;
  const inScope = (s) => !(rootScope && !rootScope.selected.has(s.file))
    && !(cwdFilter && !(s.cwds || []).some((c) => underPath(c, cwdFilter)));
  const selectedFiles = new Set(Object.values(sessions).filter(inScope).map((s) => s.file));
  for (const s of Object.values(sessions)) {
    if (!inScope(s)) continue;
    const sid = s.id || path.basename(s.file || '', '.jsonl');
    const inWindow = (day) => (!since || day >= since) && (!until || day <= until);
    if (since || until || group === 'day') {
      for (const [day, ms] of Object.entries(s.byDay)) {
        if (!inWindow(day)) continue;
        for (const [model, agg] of Object.entries(ms)) bump(group === 'day' ? day : group === 'model' ? model : group === 'session' ? sid : (s.cwd || '?'), model, agg);
      }
    } else {
      for (const [model, agg] of Object.entries(s.models)) bump(group === 'model' ? model : group === 'session' ? sid : (s.cwd || '?'), model, agg);
    }
  }
  // `usd` is a KNOWN PRICED SUBTOTAL, never a certified total: `partialReasons` says what it had to leave out and
  // why, and `withheldTurns` says how many turns carry no money at all.
  const out = [...rows.values()].map(({ reasons, models, ...r }) => ({ ...r, partialReasons: [...reasons].sort(), models: [...models].sort() }));
  return { rows: out.sort((a, b) => b.usd - a.usd), unpriced: [...unpriced].sort(), rootScope, allocationCoverage: allocationCoverage(sessions, selectedFiles, inScope) };
}
// How much of what the SELECTED sources contain actually ends up in their own subtotal.
// Every message is allocated exactly once corpus-wide, to a deterministic source file. When a selected source holds a
// copy of a message whose owner sits OUTSIDE the selection, that message is counted there instead - correctly, and
// only once - so this selection's subtotal is limited by the allocation rather than by pricing. `row.partial`
// answers a different question (could we price what we were given), and would happily say `false` here, which is how
// a scoped figure came to assert a completeness it had no way to see.
// This counts DISTINCT message ids, so one id held by several selected sources is one gap. It is source-allocation
// coverage and nothing else: it is not proof that another goal or agent produced the turn, and not a second total.
function allocationCoverage(sessions, selectedFiles, inScope) {
  const missing = new Set();
  for (const s of Object.values(sessions)) {
    if (!inScope(s)) continue;
    for (const [id, ownerFile] of Object.entries(s.allocatedElsewhere || {})) {
      if (!selectedFiles.has(ownerFile)) missing.add(id);
    }
  }
  return {
    complete: missing.size === 0,
    allocatedElsewhereIds: missing.size,
    meaning: 'selected sources contain this many shared message ids whose deterministic global source allocation is outside the selected scope',
  };
}

// One unauthenticated GET, cron-able, no agent in the loop: models.dev publishes per-MILLION numbers with cache and
// context-tier fields, which is why it is primary. LiteLLM is fetched as a CHECK, not a source - it publishes
// per-token floats and disagreements are what caught OpenRouter reporting gpt-5.6-sol at its batch rate. A
// disagreement is reported, never silently averaged.
const PRICE_SOURCE = 'https://models.dev/api.json';
const PRICE_CHECK = 'https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json';
const PRICE_PROVIDERS = ['anthropic', 'openai', 'xai'];
async function fetchPrices({ check = true, fetchImpl = fetch } = {}) {
  const res = await fetchImpl(PRICE_SOURCE, { signal: AbortSignal.timeout(60000) });
  if (!res.ok) throw new Error(`${PRICE_SOURCE} answered HTTP ${res.status}`);
  const src = await res.json();
  const models = {};
  for (const prov of PRICE_PROVIDERS) {
    for (const [id, m] of Object.entries(src[prov]?.models || {})) {
      const c = m.cost || {};
      if (typeof c.input !== 'number') continue; // a model with no published rate must stay absent, not become 0
      models[id] = { input: c.input, output: c.output ?? null, cacheRead: c.cache_read ?? null, cacheWrite: c.cache_write ?? null, provider: prov };
      if (Array.isArray(c.tiers) && c.tiers.length) models[id].tiers = c.tiers;
    }
  }
  if (!Object.keys(models).length) throw new Error('price source returned no usable models — refusing to overwrite the table');
  const doc = { source: PRICE_SOURCE, fetchedAt: new Date().toISOString(), models };
  const disagreements = [];
  if (check) {
    try {
      const r2 = await fetchImpl(PRICE_CHECK, { signal: AbortSignal.timeout(60000) });
      if (r2.ok) {
        const lite = await r2.json();
        for (const [id, p] of Object.entries(models)) {
          const l = lite[id];
          if (!l || typeof l.input_cost_per_token !== 'number') continue;
          const li = l.input_cost_per_token * 1e6, lo = (l.output_cost_per_token || 0) * 1e6;
          const off = (a, b) => a != null && b != null && Math.abs(a - b) > Math.max(0.01, Math.abs(b) * 0.01);
          if (off(li, p.input) || off(lo, p.output)) disagreements.push({ id, primary: [p.input, p.output], check: [Number(li.toFixed(4)), Number(lo.toFixed(4))] });
        }
      }
    } catch { /* the check is best-effort; its absence must not block a refresh */ }
  }
  return { doc, disagreements };
}

// Aggregates are bucketed by UTC DAY (an ISO timestamp sliced to 10 chars), so --since/--until resolve to whole UTC
// days and nothing finer. Per-turn timestamps exist in the transcripts but are not retained, so sub-day attribution
// is NOT supported and must not be claimed. Declared here so a consumer can assert on it.
const TIME_PRECISION = 'utc-day';
// The fingerprint must cover EVERY dimension that can change a computed cost, not just the base rates. `provider`
// decides whether a 1-hour cache write gets Anthropic's derived 2x rate or is refused as unrateable, and `tiers`
// carries both the thresholds and the above-threshold rates. A fingerprint over base rates alone would stay identical
// across a tier-only override or a provider correction while the numbers moved - which is the one thing a receipt
// must never do.
function rateSignature(m) {
  // Both spellings are read here for the same reason the pricer reads both: the published table writes snake_case,
  // a hand-written override may not, and either one moves the cost of every turn above the threshold.
  const tiers = (m.tiers || []).map((t) => `${t.tier?.type || '?'}@${t.tier?.size ?? '?'}>${t.input}/${t.output}/${t.cache_read ?? t.cacheRead}/${t.cache_write ?? t.cacheWrite}`).join(';');
  return `${m.provider || '?'}:${m.input}/${m.output}/${m.cacheRead}/${m.cacheWrite}/${m.cacheWrite1h ?? ''}[${tiers}]`;
}
function fingerprint(prices) {
  const ids = Object.keys(prices.models).sort();
  const body = ids.map((id) => `${id}:${rateSignature(prices.models[id])}`).join('|');
  return {
    engine: { costCacheVersion: CACHE_VERSION, timePrecision: TIME_PRECISION, bucketEdges: BUCKET_EDGES },
    prices: { models: ids.length, sources: prices.sources, sha256: require('crypto').createHash('sha256').update(body).digest('hex').slice(0, 16) },
  };
}

module.exports = { sessionCost, transcriptOf, fetchPrices, underPath, identify, sessionFiles, resolveRoots, fingerprint, rateSignature, TIME_PRECISION, CACHE_VERSION, PRICE_SOURCE, PRICE_CHECK, scanAll, scanFile, summarize, loadPrices, rateFor, normalizeModel, costOf, flatten, bucketOf, BUCKET_EDGES, EMPTY, EMPTY_BUCKETS, CACHE, PRICES_LIVE, PRICES_OVERRIDE, PRICES_SHIPPED, PROJECTS };
