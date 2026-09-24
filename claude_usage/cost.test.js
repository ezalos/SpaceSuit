// ABOUTME: Tests for cost.js: transcript scanning (incremental, deduped), prompt-size bucketing, and pricing.
// ABOUTME: Every fixture is a synthetic transcript in a temp HOME; no real transcript and no network are touched.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

const MOD = path.join(__dirname, 'cost.js');
function fresh() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'cost-'));
  process.env.HOME = home;
  delete process.env.CLAUDE_CONFIG_DIR;
  delete require.cache[MOD];
  const m = require(MOD);
  fs.mkdirSync(m.PROJECTS, { recursive: true });
  return { home, m, cache: path.join(home, 'cache.json') };
}
// Sessions are keyed by FILE PATH (a repo move puts one session id under two project directories), so tests look a
// session up by its id rather than indexing the map.
const S = (sessions, id) => Object.values(sessions).find((x) => x.id === id);
const usage = ({ i = 0, o = 0, cr = 0, cw = 0, think = 0 } = {}) => ({
  input_tokens: i, output_tokens: o, cache_read_input_tokens: cr, cache_creation_input_tokens: cw,
  output_tokens_details: { thinking_tokens: think },
});
let idSeq = 0;
const turn = (model, u, { ts = '2026-09-17T10:00:00.000Z', cwd = '/w/repo', id = null } = {}) =>
  JSON.stringify({ type: 'assistant', timestamp: ts, cwd, message: { id: id || `msg-${++idSeq}`, model, usage: usage(u) } });
function seed(t, name, lines, { dir = 'proj' } = {}) {
  const d = path.join(t.m.PROJECTS, dir);
  fs.mkdirSync(d, { recursive: true });
  const f = path.join(d, name);
  fs.writeFileSync(f, lines.join('\n') + '\n');
  return f;
}
// A minimal table, shaped like the shipped one: every entry carries a provider, and a tier states its cache rates in
// the snake_case the published source uses (never both casings at once — that would hide a naming defect).
const PRICES = {
  models: {
    'claude-opus-5': { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25, provider: 'anthropic', layer: 'test' },
    'gpt-6-astra': { input: 10, output: 50, cacheRead: 1, cacheWrite: 12.5, provider: 'openai', layer: 'test', tiers: [{ input: 20, output: 75, cache_read: 2, cache_write: 25, tier: { type: 'context', size: 272000 } }] },
    'grok-4.5': { input: 2, output: 6, cacheRead: 0.3, cacheWrite: null, provider: 'xai', layer: 'test', tiers: [{ input: 4, output: 12, cache_read: 0.6, tier: { type: 'context', size: 200000 } }] },
  },
  sources: { test: { count: 3, at: '2026-09-17' } },
};

test('scan: a replayed message is counted once', () => {
  const t = fresh();
  // Claude Code rewrites earlier turns into the transcript, so the same assistant message appears repeatedly with
  // IDENTICAL usage. Measured on the real corpus: 973 of 1967 billed lines in one file were such replays. Counting
  // lines instead of messages would have roughly doubled the bill.
  seed(t, 'a.jsonl', [
    turn('claude-opus-5', { i: 100, o: 10 }, { id: 'same' }),
    turn('claude-opus-5', { i: 100, o: 10 }, { id: 'same' }),
    turn('claude-opus-5', { i: 100, o: 10 }, { id: 'same' }),
    turn('claude-opus-5', { i: 7, o: 1 }, { id: 'other' }),
  ]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const agg = t.m.flatten(S(sessions, 'a').models['claude-opus-5']);
  assert.equal(agg.turns, 2);
  assert.equal(agg.in, 107);
});

test('scan: a refusal turn is not a billed turn', () => {
  const t = fresh();
  seed(t, 'a.jsonl', [turn('<synthetic>', { i: 999999 }), turn('claude-opus-5', { i: 10, o: 1 })]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  assert.deepEqual(Object.keys(S(sessions, 'a').models), ['claude-opus-5']);
});

test('scan: only the appended bytes are re-read, and totals still add up', () => {
  const t = fresh();
  const f = seed(t, 'a.jsonl', [turn('claude-opus-5', { i: 10, o: 1 }, { id: 'one' })]);
  const first = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(first.read, 1);
  assert.equal(t.m.flatten(S(first.sessions, 'a').models['claude-opus-5']).turns, 1);

  // Unchanged file: not re-read at all.
  const second = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(second.read, 0);
  assert.equal(second.skipped, 1);
  assert.equal(t.m.flatten(S(second.sessions, 'a').models['claude-opus-5']).turns, 1, 'a skipped file still reports its cached total');

  // Appended: the new turn is added to the old total, not counted from scratch.
  fs.appendFileSync(f, turn('claude-opus-5', { i: 5, o: 2 }, { id: 'two' }) + '\n');
  const third = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(third.read, 1);
  const agg = t.m.flatten(S(third.sessions, 'a').models['claude-opus-5']);
  assert.equal(agg.turns, 2);
  assert.equal(agg.in, 15);
});

test('scan: a half-written last line is left for next time', () => {
  const t = fresh();
  const f = seed(t, 'a.jsonl', [turn('claude-opus-5', { i: 10, o: 1 }, { id: 'one' })]);
  fs.appendFileSync(f, '{"type":"assistant","message":{"id":"partial","model":"claude-opus-5","usage":{"input_toke');
  const a = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(t.m.flatten(S(a.sessions, 'a').models['claude-opus-5']).turns, 1, 'the torn line is not parsed');
  // Once the writer finishes the line, it is picked up — and not double-counted.
  fs.writeFileSync(f, [turn('claude-opus-5', { i: 10, o: 1 }, { id: 'one' }), turn('claude-opus-5', { i: 3, o: 1 }, { id: 'two' })].join('\n') + '\n');
  const b = t.m.scanAll({ cacheFile: t.cache });
  const agg = t.m.flatten(S(b.sessions, 'a').models['claude-opus-5']);
  assert.equal(agg.turns, 2);
  assert.equal(agg.in, 13);
});

test('scan: a rewritten (shorter) transcript is re-read from the start', () => {
  const t = fresh();
  const f = seed(t, 'a.jsonl', Array.from({ length: 20 }, (_, k) => turn('claude-opus-5', { i: 100, o: 10 }, { id: `m${k}` })));
  t.m.scanAll({ cacheFile: t.cache });
  fs.writeFileSync(f, turn('claude-opus-5', { i: 1, o: 1 }, { id: 'fresh' }) + '\n'); // compacted away
  const after = t.m.scanAll({ cacheFile: t.cache });
  const agg = t.m.flatten(S(after.sessions, 'a').models['claude-opus-5']);
  assert.equal(agg.turns, 1, 'the stale offset must not make us skip the new content');
  assert.equal(agg.in, 1);
});

test('bucketing: a turn lands by its whole prompt, cache reads included', () => {
  const t = fresh();
  assert.equal(t.m.bucketOf(0), 0);
  assert.equal(t.m.bucketOf(200000), 0, 'the edge itself is still the lower bucket');
  assert.equal(t.m.bucketOf(200001), 1);
  assert.equal(t.m.bucketOf(272000), 1);
  assert.equal(t.m.bucketOf(272001), 2);
  // 300k of cache reads is a 300k prompt, even though input_tokens is tiny.
  seed(t, 'a.jsonl', [turn('gpt-6-astra', { i: 10, o: 5, cr: 300000 })]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const bs = S(sessions, 'a').models['gpt-6-astra'];
  assert.equal(bs[0].turns, 0);
  assert.equal(bs[2].turns, 1, 'priced at the long-context tier, not the standard one');
});

test('pricing: the long-context tier applies only above the threshold', () => {
  const t = fresh();
  const rate = PRICES.models['gpt-6-astra'];
  const one = (bucket, agg) => { const bs = t.m.EMPTY_BUCKETS(); Object.assign(bs[bucket], agg, { turns: 1 }); return bs; };
  // 1M output tokens under the threshold: $50. Over it: $75.
  assert.equal(Math.round(t.m.costOf(one(0, { out: 1e6 }), rate).usd), 50);
  assert.equal(Math.round(t.m.costOf(one(2, { out: 1e6 }), rate).usd), 75);
  // Bucket 1 is 200k-272k: below Astra's 272k threshold, so standard rate.
  assert.equal(Math.round(t.m.costOf(one(1, { out: 1e6 }), rate).usd), 50);
  // Grok's threshold is 200k, so the same bucket IS tiered for it.
  assert.equal(Math.round(t.m.costOf(one(1, { out: 1e6 }), PRICES.models['grok-4.5']).usd), 12);
  assert.equal(t.m.costOf(one(2, { out: 10 }), rate).tieredTurns, 1);
});

test('pricing: a missing cache rate makes the total a floor, never a guess', () => {
  const t = fresh();
  const bs = t.m.EMPTY_BUCKETS();
  Object.assign(bs[0], { turns: 1, in: 1e6, cacheWrite: 1e6, cw5m: 1e6 });
  const c = t.m.costOf(bs, PRICES.models['grok-4.5']); // grok has no published cache-write rate
  assert.equal(c.partial, true, 'flagged, because pricing a cache write as input would be the biggest error in the report');
  assert.equal(Math.round(c.usd), 2, 'the input is still priced; only the unrateable part is left out');
});

test('model ids: gateway prefix, picker suffix, dated snapshots and -fast all resolve', () => {
  const t = fresh();
  const r = (id) => t.m.rateFor(id, PRICES);
  assert.equal(r('claude-gpt-6-astra[1m]').input, 10, 'gateway prefix + picker suffix');
  assert.equal(r('gpt-6-astra').input, 10);
  assert.equal(r('claude-gpt-6-astra-fast[1m]').input, 10, 'the fast variant has no separate published rate');
  assert.equal(r('claude-opus-5').input, 5);
  assert.equal(r('claude-grok-4.5[1m]').input, 2);
  assert.equal(r('something-unheard-of'), null, 'an unknown model stays unpriced rather than borrowing a rate');
});

test('summarize: groups, windows and unpriced models', () => {
  const t = fresh();
  seed(t, 'a.jsonl', [
    turn('claude-opus-5', { o: 1e6 }, { ts: '2026-09-16T10:00:00.000Z', cwd: '/w/one' }),
    turn('claude-opus-5', { o: 1e6 }, { ts: '2026-09-17T10:00:00.000Z', cwd: '/w/one' }),
  ]);
  seed(t, 'b.jsonl', [turn('mystery-model', { o: 1e6 }, { ts: '2026-09-17T10:00:00.000Z', cwd: '/w/two' })], { dir: 'proj2' });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });

  const byModel = t.m.summarize({ sessions, prices: PRICES, group: 'model' });
  assert.equal(Math.round(byModel.rows.find((r) => r.key === 'claude-opus-5').usd), 50);
  assert.deepEqual(byModel.unpriced, ['mystery-model']);
  assert.equal(byModel.rows.find((r) => r.key === 'mystery-model').partial, true);

  const oneDay = t.m.summarize({ sessions, prices: PRICES, group: 'model', since: '2026-09-17' });
  assert.equal(Math.round(oneDay.rows.find((r) => r.key === 'claude-opus-5').usd), 25, 'the window halves it');

  const byRepo = t.m.summarize({ sessions, prices: PRICES, group: 'repo' });
  assert.deepEqual(byRepo.rows.map((r) => r.key).sort(), ['/w/one', '/w/two']);
  const filtered = t.m.summarize({ sessions, prices: PRICES, group: 'session', cwdFilter: '/w/one' });
  assert.deepEqual(filtered.rows.map((r) => r.key), ['a']);
});

test('prices: a refresh keeps absent rates absent and reports a disagreement', async () => {
  const t = fresh();
  const primary = { anthropic: { models: { 'claude-opus-5': { cost: { input: 5, output: 25, cache_read: 0.5, cache_write: 6.25 } } } },
    openai: { models: { 'gpt-5.6-sol': { cost: { input: 4, output: 20 } }, 'no-price': { cost: {} } } },
    xai: { models: {} } };
  const check = { 'gpt-5.6-sol': { input_cost_per_token: 0.000002, output_cost_per_token: 0.00001 } }; // the batch rate
  const fetchImpl = async (url) => ({ ok: true, status: 200, json: async () => (url.includes('models.dev') ? primary : check) });

  const { doc, disagreements } = await t.m.fetchPrices({ fetchImpl });
  assert.equal(doc.models['claude-opus-5'].input, 5);
  assert.equal(doc.models['gpt-5.6-sol'].cacheRead, null, 'absent stays absent; it must not default to 0');
  assert.equal('no-price' in doc.models, false, 'a model with no published rate is not stored at all');
  assert.equal(disagreements.length, 1);
  assert.equal(disagreements[0].id, 'gpt-5.6-sol');
  assert.deepEqual(disagreements[0].primary, [4, 20]);
  assert.deepEqual(disagreements[0].check, [2, 10], 'this is exactly the batch-vs-standard mixup that disqualified one source');
});

test('prices: an empty or failed fetch never overwrites the table', async () => {
  const t = fresh();
  const empty = async () => ({ ok: true, status: 200, json: async () => ({ anthropic: { models: {} }, openai: { models: {} }, xai: { models: {} } }) });
  await assert.rejects(() => t.m.fetchPrices({ fetchImpl: empty }), /no usable models/);
  const dead = async () => ({ ok: false, status: 503, json: async () => ({}) });
  await assert.rejects(() => t.m.fetchPrices({ fetchImpl: dead }), /503/);
});

// ---------- regressions from a peer audit, 2026-09-17 ----------
// All four reproduced before the fixes; each test here failed first.

test('an appended replay of an already-counted message is not billed twice', () => {
  const t = fresh();
  // The dedupe set used to be discarded once a scan reached EOF, on the theory that a finished session cannot gain a
  // replay. Reaching EOF does not finish a session: Claude Code appends replays of earlier turns later.
  const f = seed(t, 'a.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'A' })]);
  t.m.scanAll({ cacheFile: t.cache });
  fs.appendFileSync(f, [turn('claude-opus-5', { i: 100 }, { id: 'A' }), turn('claude-opus-5', { i: 7 }, { id: 'B' })].join('\n') + '\n');
  const r = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(t.m.flatten(S(r.sessions, 'a').models['claude-opus-5']).in, 107, '207 would mean the replay was billed again');
});

test('a 1-hour cache write is priced at the 1-hour rate, and flagged when that rate is unknowable', () => {
  const t = fresh();
  seed(t, 'a.jsonl', [JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/repo',
    message: { id: 'A', model: 'claude-opus-5', usage: { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 1e6,
      cache_creation: { ephemeral_1h_input_tokens: 1e6, ephemeral_5m_input_tokens: 0 } } } })]);
  const r = t.m.scanAll({ cacheFile: t.cache });
  const buckets = S(r.sessions, 'a').models['claude-opus-5'];
  // Anthropic: 5m write 1.25x input, 1h write 2x. models.dev publishes only the 5m rate, so 1h is derived.
  const known = t.m.costOf(buckets, { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25, provider: 'anthropic' });
  assert.equal(Math.round(known.usd), 10);
  assert.equal(known.partial, false);
  const unknown = t.m.costOf(buckets, { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25 });
  assert.equal(unknown.partial, true, 'a silent fallback to the 5m rate understates a 1h write by 60%');
  assert.equal(Math.round(unknown.usd), 0, 'the unrateable part is left out rather than approximated');
});

test('a write whose TTL cannot be established is a floor, not an assumed 5-minute', () => {
  const t = fresh();
  // Absence of the split cannot prove 5-minute: the flat field predates the split, but a harness that dropped the
  // metadata looks identical, and the two rates differ by 60%. Measured over 600 sampled transcripts the split is
  // always present, so this path is expected to stay unused - it exists so that if that changes, the report says so.
  seed(t, 'a.jsonl', [JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/repo',
    message: { id: 'A', model: 'claude-opus-5', usage: { cache_creation_input_tokens: 1e6 } } })]);
  const r = t.m.scanAll({ cacheFile: t.cache });
  const bs = S(r.sessions, 'a').models['claude-opus-5'];
  assert.equal(t.m.flatten(bs).cwUnknownTtl, 1e6);
  const c = t.m.costOf(bs, { input: 5, cacheWrite: 6.25, provider: 'anthropic' });
  assert.equal(c.partial, true, 'reported as incomplete rather than priced at the cheaper rate');
  assert.equal(Math.round(c.usd), 0);
});

test('an explicit 5-minute write is priced at the 5-minute rate', () => {
  const t = fresh();
  seed(t, 'a.jsonl', [JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/repo',
    message: { id: 'A', model: 'claude-opus-5', usage: { cache_creation_input_tokens: 1e6, cache_creation: { ephemeral_1h_input_tokens: 0, ephemeral_5m_input_tokens: 1e6 } } } })]);
  const r = t.m.scanAll({ cacheFile: t.cache });
  const c = t.m.costOf(S(r.sessions, 'a').models['claude-opus-5'], { input: 5, cacheWrite: 6.25, provider: 'anthropic' });
  assert.equal(Math.round(c.usd * 100) / 100, 6.25);
  assert.equal(c.partial, false);
});

test('discovery is recursive: subagent transcripts are found at any depth', () => {
  const t = fresh();
  // Subagents live below the session that spawned them, and deeper still under subagents/workflows/. Walking one
  // level omitted 49,320 billed turns and 7.9G cache-read tokens from every total on the real corpus.
  seed(t, 'root.jsonl', [turn('claude-opus-5', { i: 10 }, { id: 'p1' })], { dir: 'proj' });
  seed(t, 'agent-a.jsonl', [turn('claude-sonnet-5', { i: 20 }, { id: 'a1' })], { dir: path.join('proj', 'root', 'subagents') });
  seed(t, 'agent-b.jsonl', [turn('claude-sonnet-5', { i: 30 }, { id: 'b1' })], { dir: path.join('proj', 'root', 'subagents', 'workflows', 'wf1') });
  const r = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(Object.keys(r.sessions).length, 3, 'all three depths discovered');
  const sum = t.m.summarize({ sessions: r.sessions, prices: PRICES, group: 'model' });
  assert.equal(sum.rows.find((x) => x.key === 'claude-sonnet-5').in, 50, 'both subagents counted');
  // Path nesting is the filesystem's statement of ancestry, exposed but not claimed as verified lineage.
  assert.equal(S(r.sessions, 'root').kind, 'session');
  assert.equal(S(r.sessions, 'agent-a').kind, 'agent');
  assert.equal(S(r.sessions, 'agent-a').parent, 'root');
  assert.equal(S(r.sessions, 'agent-b').parent, 'root');
});

test('an id appearing in two files is billed once, and the owner keeps it across rescans', () => {
  const t = fresh();
  // 2 of 49,289 subagent ids also appear in a parent transcript, so recursive discovery without this bills them twice.
  seed(t, 'root.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED' })], { dir: 'proj' });
  const child = seed(t, 'agent-a.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED' }), turn('claude-opus-5', { i: 5 }, { id: 'OWN' })], { dir: path.join('proj', 'root', 'subagents') });
  const r = t.m.scanAll({ cacheFile: t.cache });
  const sum = t.m.summarize({ sessions: r.sessions, prices: PRICES, group: 'model' });
  assert.equal(sum.rows[0].in, 105, '205 would mean the shared id was billed in both files');
  // Appending to the non-owner must not let it re-claim the shared id.
  fs.appendFileSync(child, turn('claude-opus-5', { i: 1 }, { id: 'LATER' }) + '\n');
  const again = t.m.summarize({ sessions: t.m.scanAll({ cacheFile: t.cache }).sessions, prices: PRICES, group: 'model' });
  assert.equal(again.rows[0].in, 106);
});

test('one session id in two project directories: neither displaces the other', () => {
  const t = fresh();
  // A repo move leaves the same session id under both the old and the new project directory. Two such pairs exist on
  // this machine; keying the report by session id let whichever was read last drop the other's cost.
  seed(t, 'twin.jsonl', [turn('claude-opus-5', { i: 1000 }, { id: 'old-A', cwd: '/w/old' })], { dir: 'old-path' });
  seed(t, 'twin.jsonl', [turn('claude-opus-5', { i: 1000 }, { id: 'new-A', cwd: '/w/new' })], { dir: 'new-path' });
  const r = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(Object.keys(r.sessions).length, 2, 'keyed by path, not by session id');
  const all = t.m.summarize({ sessions: r.sessions, prices: PRICES, group: 'model' });
  assert.equal(all.rows[0].in, 2000);
});

test('--repo respects path boundaries and every cwd a session reported', () => {
  const t = fresh();
  seed(t, 'p.jsonl', [turn('claude-opus-5', { i: 500 }, { cwd: '/w/Alakazam' })], { dir: 'p' });
  seed(t, 'c.jsonl', [turn('claude-opus-5', { i: 500 }, { cwd: '/w/Alakazam/web_wm_onnx' })], { dir: 'c' });
  seed(t, 'x.jsonl', [turn('claude-opus-5', { i: 500 }, { cwd: '/w/AlakazamOther' })], { dir: 'x' });
  // A session whose repo moved mid-session reports the old cwd first and the new one later.
  seed(t, 'm.jsonl', [turn('claude-opus-5', { i: 500 }, { cwd: '/w/elsewhere', id: 'm1' }), turn('claude-opus-5', { i: 500 }, { cwd: '/w/Alakazam/mira', id: 'm2' })], { dir: 'm' });
  const r = t.m.scanAll({ cacheFile: t.cache });
  const got = t.m.summarize({ sessions: r.sessions, prices: PRICES, group: 'session', cwdFilter: '/w/Alakazam' });
  assert.deepEqual(got.rows.map((x) => x.key).sort(), ['c', 'm', 'p'], 'children and moved sessions in; a same-prefix sibling out');
  assert.equal(t.m.underPath('/w/AlakazamOther', '/w/Alakazam'), false, 'prefix matching must respect the separator');
  assert.equal(t.m.underPath('/w/Alakazam', '/w/Alakazam'), true);
});

test('--repo accepts the trailing slash a shell completion adds', () => {
  const t = fresh();
  // Tab-completing a directory hands over a trailing slash, and the root dir then failed its own filter: the
  // sessions that sat exactly there dropped out while their subdirectories stayed.
  assert.equal(t.m.underPath('/w/A/mira', '/w/A/mira/'), true);
  assert.equal(t.m.underPath('/w/A/mira/sub', '/w/A/mira///'), true);
  assert.equal(t.m.underPath('/w/A/miraOther', '/w/A/mira/'), false, 'and the boundary still holds');
  seed(t, 'p.jsonl', [turn('claude-opus-5', { i: 500 }, { cwd: '/w/A/mira' })], { dir: 'p' });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const got = t.m.summarize({ sessions, prices: PRICES, group: 'session', cwdFilter: '/w/A/mira/' });
  assert.deepEqual(got.rows.map((x) => x.key), ['p']);
});

test('roots: an explicit set selects those sessions and their descendants, and reports what matched nothing', () => {
  const t = fresh();
  // A campaign root is a session, not a directory. Descendants come from path nesting at any depth.
  seed(t, 'rootA.jsonl', [turn('claude-opus-5', { i: 10 }, { id: 'A1' })], { dir: 'proj' });
  seed(t, 'agent-1.jsonl', [turn('claude-opus-5', { i: 20 }, { id: 'A2' })], { dir: path.join('proj', 'rootA', 'subagents') });
  seed(t, 'agent-2.jsonl', [turn('claude-opus-5', { i: 40 }, { id: 'A3' })], { dir: path.join('proj', 'rootA', 'subagents', 'workflows', 'w') });
  seed(t, 'rootB.jsonl', [turn('claude-opus-5', { i: 80 }, { id: 'B1' })], { dir: 'proj' });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });

  const only = t.m.summarize({ sessions, prices: PRICES, group: 'model', rootFilter: ['rootA'] });
  assert.equal(only.rows[0].in, 70, 'root plus both nested descendants, and nothing from rootB');
  assert.deepEqual(only.rootScope.resolved, ['rootA']);
  assert.deepEqual(only.rootScope.unknown, []);

  const both = t.m.summarize({ sessions, prices: PRICES, group: 'model', rootFilter: ['rootA', 'rootB'] });
  assert.equal(both.rows[0].in, 150, 'two sibling roots union, no double counting');

  const gap = t.m.summarize({ sessions, prices: PRICES, group: 'model', rootFilter: ['rootA', 'no-such-root'] });
  assert.deepEqual(gap.rootScope.unknown, ['no-such-root'], 'a root that matched nothing is reported, not dropped');
  assert.equal(gap.rows[0].in, 70);
});

// A selection answers "what did these sources cost", but a message shared with a source OUTSIDE the selection is
// counted under that outside source — correctly, and only once corpus-wide. The subtotal is then limited by that
// allocation, and the report has to say so rather than let `partial: false` assert a completeness it cannot see.
// This is source-allocation coverage, never a claim about which agent produced the turn, and never a second total.
const coverage = (t, sessions, opts) => t.m.summarize({ sessions, prices: PRICES, group: 'session', ...opts }).allocationCoverage;

test('scope coverage: a selected root whose shared message is allocated to an outside source says so', () => {
  const t = fresh();
  // `outside` sorts before `rootB`, so it owns SHARED corpus-wide. Selecting only rootB gives a priced-complete
  // row that is nonetheless missing that message.
  seed(t, 'outside.jsonl', [turn('claude-opus-5', { i: 2e6 }, { id: 'SHARED' })], { dir: 'proj' });
  seed(t, 'rootB.jsonl', [turn('claude-opus-5', { i: 10 }, { id: 'OWN' }), turn('claude-opus-5', { i: 2e6 }, { id: 'SHARED' })], { dir: 'proj' });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });

  const scoped = t.m.summarize({ sessions, prices: PRICES, group: 'session', rootFilter: ['rootB'] });
  assert.equal(scoped.rows[0].partial, false, 'pricing really is complete for what the scope was allocated');
  assert.equal(scoped.allocationCoverage.complete, false, 'but the scope itself is not fully represented');
  assert.equal(scoped.allocationCoverage.allocatedElsewhereIds, 1);
  assert.equal(typeof scoped.allocationCoverage.meaning, 'string');
  assert.ok(!JSON.stringify(scoped.allocationCoverage).includes('SHARED'), 'no raw message id leaves the engine');
});

test('scope coverage: complete when the shared message is owned inside the selection', () => {
  const t = fresh();
  // Same corpus shape, but the owner is selected too, so nothing is missing from the answer.
  seed(t, 'rootA.jsonl', [turn('claude-opus-5', { i: 2e6 }, { id: 'SHARED' })], { dir: 'proj' });
  seed(t, 'agent-1.jsonl', [turn('claude-opus-5', { i: 2e6 }, { id: 'SHARED' }), turn('claude-opus-5', { i: 10 }, { id: 'OWN' })], { dir: path.join('proj', 'rootA', 'subagents') });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  assert.deepEqual(coverage(t, sessions, { rootFilter: ['rootA'] }), { complete: true, allocatedElsewhereIds: 0, meaning: coverage(t, sessions, { rootFilter: ['rootA'] }).meaning });
  assert.equal(coverage(t, sessions, {}).complete, true, 'the whole corpus is always fully represented');
});

test('scope coverage: one shared id held by two selected sources counts once', () => {
  const t = fresh();
  seed(t, 'outside.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED' })], { dir: 'proj' });
  seed(t, 'rootB.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED' })], { dir: 'proj' });
  seed(t, 'agent-1.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED' })], { dir: path.join('proj', 'rootB', 'subagents') });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const c = coverage(t, sessions, { rootFilter: ['rootB'] });
  assert.equal(c.allocatedElsewhereIds, 1, 'two selected sources hold the same outside-owned id: one gap, not two');
  assert.equal(c.complete, false);
});

test('scope coverage: a repo selection is measured the same way', () => {
  const t = fresh();
  seed(t, 'outside.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED', cwd: '/w/other' })], { dir: 'p1' });
  seed(t, 'inside.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED', cwd: '/w/repo' })], { dir: 'p2' });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(coverage(t, sessions, { cwdFilter: '/w/repo' }).allocatedElsewhereIds, 1);
  assert.equal(coverage(t, sessions, { cwdFilter: '/w/other' }).complete, true, 'the owner is inside this one');
});

test('scope coverage follows the owner across cold, warm and append', () => {
  const t = fresh();
  // rootB owns SHARED while it is the only copy; once an earlier-sorting outsider appears the owner moves, and the
  // coverage counter has to move with it — identically warm and cold.
  seed(t, 'rootB.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED' })], { dir: 'proj' });
  const first = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(coverage(t, first.sessions, { rootFilter: ['rootB'] }).complete, true);

  const out = seed(t, 'outside.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'SHARED' })], { dir: 'proj' });
  const warm = t.m.scanAll({ cacheFile: t.cache });
  const cold = coldRun(t, 'cold');
  assert.equal(coverage(t, warm.sessions, { rootFilter: ['rootB'] }).allocatedElsewhereIds, 1);
  assert.deepEqual(coverage(t, warm.sessions, { rootFilter: ['rootB'] }), coverage(t, cold.sessions, { rootFilter: ['rootB'] }));

  // The outsider is compacted away: rootB is the only copy again, so the scope is whole again.
  fs.writeFileSync(out, turn('claude-opus-5', { i: 1 }, { id: 'OTHER' }) + '\n');
  const after = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(coverage(t, after.sessions, { rootFilter: ['rootB'] }).complete, true);
  assert.deepEqual(coverage(t, after.sessions, { rootFilter: ['rootB'] }), coverage(t, coldRun(t, 'cold2').sessions, { rootFilter: ['rootB'] }));
});

test('rows keep the token classes distinct, and the fingerprint pins engine and price table', () => {
  const t = fresh();
  seed(t, 'a.jsonl', [JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/r',
    message: { id: 'A', model: 'claude-opus-5', usage: { input_tokens: 1, output_tokens: 2, cache_read_input_tokens: 3,
      cache_creation_input_tokens: 10, cache_creation: { ephemeral_1h_input_tokens: 7, ephemeral_5m_input_tokens: 3 } } } })]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const r = t.m.summarize({ sessions, prices: PRICES, group: 'model' }).rows[0];
  assert.equal(r.cacheRead, 3);
  assert.equal(r.cw5m, 3);
  assert.equal(r.cw1h, 7);
  assert.equal(r.cwUnknownTtl, 0, 'the classes must not be collapsed: their rates differ by up to 12.5x');

  const fp = t.m.fingerprint(PRICES);
  assert.equal(fp.engine.timePrecision, 'utc-day', 'declared, so a consumer cannot assume sub-day attribution');
  assert.equal(fp.engine.costCacheVersion, t.m.CACHE_VERSION);
  assert.equal(fp.prices.models, 3);
  assert.match(fp.prices.sha256, /^[0-9a-f]{16}$/);
  // The same table fingerprints the same; a changed rate does not.
  assert.equal(t.m.fingerprint(PRICES).prices.sha256, fp.prices.sha256);
  const bumped = { ...PRICES, models: { ...PRICES.models, 'claude-opus-5': { ...PRICES.models['claude-opus-5'], input: 6 } } };
  assert.notEqual(t.m.fingerprint(bumped).prices.sha256, fp.prices.sha256);
});

// ---------- regressions from a second peer audit, 2026-09-17 (B1-B7) ----------
// Each reproduced against the committed engine before its fix.

// The status line is the fast path: one session, no corpus cache. It has to obey the same law as the corpus —
// the same final bytes cost the same whether they were read in one pass or appended over a day.
test('sessionCost: the same final bytes cost the same cold, warm and incrementally', () => {
  const t = fresh();
  const sid = 'sess-replay-1';
  const f = seed(t, `${sid}.jsonl`, [turn('claude-opus-5', { i: 1e6 }, { id: 'A' })]);
  const cold = t.m.sessionCost(sid, PRICES);
  assert.equal(Math.round(cold.usd), 5);
  // Claude Code appends replays of earlier turns; the dedupe set used to be discarded once a scan reached EOF.
  fs.appendFileSync(f, [turn('claude-opus-5', { i: 1e6 }, { id: 'A' }), turn('claude-opus-5', { i: 1e6 }, { id: 'B' })].join('\n') + '\n');
  const incremental = t.m.sessionCost(sid, PRICES);
  const warm = t.m.sessionCost(sid, PRICES); // unchanged bytes: served from the cache
  assert.equal(Math.round(incremental.usd), 10, '15 would mean the replayed turn was billed a second time');
  assert.equal(warm.usd, incremental.usd);
});

// The per-session cache is written by whatever build ran last. An entry from an older representation describes
// buckets that no longer mean the same thing, so it has to be rejected and rebuilt rather than merged into.
test('sessionCost: a cache from an older representation is rebuilt, not merged', () => {
  const t = fresh();
  const sid = 'sess-legacy-1';
  const f = seed(t, `${sid}.jsonl`, [JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/r',
    message: { id: 'A', model: 'claude-opus-5', usage: { cache_creation_input_tokens: 1e6, cache_creation: { ephemeral_1h_input_tokens: 1e6, ephemeral_5m_input_tokens: 0 } } } })]);
  t.m.sessionCost(sid, PRICES);
  const cf = path.join(t.home, '.claude', 'claude-usage', 'session-cost', `${sid}.json`);
  // Exactly what a build that predates the TTL split left on disk: a flat cacheWrite and no cw5m/cw1h keys.
  const stale = JSON.parse(fs.readFileSync(cf, 'utf8'));
  delete stale.v;
  for (const bs of Object.values(stale.models)) for (const b of bs) { delete b.cw5m; delete b.cw1h; delete b.cwUnknownTtl; }
  fs.writeFileSync(cf, JSON.stringify(stale));
  fs.appendFileSync(f, turn('claude-opus-5', { i: 1 }, { id: 'B' }) + '\n');
  const after = t.m.sessionCost(sid, PRICES);
  // claude-opus-5 is Anthropic here, so a 1h write is 2x input: $10, plus one input token.
  assert.ok(Math.abs(after.usd - (10 + 5e-6)) < 1e-9, `a stale entry priced the 1h write at $0: got ${after.usd}`);
  assert.equal(after.partial, false);
});

test('a malformed cached entry is rebuilt from the bytes, on both cache paths', () => {
  const t = fresh();
  // Half a written cache, a hand edit, a disk that lied: whatever the cause, a bucket that is not a number used to
  // merge straight into the totals and come back as NaN (fast path) or a concatenated string (corpus path).
  const sid = 'sess-broken-1';
  const f = seed(t, `${sid}.jsonl`, [turn('claude-opus-5', { i: 1e6 }, { id: 'A' })]);
  t.m.sessionCost(sid, PRICES);
  const cf = path.join(t.home, '.claude', 'claude-usage', 'session-cost', `${sid}.json`);
  const e = JSON.parse(fs.readFileSync(cf, 'utf8'));
  for (const bs of Object.values(e.models)) for (const b of bs) b.in = 'not-a-number';
  fs.writeFileSync(cf, JSON.stringify(e));
  fs.appendFileSync(f, turn('claude-opus-5', { i: 1e6 }, { id: 'B' }) + '\n');
  assert.equal(Math.round(t.m.sessionCost(sid, PRICES).usd), 10);

  t.m.scanAll({ cacheFile: t.cache });
  const doc = JSON.parse(fs.readFileSync(t.cache, 'utf8'));
  for (const entry of Object.values(doc.files)) for (const bs of Object.values(entry.models)) for (const b of bs) b.in = 'not-a-number';
  fs.writeFileSync(t.cache, JSON.stringify(doc));
  const after = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(t.m.flatten(S(after.sessions, sid).models['claude-opus-5']).in, 2e6, 'the corrupt entry must be re-derived, not merged');
});

test('both cache paths and the fingerprint declare one and the same version', () => {
  const t = fresh();
  // The contract is that the two caches move together — not that the number is any particular value. Asserting the
  // constant rather than a literal is the point: a representation change bumps it, and this still holds.
  const sid = 'sess-version-1';
  seed(t, `${sid}.jsonl`, [turn('claude-opus-5', { i: 10 }, { id: 'A' })]);
  t.m.sessionCost(sid, PRICES);
  t.m.scanAll({ cacheFile: t.cache });
  const sess = JSON.parse(fs.readFileSync(path.join(t.home, '.claude', 'claude-usage', 'session-cost', `${sid}.json`), 'utf8'));
  const corpus = JSON.parse(fs.readFileSync(t.cache, 'utf8'));
  assert.equal(sess.v, corpus.version, 'one version for both paths, bumped together');
  assert.equal(t.m.fingerprint(PRICES).engine.costCacheVersion, corpus.version, 'and the receipt names the same one');
  assert.equal(corpus.version, t.m.CACHE_VERSION);
});

test('a cache written by an older engine version is discarded whole', () => {
  const t = fresh();
  seed(t, 'a.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'A' })]);
  t.m.scanAll({ cacheFile: t.cache });
  const doc = JSON.parse(fs.readFileSync(t.cache, 'utf8'));
  assert.equal(doc.version, t.m.CACHE_VERSION);
  // An entry from a version whose buckets meant something else: asserting an empty aggregate for a file that is not.
  for (const e of Object.values(doc.files)) e.models = {};
  fs.writeFileSync(t.cache, JSON.stringify({ ...doc, version: doc.version - 1 }));
  const after = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(t.m.flatten(S(after.sessions, 'a').models['claude-opus-5']).in, 100);
});

// The flat cache-write count and the per-TTL split are two statements about the same tokens. When they disagree the
// difference is information we do not have, and the one thing it must never become is an exact-looking price.
const write = (t, name, flat, split, opts) => seed(t, name, [JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z',
  cwd: '/w/r', message: { id: `w-${name}`, model: 'claude-opus-5', usage: { cache_creation_input_tokens: flat, ...(split ? { cache_creation: split } : {}) } } })], opts);

test('a write split that does not add up to the flat count leaves a flagged residue', () => {
  const t = fresh();
  write(t, 'a.jsonl', 1e6, { ephemeral_1h_input_tokens: 4e5, ephemeral_5m_input_tokens: 1e5 });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const row = t.m.summarize({ sessions, prices: PRICES, group: 'model' }).rows[0];
  assert.equal(row.cacheWrite, 1e6);
  assert.equal(row.cw1h, 4e5);
  assert.equal(row.cw5m, 1e5);
  assert.equal(row.cwUnknownTtl, 5e5, 'the 500k the split never classified must not vanish');
  assert.equal(row.partial, true, 'half the write is priced nowhere, so the row is a floor');
});

test('an absent split and an explicit zero split are both unknown, never assumed 5-minute', () => {
  const t = fresh();
  write(t, 'a.jsonl', 1e6, null);                                                    // no cache_creation key at all
  write(t, 'b.jsonl', 1e6, { ephemeral_1h_input_tokens: 0, ephemeral_5m_input_tokens: 0 }, { dir: 'p2' });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const rows = t.m.summarize({ sessions, prices: PRICES, group: 'session' }).rows;
  for (const key of ['a', 'b']) {
    const r = rows.find((x) => x.key === key);
    assert.equal(r.cwUnknownTtl, 1e6, `${key}: the write has no establishable TTL`);
    assert.equal(r.cw5m, 0);
    assert.equal(r.usd, 0);
    assert.equal(r.partial, true);
  }
});

test('a split claiming more tokens than the flat count is observed but never billed', () => {
  const t = fresh();
  // The two statements cannot both be true, and there is no honest way to choose between them: billing the split
  // charged 2M tokens where the transcript's own flat count says 1M were written, and then called the result a
  // FLOOR — a 2x OVER-estimate presented as an under-estimate. The turn's counters are kept as observations; its
  // money is withheld entirely.
  write(t, 'a.jsonl', 1e6, { ephemeral_1h_input_tokens: 2e6 });
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const row = t.m.summarize({ sessions, prices: PRICES, group: 'model' }).rows[0];
  assert.equal(row.cw1h, 2e6, 'the raw observation is retained for diagnosis');
  assert.equal(row.cacheWrite, 1e6, 'so is the flat count it contradicts');
  assert.equal(row.cwConflict, 1e6, 'the size of the disagreement is recorded, never billed');
  assert.equal(row.usd, 0, 'a contradictory turn contributes nothing to the known subtotal');
  assert.equal(row.withheldTurns, 1, 'and the report says one turn was left out of the money');
  assert.equal(row.partial, true);
  assert.deepEqual(row.partialReasons, ['count-conflict'], 'the cause is the counts, not a missing rate');
  // The invariant the whole fix exists to hold: write money can never exceed what the flat count supports.
  const dearest = PRICES.models['claude-opus-5'].input * 2; // an Anthropic 1-hour write
  assert.ok(row.usd <= (row.cacheWrite / 1e6) * dearest, 'priced writes must fit inside the flat count');
});

test('a contradictory turn does not stop a valid turn beside it being priced', () => {
  const t = fresh();
  // Both turns land in the same model, the same day and the same prompt-size bucket. Withholding the bad turn's
  // money must not withhold the good turn's, and the bad turn must not inflate the good turn's total either.
  seed(t, 'a.jsonl', [
    turn('claude-opus-5', { i: 1e6 }, { id: 'GOOD' }),
    JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/r',
      message: { id: 'BAD', model: 'claude-opus-5', usage: { cache_creation_input_tokens: 1e6, cache_creation: { ephemeral_1h_input_tokens: 3e6 } } } }),
  ]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const row = t.m.summarize({ sessions, prices: PRICES, group: 'model' }).rows[0];
  assert.equal(row.turns, 2, 'both turns are observed');
  assert.equal(row.withheldTurns, 1, 'one of them carries no money');
  assert.equal(row.usd, 5, 'exactly the valid turn: 1M input at $5/M, and not a cent of the contradictory one');
  assert.equal(row.cwConflict, 2e6);
  assert.equal(row.partial, true, 'the row is incomplete because a turn was left out, and says so');
});

test('a contradiction that would change the prompt-size tier makes no tier claim', () => {
  const t = fresh();
  // The flat count puts this turn's prompt at 100k (standard rate); the split would put it at 400k (tiered, double
  // rate). The turn cannot be priced at either without picking a winner, so it is priced at neither and counts
  // toward no tier.
  seed(t, 'a.jsonl', [JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/r',
    message: { id: 'AMBIG', model: 'gpt-6-astra', usage: { cache_creation_input_tokens: 100000, cache_creation: { ephemeral_1h_input_tokens: 400000 } } } })]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const row = t.m.summarize({ sessions, prices: PRICES, group: 'model' }).rows[0];
  assert.equal(row.usd, 0, 'neither the standard nor the tiered reading is asserted');
  assert.equal(row.tieredTurns, 0, 'an unpriced turn was not priced at a long-context tier');
  assert.equal(row.withheldTurns, 1);
  assert.equal(row.cwConflict, 300000);
  assert.deepEqual(row.partialReasons, ['count-conflict']);
});

test('the fast path withholds a contradictory turn too, cold and after an append', () => {
  const t = fresh();
  const sid = 'sess-conflict-1';
  const f = seed(t, `${sid}.jsonl`, [JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/r',
    message: { id: 'BAD', model: 'claude-opus-5', usage: { cache_creation_input_tokens: 1e6, cache_creation: { ephemeral_1h_input_tokens: 2e6 } } } })]);
  const cold = t.m.sessionCost(sid, PRICES);
  assert.equal(cold.usd, 0, 'the status line must not show $20 for a 1M write');
  assert.equal(cold.partial, true);
  assert.deepEqual(cold.reasons, ['count-conflict']);
  // A good turn appended afterwards is priced normally, and the bad one still contributes nothing.
  fs.appendFileSync(f, turn('claude-opus-5', { i: 1e6 }, { id: 'GOOD' }) + '\n');
  const after = t.m.sessionCost(sid, PRICES);
  assert.equal(after.usd, 5);
  assert.equal(after.partial, true);
});

test('a contradictory turn shared by two files is withheld once, warm and cold alike', () => {
  const t = fresh();
  // The conflicted turn is also the contested id, so ownership subtraction has to carry the withheld quantities
  // with it — otherwise surrendering the turn would hand its money back to the non-owner.
  const bad = JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/r',
    message: { id: 'SHARED', model: 'claude-opus-5', usage: { cache_creation_input_tokens: 1e6, cache_creation: { ephemeral_1h_input_tokens: 2e6 } } } });
  seed(t, 'b.jsonl', [bad, turn('claude-opus-5', { i: 1e6 }, { id: 'BOWN' })]);
  t.m.scanAll({ cacheFile: t.cache });
  seed(t, 'a.jsonl', [bad, turn('claude-opus-5', { i: 1e6 }, { id: 'AOWN' })]); // sorts earlier, takes SHARED
  const warm = t.m.scanAll({ cacheFile: t.cache });
  const cold = coldRun(t, 'cold');
  const rows = (r) => t.m.summarize({ sessions: r.sessions, prices: PRICES, group: 'session' }).rows;
  assert.deepEqual(rows(warm), rows(cold), 'warm and cold agree on the withheld turn and on the priced ones');
  const total = rows(warm).reduce((a, x) => a + x.usd, 0);
  assert.equal(total, 10, 'two valid 1M-input turns at $5; the shared contradictory turn is billed nowhere');
  assert.equal(rows(warm).reduce((a, x) => a + x.withheldTurns, 0), 1, 'and it is withheld once, not twice');
});

// Ownership is deterministic SOURCE ALLOCATION - which file a billed id is counted under - and nothing more; it is
// not proof of which agent produced the turn. What it must be is a function of the corpus as it stands, so that the
// same final files give the same answer whether they were read in one pass or accumulated over weeks.
const inOf = (sessions, m, id) => Object.values(sessions).filter((s) => s.id === id)
  .reduce((a, s) => a + Object.values(s.models).reduce((b, bs) => b + m.flatten(bs).in, 0), 0);
const totalIn = (sessions, m) => Object.values(sessions).reduce((a, s) =>
  a + Object.values(s.models).reduce((b, bs) => b + m.flatten(bs).in, 0), 0);
// A cold rebuild of the SAME corpus, for the warm result to be compared against.
const coldRun = (t, name) => t.m.scanAll({ cacheFile: path.join(t.home, `${name}.json`) });

test('a compacted owner hands its shared id back to the copy that still has it', () => {
  const t = fresh();
  // `a` owns X because it sorts first. Then `a` is compacted away - the `restarted` path the scanner already knows
  // about - while `b` still holds X. A sticky owner billed X nowhere at all.
  const a = seed(t, 'a.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X' }), turn('claude-opus-5', { i: 1 }, { id: 'A2' })]);
  seed(t, 'b.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X' })], { dir: 'proj2' });
  t.m.scanAll({ cacheFile: t.cache });
  fs.writeFileSync(a, turn('claude-opus-5', { i: 1 }, { id: 'A2' }) + '\n');
  const warm = t.m.scanAll({ cacheFile: t.cache });
  const cold = coldRun(t, 'cold');
  assert.equal(totalIn(warm.sessions, t.m), 101, 'X survives in b, so it is still billed once');
  assert.equal(totalIn(warm.sessions, t.m), totalIn(cold.sessions, t.m));
  assert.equal(inOf(warm.sessions, t.m, 'b'), inOf(cold.sessions, t.m, 'b'));
});

test('a deleted owner hands its shared id back to the copy that still has it', () => {
  const t = fresh();
  const a = seed(t, 'a.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X' })]);
  seed(t, 'b.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X' }), turn('claude-opus-5', { i: 5 }, { id: 'B2' })], { dir: 'proj2' });
  t.m.scanAll({ cacheFile: t.cache });
  fs.unlinkSync(a);
  const warm = t.m.scanAll({ cacheFile: t.cache });
  assert.equal(totalIn(warm.sessions, t.m), 105);
  assert.equal(totalIn(warm.sessions, t.m), totalIn(coldRun(t, 'cold').sessions, t.m));
});

test('a new earlier-sorting source allocates the same warm as it does cold', () => {
  const t = fresh();
  // `b` is scanned alone first and claims X. Then `a` appears, sorts earlier, and holds a copy of X plus a new id.
  // The grand total was already right; the PER-SESSION split was not, so a --by session or --roots number moved
  // across a rebuild while the machine total sat still.
  seed(t, 'b.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X' }), turn('claude-opus-5', { i: 20 }, { id: 'Y' })]);
  t.m.scanAll({ cacheFile: t.cache });
  seed(t, 'a.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X' }), turn('claude-opus-5', { i: 3 }, { id: 'Z' })]);
  const warm = t.m.scanAll({ cacheFile: t.cache });
  const cold = coldRun(t, 'cold');
  assert.equal(totalIn(warm.sessions, t.m), 123, 'X is still billed once');
  assert.equal(totalIn(cold.sessions, t.m), 123);
  assert.equal(inOf(warm.sessions, t.m, 'a'), inOf(cold.sessions, t.m, 'a'), 'a owns X in both');
  assert.equal(inOf(warm.sessions, t.m, 'b'), inOf(cold.sessions, t.m, 'b'));
  assert.equal(inOf(cold.sessions, t.m, 'a'), 103);
});

test('a day-grouped report allocates the same warm as it does cold', () => {
  const t = fresh();
  seed(t, 'b.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X', ts: '2026-09-16T10:00:00.000Z' })]);
  t.m.scanAll({ cacheFile: t.cache });
  seed(t, 'a.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X', ts: '2026-09-16T10:00:00.000Z' }), turn('claude-opus-5', { i: 7 }, { id: 'Z', ts: '2026-09-17T10:00:00.000Z' })]);
  const byDay = (r) => Object.fromEntries(t.m.summarize({ sessions: r.sessions, prices: PRICES, group: 'day' }).rows.map((x) => [x.key, x.in]));
  const warm = byDay(t.m.scanAll({ cacheFile: t.cache }));
  assert.deepEqual(warm, byDay(coldRun(t, 'cold')), 'the per-day split must not depend on ingestion order either');
  assert.deepEqual(warm, { '2026-09-16': 100, '2026-09-17': 7 });
});

test('two turns with identical usage but different ids are two billed turns', () => {
  const t = fresh();
  // Dedupe keys on message.id and nothing else. Two requests that happen to cost the same are not one request, and a
  // line with no id at all cannot be deduped - it is counted, which is the safe direction for a floor.
  seed(t, 'a.jsonl', [
    turn('claude-opus-5', { i: 100 }, { id: 'A' }),
    turn('claude-opus-5', { i: 100 }, { id: 'A' }),      // the same turn, replayed
    turn('claude-opus-5', { i: 100 }, { id: 'B' }),      // a different request, same shape
    JSON.stringify({ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/r', message: { model: 'claude-opus-5', usage: { input_tokens: 100 } } }),
  ]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const agg = t.m.flatten(S(sessions, 'a').models['claude-opus-5']);
  assert.equal(agg.turns, 3);
  assert.equal(agg.in, 300);
});

test('a corpus built in any order gives the same rows as one built in one pass', () => {
  const t = fresh();
  // The whole contract in one place: the same final bytes and the same price table give the same totals AND the same
  // per-session allocation, whether the corpus arrived over a week or was read from scratch just now.
  const b = seed(t, 'b.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X' }), turn('claude-opus-5', { i: 20 }, { id: 'Y' })]);
  t.m.scanAll({ cacheFile: t.cache });
  const a = seed(t, 'a.jsonl', [turn('gpt-6-astra', { i: 300000, o: 1e6 }, { id: 'BIG' }), turn('claude-opus-5', { i: 100 }, { id: 'X' })], { dir: 'proj' });
  t.m.scanAll({ cacheFile: t.cache });
  fs.appendFileSync(b, [turn('claude-opus-5', { i: 20 }, { id: 'Y' }), turn('claude-opus-5', { i: 3 }, { id: 'Z' })].join('\n') + '\n'); // a replay plus a new turn
  t.m.scanAll({ cacheFile: t.cache });
  seed(t, 'agent-1.jsonl', [turn('claude-opus-5', { i: 100 }, { id: 'X' }), turn('claude-opus-5', { i: 9 }, { id: 'W' })], { dir: path.join('proj', 'a', 'subagents') });
  t.m.scanAll({ cacheFile: t.cache });
  fs.writeFileSync(a, turn('claude-opus-5', { i: 100 }, { id: 'X' }) + '\n'); // a is compacted: BIG is gone from it
  const warm = t.m.scanAll({ cacheFile: t.cache });
  const cold = coldRun(t, 'cold');

  for (const group of ['model', 'session', 'day', 'repo']) {
    const w = t.m.summarize({ sessions: warm.sessions, prices: PRICES, group });
    const c = t.m.summarize({ sessions: cold.sessions, prices: PRICES, group });
    assert.deepEqual(w.rows, c.rows, `--by ${group} differs between a warm cache and a cold rebuild`);
  }
  // And the surviving copies still carry every id: X in a, Y and Z in b, W in the subagent. BIG left with the
  // compaction and is claimed by nobody.
  assert.equal(totalIn(warm.sessions, t.m), 100 + 20 + 3 + 9);
});

test('summarize prices each prompt-size bucket before it flattens them', () => {
  const t = fresh();
  // The report flattened the three buckets into one aggregate and then priced it, so the bucket index was always 0
  // and the threshold was never reached: the whole bucketing machinery was dead in the only path the report uses.
  seed(t, 'a.jsonl', [turn('gpt-6-astra', { i: 300000, o: 1e6 }, { id: 'big' })]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const direct = t.m.costOf(S(sessions, 'a').models['gpt-6-astra'], PRICES.models['gpt-6-astra']);
  assert.equal(Math.round(direct.usd), 81, '300k of input at $20/M plus 1M of output at $75/M');

  for (const group of ['model', 'session', 'day']) {
    const row = t.m.summarize({ sessions, prices: PRICES, group }).rows[0];
    assert.equal(Math.round(row.usd), 81, `--by ${group} reported the standard rate, not the tier`);
    assert.equal(row.tieredTurns, 1, `--by ${group} could never print the tiered-turn line`);
  }
  // A window forces the per-day path, which carries its own buckets.
  const windowed = t.m.summarize({ sessions, prices: PRICES, group: 'model', since: '2026-09-17' }).rows[0];
  assert.equal(Math.round(windowed.usd), 81);
});

test('summarize keeps the buckets apart when one turn is tiered and another is not', () => {
  const t = fresh();
  seed(t, 'a.jsonl', [
    turn('gpt-6-astra', { i: 300000, o: 1e6 }, { id: 'big' }),   // above 272k: $6 + $75
    turn('gpt-6-astra', { i: 1000, o: 1e6 }, { id: 'small' }),   // below it: $0.01 + $50
  ]);
  const { sessions } = t.m.scanAll({ cacheFile: t.cache });
  const row = t.m.summarize({ sessions, prices: PRICES, group: 'model' }).rows[0];
  assert.equal(row.turns, 2);
  assert.equal(Math.round(row.usd), 131, 'flattening would price both turns at one rate');
  assert.equal(row.tieredTurns, 1, 'only the big turn is tiered');
});

test('a tier states its cache rates in snake_case, and they override the base rates', () => {
  const t = fresh();
  // Every tiered entry in the shipped table writes `cache_read`; the pricer reads `cacheRead`. The two never met, so
  // a tiered cache read - the dominant term in this corpus - was billed at the standard rate.
  const bs = t.m.EMPTY_BUCKETS();
  Object.assign(bs[2], { turns: 1, cacheRead: 1e6, cacheWrite: 1e6, cw5m: 1e6 });
  const c = t.m.costOf(bs, PRICES.models['gpt-6-astra']);
  assert.equal(c.usd, 2 + 25, 'tier cache_read 2 and cache_write 25, not the base 1 and 12.5');
  assert.equal(c.partial, false);
});

test('a tier that explicitly has no rate for a class does not borrow the base one', () => {
  const t = fresh();
  // An absent tier field means "unchanged above the threshold"; an explicit null means "no published rate here", and
  // the second must stay a floor rather than quietly reusing the cheaper standard rate.
  const bs = t.m.EMPTY_BUCKETS();
  Object.assign(bs[2], { turns: 1, cacheRead: 1e6 });
  const stated = { input: 10, output: 50, cacheRead: 1, cacheWrite: 12.5, provider: 'openai',
    tiers: [{ input: 20, output: 75, cache_read: null, tier: { type: 'context', size: 272000 } }] };
  const c = t.m.costOf(bs, stated);
  assert.equal(c.usd, 0);
  assert.equal(c.partial, true);
  // With the field simply absent, the base cache rate still applies above the threshold.
  const silent = { ...stated, tiers: [{ input: 20, output: 75, tier: { type: 'context', size: 272000 } }] };
  const c2 = t.m.costOf(bs, silent);
  assert.equal(c2.usd, 1);
  assert.equal(c2.partial, false);
});

test('the fingerprint covers every dimension that can change a cost, not just base rates', () => {
  const t = fresh();
  const base = t.m.fingerprint(PRICES).prices.sha256;
  const mut = (patch) => t.m.fingerprint({ ...PRICES, models: { ...PRICES.models, 'gpt-6-astra': { ...PRICES.models['gpt-6-astra'], ...patch } } }).prices.sha256;
  // A tier-only override moves the cost of every turn above the threshold.
  assert.notEqual(mut({ tiers: [{ input: 99, output: 75, cacheRead: 2, cacheWrite: 25, tier: { type: 'context', size: 272000 } }] }), base, 'tier RATES must change it');
  assert.notEqual(mut({ tiers: [{ input: 20, output: 75, cacheRead: 2, cacheWrite: 25, tier: { type: 'context', size: 100000 } }] }), base, 'tier THRESHOLD must change it');
  // The tier's own cache rates are spelled the way the published source spells them, and both move real money.
  assert.notEqual(mut({ tiers: [{ input: 20, output: 75, cache_read: 9, cache_write: 25, tier: { type: 'context', size: 272000 } }] }), base, 'tier cache_read must change it');
  assert.notEqual(mut({ tiers: [{ input: 20, output: 75, cache_read: 2, cache_write: 99, tier: { type: 'context', size: 272000 } }] }), base, 'tier cache_write must change it');
  // provider decides whether a 1h cache write gets Anthropic's derived 2x rate or is refused as unrateable.
  assert.notEqual(mut({ provider: 'anthropic' }), base, 'provider must change it');
  assert.notEqual(mut({ cacheWrite1h: 40 }), base, 'an explicit 1h rate must change it');
  // Same table, same fingerprint.
  assert.equal(t.m.fingerprint(PRICES).prices.sha256, base);
});
