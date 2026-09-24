#!/usr/bin/env node
'use strict';
// ABOUTME: claude-usage: every family Claude account's rate-limit meter, plus the account switch and the usage-limit failover.
// ABOUTME: Owns one OAuth grant per account per machine; one is checked out into Claude Code's live store, the rest are parked here.
// claude-usage — one table with the rate-limit meters of every Claude account in the family.
//
// Each stored account is a DEDICATED Claude Code OAuth grant: `claude-usage add <name>` runs `claude auth login`
// under a private config dir (its Keychain item is "Claude Code-credentials-<sha256(dir)[:8]>"), moves the token
// pair out of that item into accounts/<name>.json, and deletes the item + dir. From then on only this tool refreshes
// that grant. That matters: a refresh ROTATES the refresh token and KILLS the previous access token, so a grant
// shared with a running Claude Code would log that session out. The login living in the default Keychain item
// (the session you are typing in) is shown as the "live" row, read-only, never refreshed here.
//
// Meter: GET https://api.anthropic.com/api/oauth/usage — the same call Claude Code's /usage screen makes.
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const HOME = os.homedir();
const ROOT = path.join(HOME, '.claude', 'claude-usage');
const ACCOUNTS = path.join(ROOT, 'accounts');
const LOGINS = path.join(ROOT, 'logins');
const METER = path.join(ROOT, 'meter.json');
const STATE = path.join(ROOT, 'state.json');
const AUTO_LOG = path.join(ROOT, 'auto.log');
const USER_SETTINGS = path.join(HOME, '.claude', 'settings.json');
const STATUSLINE_SH = path.join(ROOT, 'statusline.sh');
const CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'; // Claude Code's OAuth client
const TOKEN_URL = 'https://platform.claude.com/v1/oauth/token';
const API = 'https://api.anthropic.com';
const BETA = 'oauth-2025-04-20';
const DEFAULT_SCOPES = ['user:file_upload', 'user:inference', 'user:mcp_servers', 'user:profile', 'user:sessions:claude_code'];
const KEYCHAIN_USER = os.userInfo().username;
const NAME_RE = /^[a-z0-9][a-z0-9._-]{0,39}$/i;
const REFRESH_AHEAD_MS = 10 * 60e3; // refresh when the access token has less than this left
const STALE_MS = 15 * 60e3;
// The usage endpoint has its own budget, and a watcher is a steady customer: one call per account per 60 s tick drained it
// on the main workstation (2026-09-15, every account ✗ "usage endpoint rate-limited" for minutes at a time, ticks included). Only the
// CHECKED-OUT account's numbers actually move — nothing runs on a parked grant, its windows only change when they reset —
// so a reading is reused until it ages past the TTL for its role, which takes the steady rate from 3/min to ~1.2/min.
const METER_TTL = { checkedOut: 45e3, parked: 5 * 60e3 };
const DECIDE_MAX_AGE_MS = 15 * 60e3; // older than this, a reading is history, not grounds to move the machine
const RL_BACKOFF_MS = [60e3, 2 * 60e3, 5 * 60e3, 10 * 60e3]; // per account, after a 429: asking a drained bucket keeps it drained
const LAUNCHD_LABEL = 'gg.alakazam.claude-usage';
const LAUNCHD_PLIST = path.join(HOME, 'Library', 'LaunchAgents', `${LAUNCHD_LABEL}.plist`);
// ---------- where Claude Code keeps the login it is using (the "live store") ----------
const CFG = process.env.CLAUDE_CONFIG_DIR ? path.resolve(process.env.CLAUDE_CONFIG_DIR) : path.join(HOME, '.claude');
const CFG_IS_DEFAULT = CFG === path.join(HOME, '.claude');
const CLAUDE_JSON = CFG_IS_DEFAULT ? path.join(HOME, '.claude.json') : path.join(CFG, '.claude.json');
const CREDS_FILE = path.join(CFG, '.credentials.json');
const REFRESH_LOCK = path.join(CFG, '.oauth_refresh.lock'); // the client holds this across POST + save of a refresh
const WRITE_LOCK = path.join(CFG, '.storage-write.lock');   // the client's write lock on the credential file
const SWITCH_LOCK = path.join(ROOT, 'switch.lock');          // ours: one switch at a time (hand or watcher)
const DARWIN = process.platform === 'darwin' && process.env.CLAUDE_USAGE_STORE !== 'file'; // tests force the file backend

// ---------- the live store ----------
// Linux: <cfg>/.credentials.json. macOS: Keychain item "Claude Code-credentials"; the file is mirrored only when it already
// exists there, so the client's mtime probe (which runs before the Keychain probe) never lies.
// The item is named per config dir, exactly as Claude Code names it: the default dir gets the plain name, any other dir
// gets the -<sha256(dir)[:8]> suffix `add` already uses. Reading the plain item under a non-default CLAUDE_CONFIG_DIR
// returns another profile's grant — and INSTALLING into it would overwrite that profile's only copy.
function keychainService() { return CFG_IS_DEFAULT ? 'Claude Code-credentials' : `Claude Code-credentials-${h8(CFG)}`; }
function liveStoreRead() {
  if (DARWIN) {
    const r = security(['find-generic-password', '-s', keychainService(), '-a', KEYCHAIN_USER, '-w']);
    if (r.ok && r.out) { try { return JSON.parse(r.out); } catch { /* fall through to the file */ } }
  }
  try { return readJson(CREDS_FILE); } catch { return null; }
}
function liveStoreWrite(obj) {
  const text = JSON.stringify(obj);
  if (DARWIN) {
    const r = security(['add-generic-password', '-U', '-s', keychainService(), '-a', KEYCHAIN_USER, '-w', text]);
    if (!r.ok) throw new UsageError(`keychain write failed: ${r.err}`, 'store');
    if (!fs.existsSync(CREDS_FILE)) return;
  }
  fs.mkdirSync(CFG, { recursive: true, mode: 0o700 });
  const tmp = `${CREDS_FILE}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text, { mode: 0o600 });
  fs.renameSync(tmp, CREDS_FILE); fs.chmodSync(CREDS_FILE, 0o600);
}

// ---------- locks: directories, proper-lockfile style, exactly as the client takes them ----------
const LOCK_WAIT = { refresh: 90e3, write: 30e3, switch: 5e3, account: 150e3 }; // account: a switch can hold it across a 25 s POST + the 90 s and 30 s client-lock waits
const ACCT_LOCK_STALE = 600e3; // same worst case as the switch lock: never call a live switch's lock stale
function sleepSync(ms) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }
function lockAge(dir) { try { return Date.now() - fs.statSync(dir).mtimeMs; } catch { return null; } }
function takeLock(dir, staleMs, waitMs) {
  fs.mkdirSync(path.dirname(dir), { recursive: true, mode: 0o700 }); // a fresh machine gets the 'usage' error below, not a raw ENOENT
  const t0 = Date.now();
  for (;;) {
    try { fs.mkdirSync(dir); return () => { try { fs.rmdirSync(dir); } catch { /* already gone */ } }; }
    catch (e) {
      if (e.code !== 'EEXIST') throw e;
      const age = lockAge(dir);
      // The deadline comes FIRST: a stale lock we cannot clear (junk inside it, a permission) must time out like any other.
      // Below it, a `continue` that skipped both this and the sleep spun at 100 % CPU forever, invisibly (Type=oneshot, no timeout).
      if (Date.now() - t0 > waitMs) throw new UsageError(`${path.basename(dir)} held by another process for ${Math.round((age || 0) / 1000)}s`, 'lock');
      // Reclaim by rename, so two reclaimers cannot both believe they took it: the loser's rename fails and it goes back to waiting.
      if (age !== null && age > staleMs) { const gone = `${dir}.stale-${process.pid}`; try { fs.renameSync(dir, gone); fs.rmSync(gone, { recursive: true, force: true }); continue; } catch { /* lost the reclaim race, or not ours to move */ } }
      sleepSync(250);
    }
  }
}

// ---------- records: parked (tokens here, the tool refreshes) vs checked out (tokens in the live store, Claude Code refreshes) ----------
const TOKEN_KEYS = ['accessToken', 'refreshToken', 'expiresAt', 'refreshTokenExpiresAt'];
const EXTRA_KEYS = ['scopes', 'subscriptionType', 'rateLimitTier', 'clientId'];
function hasTokens(a) { return !!(a.accessToken && a.refreshToken); }
function isCheckedOut(a) { return !!a.checkedOut; }
function inTransition(a) { return isCheckedOut(a) && hasTokens(a); } // a switch died between park and install; nothing refreshes it until reconcile
function stripTokens(a) { for (const k of TOKEN_KEYS) delete a[k]; return a; }
function parkPair(a, oauth) { // the live pair moves into the record
  for (const k of TOKEN_KEYS) a[k] = oauth[k] ?? null;
  a.oauthExtra = Object.fromEntries(EXTRA_KEYS.filter((k) => oauth[k] !== undefined).map((k) => [k, oauth[k]]));
  if (oauth.scopes) a.scopes = oauth.scopes;
  a.parkedAt = new Date().toISOString();
  return a;
}
function pairFromRecord(a) { // what goes back into the live store's claudeAiOauth; a.scopes wins over the oauthExtra snapshot because a refresh updates a.scopes only
  const x = a.oauthExtra || {};
  return { accessToken: a.accessToken, refreshToken: a.refreshToken, expiresAt: a.expiresAt, refreshTokenExpiresAt: a.refreshTokenExpiresAt || null,
    scopes: a.scopes || x.scopes || DEFAULT_SCOPES, subscriptionType: x.subscriptionType || a.plan || 'max', rateLimitTier: x.rateLimitTier || a.rateLimitTier || a.tier || '',
    ...(x.clientId ? { clientId: x.clientId } : {}) };
}
function claudeJsonAccount() { try { return readJson(CLAUDE_JSON).oauthAccount || null; } catch { return null; } }

// ---------- who is the live login? ----------
async function profileOf(token) {
  const p = await net.http('GET', `${API}/api/oauth/profile`, { headers: { Authorization: `Bearer ${token}`, 'anthropic-beta': BETA } });
  if (p.status !== 200) return null;
  return { accountUuid: p.data.account?.uuid || null, email: p.data.account?.email || null, orgName: p.data.organization?.name || null,
    tier: p.data.organization?.rate_limit_tier || '', plan: p.data.account?.has_claude_max ? 'max' : p.data.account?.has_claude_pro ? 'pro' : '' };
}
// The profile behind the live token is authoritative; ~/.claude.json is what Claude Code wrote at login and is only wrong
// if a switch died before step 10 — acceptable as a fallback for an expired token. `source` tells callers which one answered,
// so a destructive repair can refuse to trust the unverified fallback.
async function liveIdentity(live) {
  const o = live?.claudeAiOauth;
  if (o?.accessToken) { try { const p = await profileOf(o.accessToken); if (p?.accountUuid) return { ...p, source: 'profile' }; } catch { /* offline: fall back */ } }
  const a = claudeJsonAccount();
  return a?.accountUuid ? { accountUuid: a.accountUuid, email: a.emailAddress || '?', orgName: a.organizationName || null, tier: a.organizationRateLimitTier || '', plan: '', source: 'claudeJson' } : null;
}
// Unlocked read-modify-rename, on purpose: ~/.claude.json is Claude Code's own file and only `oauthAccount` is ours to
// set. A racing writer can cost us this one cosmetic field (the next switch or reconcile writes it again); no grant, no
// token and no invariant lives here, so there is nothing a lost update could corrupt.
function writeClaudeJsonAccount(a) {
  let j; try { j = readJson(CLAUDE_JSON); } catch { return; } // no ~/.claude.json yet: nothing to fix
  j.oauthAccount = a.claudeJsonAccount || { accountUuid: a.accountUuid, emailAddress: a.email, organizationName: a.orgName || undefined, organizationRateLimitTier: a.tier || undefined };
  writeJsonAtomic(CLAUDE_JSON, j, fs.statSync(CLAUDE_JSON).mode & 0o777);
}
// Which stored record owns the live pair? Then repair anything a dead switch left behind: the owner is checked out
// (tokens stripped), everyone else is parked (checkedOut dropped, tokens kept). Returns the owner, or null when the
// live login is not a stored account at all.
async function reconcile(live) {
  const id = await liveIdentity(live);
  if (!id?.accountUuid) return null;
  const accts = names().map(loadAccount);
  const owner = accts.find((a) => a.accountUuid === id.accountUuid) || null;
  // A repair rewrites records on the strength of this identity; an unverified (~/.claude.json) identity is not strong
  // enough to strip a grant or drop a checkedOut flag — refuse before touching anything, rather than guess and corrupt.
  const needsRepair = (owner && (!owner.checkedOut || hasTokens(owner))) || accts.some((a) => a !== owner && a.checkedOut);
  if (needsRepair && id.source !== 'profile') throw new UsageError('records need repair but the live token cannot be verified (expired or offline) — run one claude session so it refreshes the token, then retry', 'none');
  for (const a of accts) {
    if (owner && a.name === owner.name) {
      if (!a.checkedOut || hasTokens(a)) {
        if (hasTokens(a) && !a.checkedOut) log(`${a.name}: live login adopted; its earlier stored grant is superseded (it expires on its own)`);
        a.checkedOut = a.checkedOut || new Date().toISOString(); stripTokens(a); delete a.dead; saveAccount(a);
      }
    } else if (a.checkedOut) { delete a.checkedOut; saveAccount(a); }
  }
  if (owner && state().live !== owner.name) setState({ live: owner.name, home: state().home || owner.name });
  // The profile just proved who owns the live pair: if ~/.claude.json still names someone else (a dead switch stopped
  // short of writing it), fix it now rather than leave Claude Code's own UI pointing at the wrong account.
  if (owner && id.source === 'profile' && claudeJsonAccount()?.accountUuid !== owner.accountUuid) writeClaudeJsonAccount(owner);
  return owner;
}
async function adoptLive(name) {
  if (!name || !NAME_RE.test(name) || name === 'live') throw new UsageError('usage: claude-usage adopt <name>', 'usage');
  const live = liveStoreRead();
  if (!live?.claudeAiOauth?.accessToken) throw new UsageError('no live login in Claude Code (claude auth login first)', 'none');
  const id = await liveIdentity(live);
  if (!id?.accountUuid) throw new UsageError('cannot identify the live login (token expired and no ~/.claude.json account) — run a claude session once, then retry', 'none');
  const dup = names().find((n) => n !== name && loadAccount(n).accountUuid === id.accountUuid);
  if (dup) throw new UsageError(`${id.email} is already stored as "${dup}" — use that name`, 'usage');
  let a = {}; try { a = loadAccount(name); } catch { /* new record */ }
  if (hasTokens(a)) log(`${name}: its earlier stored grant is superseded by the live login (it expires on its own)`);
  const rec = { ...a, name, email: id.email || a.email || '?', accountUuid: id.accountUuid, orgName: id.orgName || a.orgName || null, tier: id.tier || a.tier || '', plan: id.plan || a.plan || '',
    scopes: live.claudeAiOauth.scopes || a.scopes || DEFAULT_SCOPES, addedAt: a.addedAt || new Date().toISOString(), checkedOut: new Date().toISOString(), claudeJsonAccount: claudeJsonAccount() };
  stripTokens(rec); delete rec.dead;
  fs.mkdirSync(ACCOUNTS, { recursive: true, mode: 0o700 }); saveAccount(rec);
  // The invariant: exactly one record is checked out. Whoever claimed it before had its pair in the live store, which now
  // holds this login instead — so that grant is gone, and leaving the flag on would meter it with THIS account's token.
  for (const n of names()) {
    if (n === name) continue;
    const o = loadAccount(n); if (!o.checkedOut) continue;
    delete o.checkedOut; saveAccount(o);
    log(`${n}: no longer checked out (the live login is "${name}" now)${hasTokens(o) ? '' : ` — it holds no tokens: \`claude-usage add ${n} --force\` stores a grant for it again`}`);
  }
  setState({ live: name, home: state().home || name });
  log(`adopted the live login as "${name}" (${rec.email}, ${planLabel(rec)}) — checked out; home = ${state().home}`);
}

// ---------- the hand-off ----------
// Park the live pair into its record, install the target's pair into the live store. Under the client's own locks, in its
// order, so no refresh is in flight and none starts while we hold them: the pair we park is the pair the server knows.
// Every session on the machine, subagents included, is on the target at its next request (the client re-reads on mtime).
// Lock order, and it is the whole safety argument: switch.lock (one switch at a time) → refresh-<target>.lock (no parked
// refresh may rotate the pair we are about to install, and none may start once we hold it) → .oauth_refresh.lock →
// .storage-write.lock (the client's own two, in the client's order). SWITCH_TRACE records that order, and the re-read of
// the target under all of them, so a test can pin it: nothing else in the code would notice if it changed.
const SWITCH_TRACE = [];
async function switchTo(target, { failover = false } = {}) {
  SWITCH_TRACE.length = 0;
  const releaseSwitch = takeLock(SWITCH_LOCK, 600e3, LOCK_WAIT.switch); // worst-case hold: a stalled refresh POST plus both client locks
  SWITCH_TRACE.push('switch.lock');
  try {
    const live0 = liveStoreRead();
    // An empty credential store is what /logout leaves behind: that grant is revoked server-side, so there is nothing to
    // park and nothing to lose. Installing a parked grant is then the only way back in without a browser — never refuse it.
    const emptyStore = !live0?.claudeAiOauth?.accessToken;
    const cur = emptyStore ? null : await reconcile(live0);
    if (!emptyStore && !cur) throw new UsageError('the live login is not a stored account — `claude-usage adopt <name>` takes it', 'usage');
    const curName = cur ? cur.name : (names().find((n) => isCheckedOut(loadAccount(n))) || null);
    if (!target) {
      const others = names().filter((n) => n !== curName);
      if (others.length !== 1) throw new UsageError(`switch to which? ${others.join(', ') || 'no other account stored'}`, 'usage');
      target = others[0];
    }
    if (!NAME_RE.test(target)) throw new UsageError('name: letters, digits, . _ - only', 'usage');
    if (cur && target === cur.name) throw new UsageError(`already on ${target}`, 'usage'); // with an empty store the same name falls through to "holds no tokens", which is the true reason
    if (!fs.existsSync(acctFile(target))) throw new UsageError(`no stored account "${target}" (have: ${names().join(', ')})`, 'usage');
    const releaseAcct = takeLock(acctLock(target), ACCT_LOCK_STALE, LOCK_WAIT.account); // held across the refresh, the park and the install
    SWITCH_TRACE.push(path.basename(acctLock(target)));
    try {
      let to = loadAccount(target);
      if (to.dead) throw new UsageError(`${target}: ${to.dead}`, 'dead');
      if (!hasTokens(to)) throw new UsageError(`${target} holds no tokens — claude-usage add ${target} --force`, 'dead');
      if ((to.expiresAt || 0) - Date.now() < REFRESH_AHEAD_MS) await refresh(to, { locked: true }); // a fresh 8 h token for the sessions; no refresh races the swap
      const releaseRefresh = takeLock(REFRESH_LOCK, 60e3, LOCK_WAIT.refresh);
      SWITCH_TRACE.push(path.basename(REFRESH_LOCK));
      try {
        const releaseWrite = takeLock(WRITE_LOCK, 15e3, LOCK_WAIT.write);
        SWITCH_TRACE.push(path.basename(WRITE_LOCK));
        try {
          const live = liveStoreRead(); // re-read under the locks: the client may have refreshed since
          if (!emptyStore && !live?.claudeAiOauth?.accessToken) throw new UsageError('the live login vanished under the switch — retry', 'none');
          if (emptyStore && live?.claudeAiOauth?.accessToken) throw new UsageError('a login appeared in the empty credential store under the switch (a /login?) — retry', 'usage');
          if (cur) {
            const cj = claudeJsonAccount();
            if (cj?.accountUuid !== cur.accountUuid) throw new UsageError('the live login changed under the switch (a /login?) — retry', 'usage');
            cur.claudeJsonAccount = cj; // proven cur's own by the check above; never park a stale/mismatched snapshot
            parkPair(cur, live.claudeAiOauth); saveAccount(cur); // checkedOut kept: in transition
          } else for (const n of names()) { // nothing to park: drop the flag from whoever still claims the store, and say why
            const a = loadAccount(n); if (!a.checkedOut || n === target) continue;
            delete a.checkedOut; saveAccount(a);
            log(`${n}: Claude Code's credential store is empty (a /logout?) — nothing to park, and that grant is revoked: \`claude-usage add ${n} --force\` stores a new one`);
          }
          to = loadAccount(target); // the copy above can be ~145 s old by now (both client-lock waits): install the pair that is on disk NOW,
          if (to.dead) throw new UsageError(`${target}: ${to.dead}`, 'dead');                              // never one a parked refresh has since rotated away
          if (!hasTokens(to)) throw new UsageError(`${target} holds no tokens — claude-usage add ${target} --force`, 'dead');
          SWITCH_TRACE.push(`reload:${target}`);
          to.checkedOut = new Date().toISOString(); saveAccount(to);                                        // in transition too
          liveStoreWrite({ ...(live || {}), claudeAiOauth: pairFromRecord(to) });
          SWITCH_TRACE.push('install');
          stripTokens(to); saveAccount(to);
          if (cur) { delete cur.checkedOut; saveAccount(cur); }
          setState({ live: target, lastSwitchAt: Date.now(), ...(failover ? {} : { home: target }) });
        } finally { releaseWrite(); }
      } finally { releaseRefresh(); }
      try { writeClaudeJsonAccount(to); } catch (e) { log(`warning: ~/.claude.json not updated (${e.message}) — /status may show the old account until the next switch`); } // the switch itself already committed; this is cosmetic
      return { from: curName || 'none', to: target, parked: !!cur };
    } finally { releaseAcct(); }
  } finally { releaseSwitch(); }
}
async function cmdSwitch(target, opts) {
  const r = await switchTo(target, opts);
  const to = loadAccount(r.to);
  log(`live: ${r.to} (${to.email}) · ${r.parked ? `${r.from} parked` : 'the credential store was empty — nothing to park'} · sessions follow at their next request${opts?.failover ? '' : ` · home = ${r.to}`}`);
}

// ---------- the failover decision: pure, so it is testable without a network ----------
const THRESHOLDS = { switchAt: 95, targetBelow: 85, fableCeiling: 90, sessionHot: 85, sessionWarm: 65, edfLeadMs: 7_200_000, minHoldMs: 600_000, blindAfter: 3 }; // targetBelow: the ChatGPT domain only (decideCodex)
// The meter is not the only evidence of a limit, and it is not always there. On 2026-09-15 perso's usage endpoint answered
// 429 for nine ticks exactly while perso refused jobs (the granola sweep died at 18:45Z), and work2's for eleven while it
// climbed from 65% to 100%: the meter goes blind when an account is busiest. Two answers: a refusal written by any run is
// proof of the limit, and a live row unreadable for blindAfter ticks in a row is a reason to move to an account with room
// rather than to wait. Neither could act that day: every other account was out too.
const LIMIT_COOLDOWN_MS = 60 * 60e3; // how long a refusal keeps an account off the candidate list (its meter lies meanwhile)
const REFUSAL_TAIL_BYTES = 256 * 1024; // only the end of a transcript can hold a turn newer than the last scan
// The mtime test below is an OPTIMISATION — which files are worth opening — while the per-turn timestamp is the
// correctness gate, so it has to err towards opening one file too many. The kernel stamps mtime from a coarse clock
// that runs behind the fine-grained one `new Date().toISOString()` records: measured on the main workstation, 85% of writes land
// up to 0.85 ms behind the Date.now() taken immediately before them, and other filesystems are coarser still. So a
// transcript written just after a scan can carry an mtime just before it. Compared exactly, that refusal is skipped —
// and because the next scan starts where this one ended, it is lost for good rather than caught next time.
const MTIME_SLACK_MS = 1000;
const REFUSAL_RE = /(hit your (?:5-hour|weekly|usage) limit|out of usage credits|usage limit reached)/i;
function thresholds() { return { ...THRESHOLDS, ...(state().thresholds || {}) }; }
// A window is evidence only while it carries a number. Missing is UNKNOWN, never empty, and the difference is the whole
// of whether this account may be handed the machine: scored as 0% an unparsed row sorts ahead of every real candidate,
// so the emptiest-looking account on the table is the one nobody managed to read.
// Number(null) is 0 and Number('') is 0, so the coercion has to be gated rather than trusted: this exact shortcut is
// how an absent percentage became a full tank in the first place.
const pctOf = (w) => { if (!w || w.percent === null || w.percent === undefined || w.percent === '') return null; const n = Number(w.percent); return Number.isFinite(n) ? n : null; };
// Every window gates the account, the per-model weeklies included: a spent window is a spent window, whichever one it is.
const windowsOf = (usage) => [usage.session, usage.weekly, ...(usage.scoped || [])].filter(Boolean);
function worstOf(usage) {
  const ps = windowsOf(usage).map(pctOf).filter((p) => p !== null);
  return ps.length ? Math.max(...ps) : null; // null: nothing readable. No caller may read that as room.
}
// Why this account cannot take the machine, or null when it can — the test both halves of a switch have to pass.
// An Anthropic payload always carries BOTH the 5-hour and the weekly window, so a missing one is a degraded reading
// and not a plan without that window: the account fails here rather than being scored on the half that did parse.
// ChatGPT plans genuinely differ (on Pro Lite there is no 5-hour window at all), so there the test is only that
// something was readable. A locked window is unavailable at any percentage — the number describes a budget, the lock
// describes the door.
function blockedFrom(row, limit) {
  const u = row.usage;
  if (!u) return 'no reading';
  if ((row.provider || 'anthropic') === 'anthropic') {
    if (pctOf(u.session) === null) return '5-hour window unreadable';
    if (pctOf(u.weekly) === null) return 'weekly window unreadable';
  }
  const locked = windowsOf(u).find((w) => w.locked);
  if (locked) return `${locked.label || 'a window'} locked${typeof locked.locked === 'string' ? `: ${locked.locked}` : ''}`;
  const worst = worstOf(u);
  if (worst === null) return 'no window readable';
  return worst < limit ? null : `${worst}%`;
}
// Fable specifically, unweighted — the safety-stock floor in decide() below cares about Fable alone, never
// whichever per-model window happens to be worst.
function fablePctOf(usage) {
  const f = (usage.scoped || []).find((s) => s.label === 'Fable');
  return pctOf(f) ?? 0;
}
// ---------- the EDF candidate table ----------
// Why this account cannot take the machine, or null when it can. The 5-hour bound is the caller's: sessionHot for the
// hard rule (an emergency takes any account with room), sessionWarm for a proactive rotation (never rotate INTO an
// account that is about to go warm itself — it would go hot after a few requests and the hard rule would fire again,
// churn for nothing). blockedFrom, given no percentage limit, still owns "did it parse" and "is it locked": a spent
// window is a budget, a locked one is a door.
function unusable(row, t, sessionBound) {
  const parse = blockedFrom(row, Infinity);
  if (parse) return parse;
  const u = row.usage;
  const session = pctOf(u.session), weekly = pctOf(u.weekly), fable = fablePctOf(u);
  const fableCeiling = t.fableCeiling ?? THRESHOLDS.fableCeiling;
  if (session !== null && session >= sessionBound) return `5h ${session}% ≥ ${sessionBound}% cap`;
  if (weekly !== null && weekly >= t.switchAt) return `weekly ${weekly}% ≥ ${t.switchAt}% ceiling`;
  if (fable >= fableCeiling) return `Fable ${fable}% ≥ ${fableCeiling}% floor`;
  return null;
}
// EDF: the account whose weekly reset is soonest wins — headroom unspent at reset is lost, headroom on a later reset
// can still be spent later. Priority tiers first, always. Same reset (to the minute): the most remaining weekly
// headroom, it has the most to drain before that deadline — two resets seconds apart are effectively the same
// deadline, and the emptier-looking account is the better first pick between them. An unknown reset sorts last;
// mapping it to MAX_SAFE_INTEGER rather than leaving it Infinity keeps two unknowns tied (equal minute) rather than
// Infinity - Infinity producing NaN.
function edfCompare(a, b) {
  if (a.priority !== b.priority) return a.priority - b.priority;
  const minute = (x) => (Number.isFinite(x.resetsAtMs) ? Math.floor(x.resetsAtMs / 60e3) : Number.MAX_SAFE_INTEGER);
  if (minute(a) !== minute(b)) return minute(a) - minute(b);
  return a.weekly - b.weekly;
}
// The table every decision and every display reads: one row per parked Anthropic account with a fresh enough reading,
// EDF-ordered, with why it cannot take the machine under the hard-rule bound (`blocked`) and whether a proactive
// rotation may enter it (`warm`, the sessionWarm bound). `next` is the row a rotation would pick: usable, enterable,
// and never in a worse priority tier than the live account — the last-resort tier is reachable only through the hard
// rule, which reads `parked` directly.
function candidates({ rows, live, thresholds: t, limited = {} }) {
  const stale = (r) => r?.usage?.at && Date.now() - r.usage.at > DECIDE_MAX_AGE_MS;
  const ok = (r) => r && !r.error && r.usage && !stale(r) && worstOf(r.usage) !== null;
  const refused = (n) => { const at = Date.parse(limited[n] || ''); return Number.isFinite(at) && Date.now() - at < LIMIT_COOLDOWN_MS; };
  const sessionHot = t.sessionHot ?? THRESHOLDS.sessionHot, sessionWarm = t.sessionWarm ?? THRESHOLDS.sessionWarm;
  // !r.live: the synthetic "live" row is the login itself, not a parked grant — switchTo('live') throws. provider: the
  // ChatGPT row is a meter, not a grant; switchTo('chatgpt') has nothing to check out.
  const parked = rows.filter((r) => (r.provider || 'anthropic') === 'anthropic' && r.name !== live && ok(r) && !r.checkedOut && !r.live).map((r) => ({
    name: r.name,
    usage: r.usage,
    worst: worstOf(r.usage),
    weekly: pctOf(r.usage.weekly) ?? 0,
    session: pctOf(r.usage.session) ?? 0,
    resetsAt: r.usage.weekly?.resetsAt || null,
    resetsAtMs: Date.parse(r.usage.weekly?.resetsAt || '') || Infinity,
    priority: r.priority || 0,
    blocked: refused(r.name) ? 'refused on its limit' : unusable(r, t, sessionHot),
    warm: !refused(r.name) && !unusable(r, t, sessionWarm),
  })).sort(edfCompare);
  const L = rows.find((r) => r.name === live);
  const livePriority = L?.priority || 0;
  const next = parked.find((p) => !p.blocked && p.warm && p.priority <= livePriority) || null;
  return { parked, next, ok, refused };
}
// blind: how many ticks in a row before this one found the live row unreadable (tick keeps the count in state).
// limited: account name → ISO time a limit refusal was seen for it (see limitRefusals).
// Precedence, top to bottom, one chain of gates: refused live > unreadable or half-readable live (blind streak) >
// the hard rule (locked / 5h ≥ sessionHot / weekly ≥ switchAt / Fable ≥ fableCeiling; any tier; never held) >
// EDF rotation with hysteresis (better tier / ≥ edfLead lead / warm live 5h; live's tier or better; held) > none.
function decide({ rows, live, thresholds: t, exhaustedSince, blind = 0, limited = {}, lastSwitchAt = 0 }) {
  const { parked, next, ok, refused } = candidates({ rows, live, thresholds: t, limited });
  const L = rows.find((r) => r.name === live);
  const halfRead = (r) => (r?.provider || 'anthropic') === 'anthropic' && !!r?.usage && (pctOf(r.usage.session) === null || pctOf(r.usage.weekly) === null);
  // worstOf !== null first: halfRead is an OR, so it is true of a row where NOTHING parsed too, and that row's reason
  // is 'no window readable' — naming one window implies the other answered.
  const why = (r) => r?.error || (!r?.usage ? 'missing' : r.usage.at && Date.now() - r.usage.at > DECIDE_MAX_AGE_MS ? `last reading ${Math.round((Date.now() - r.usage.at) / 60e3)} min old` : worstOf(r.usage) !== null && halfRead(r) ? blockedFrom(r, Infinity) : 'no window readable');
  // The hard rule's target: the EDF-first usable row of ANY tier. A deprioritised account (Louis's personal one) is a
  // LAST resort: it is taken only when nothing preferred is usable — a dead machine is worse — and the alert has to say
  // so, because not being on it is the entire point of the flag. Warn only when there WAS a preferred alternative and it
  // had no room: with no preferred candidate at all the switch is the only possible move and "deprioritised" is noise.
  const pick = () => {
    const to = parked.find((p) => !p.blocked);
    if (!to) return null;
    const preferred = to.priority ? parked.filter((p) => p.priority < to.priority) : [];
    return { ...to, warn: preferred.length ? `${to.name} is deprioritised; ${preferred.map((p) => `${p.name} ${p.blocked}`).join(', ')} had no room` : null };
  };
  if (live && refused(live)) {
    const to = pick();
    if (to) return { action: 'failover', to: to.name, warn: to.warn, reason: `${live} refused on its limit at ${limited[live]}, ${to.name} ${to.worst}%` };
    return { action: 'exhausted', notify: !exhaustedSince, reason: `${live} refused on its limit, no usable account` };
  }
  // A live row that parsed only one of its two windows is a degraded READING, not evidence about the account: it
  // joins the blind streak (debounced by blindAfter) rather than the hard rule, which would move the machine — or
  // page "exhausted" — on one bad read. Candidates keep the stricter test in unusable(): an unread window is not room.
  if (!ok(L) || halfRead(L)) {
    if (!live) return { action: 'none', reason: 'no live account' };
    const ticks = blind + 1, after = t.blindAfter ?? THRESHOLDS.blindAfter;
    const to = ticks >= after && pick();
    if (to) return { action: 'failover', to: to.name, warn: to.warn, liveUnreadable: true, reason: `live row ${live} unreadable ${ticks} ticks (${why(L)}), ${to.name} ${to.worst}%` };
    return { action: 'none', liveUnreadable: true, reason: `live row ${live} unreadable (${why(L)})${ticks >= after ? `, ${ticks} ticks, no usable account` : ''}` };
  }
  const sessionHot = t.sessionHot ?? THRESHOLDS.sessionHot, sessionWarm = t.sessionWarm ?? THRESHOLDS.sessionWarm;
  const edfLeadMs = t.edfLeadMs ?? THRESHOLDS.edfLeadMs, minHoldMs = t.minHoldMs ?? THRESHOLDS.minHoldMs;
  const target = (p) => `${p.name} 5h ${p.session}% weekly ${p.weekly}% ↻${until(p.resetsAt)}`;
  // Step 1 — the hard rule: live cannot serve (locked / 5-hour at the cap / weekly at the ceiling / Fable at the floor).
  // Move now, to the best usable row of any tier; never held. The 5-hour cap sits under 100 because a switch lands at
  // the next request and the meter is read once a minute: at 98% there is no headroom left to absorb that lag.
  const cannot = unusable(L, t, sessionHot);
  if (cannot) {
    const to = pick();
    if (to) return { action: 'failover', to: to.name, warn: to.warn, reason: `${live} ${cannot}, ${target(to)}` };
    return { action: 'exhausted', notify: !exhaustedSince, reason: `${live} ${cannot}, no usable account` };
  }
  // Steps 2-4 — EDF among usable, enterable rows in live's tier or better (`next`), then hysteresis: a strictly better
  // tier, a real lead on the weekly reset, or a warm live 5-hour window; and not inside the hold window. Two accounts
  // resetting minutes apart never ping-pong — neither leads, so the machine stays until the live window warms, rotates
  // once, and the same test holds it there.
  const liveSession = pctOf(L.usage.session) ?? 0;
  const liveWeekly = pctOf(L.usage.weekly) ?? 0;
  const livePriority = L.priority || 0;
  const status = `${live} 5h ${liveSession}% weekly ${liveWeekly}% ↻${until(L.usage.weekly?.resetsAt)}`;
  if (!next) return { action: 'none', reason: `${status}; nothing to rotate to` };
  const lead = (Date.parse(L.usage.weekly?.resetsAt || '') || Infinity) - next.resetsAtMs; // Infinity−x = Infinity, x−Infinity = −Infinity, Infinity−Infinity = NaN: all compare as intended
  const hours = (ms) => `${Math.round(ms / 36e4) / 10}h`;
  const because = next.priority < livePriority ? `${next.name} is a preferred account and usable`
    : lead >= edfLeadMs ? (Number.isFinite(lead) ? `${next.name} resets ${hours(lead)} sooner` : `${live} has no weekly deadline yet`)
    : liveSession >= sessionWarm ? `${live} 5h ${liveSession}% ≥ ${sessionWarm}% warm`
    : null;
  const held = (Date.now() - (lastSwitchAt || 0)) < minHoldMs;
  if (because && !held) return { action: 'rebalance', to: next.name, warn: null, reason: `${status}; ${because} → ${target(next)}` };
  return { action: 'none', reason: `${status}; next ${target(next)}${because ? ' (hold)' : ` (lead ${Number.isFinite(lead) ? hours(lead) : '?'} < ${hours(edfLeadMs)}, 5h < ${sessionWarm}%)`}` };
}

// The ChatGPT domain decides on its own rows and its own active account, and never looks at a Claude row. That is the
// whole of "stay in the same model family": the two resources are not substitutes, so there is no ordered candidate
// list to get wrong. When every ChatGPT account is spent, nothing reroutes — the alert names the Claude model at the
// same tier and Louis moves the session himself.
function decideCodex({ rows, active, thresholds: t, blocked = null, exhaustedSince = null }) {
  const gpt = rows.filter((r) => (r.provider || 'anthropic') === 'openai');
  const ok = (r) => r && !r.error && r.usage && (!r.usage.at || Date.now() - r.usage.at <= DECIDE_MAX_AGE_MS) && worstOf(r.usage) !== null;
  if (!active) return { action: 'none', reason: gpt.length ? 'no active ChatGPT account' : 'no ChatGPT accounts' };
  const A = gpt.find((r) => r.name === active);
  const others = gpt.filter((r) => r.name !== active && ok(r)).map((r) => ({ name: r.name, worst: worstOf(r.usage), priority: r.priority || 0, blocked: blockedFrom(r, t.targetBelow) })).sort((a, b) => a.priority - b.priority || a.worst - b.worst);
  // The blocked file is written on 401/403/429 and stays until the plugin's setup clears it, so it is evidence only
  // while fresh AND only as a 429: a 401 is a broken login, and switching accounts would hide that instead of fixing it.
  const rejected = !!blocked && blocked.statusCode === 429 && Date.now() - Date.parse(blocked.observedAt || '') < CODEX_BLOCKED_COOLDOWN_MS;
  const metered = ok(A) ? worstOf(A.usage) : null;
  if (!rejected && !(metered !== null && metered >= t.switchAt)) {
    return { action: 'none', reason: metered !== null ? `${active} ${metered}%` : `${active} unreadable (${A?.error || 'missing'})` };
  }
  const to = others.find((o) => !o.blocked);
  const why = rejected ? `${active} was refused on a spent window` : `${active} ${metered}% >= ${t.switchAt}%`;
  if (to) {
    const preferred = to.priority ? others.filter((o) => o.priority < to.priority) : [];
    const warn = preferred.length ? `${to.name} is deprioritised; ${preferred.map((o) => `${o.name} ${o.blocked}`).join(', ')} had no room` : null;
    return { action: 'failover', to: to.name, warn, reason: `${why}, ${to.name} ${to.worst}%` };
  }
  const suggest = CODEX_TIER_FALLBACK['gpt-6-astra'];
  return { action: 'exhausted', notify: !exhaustedSince, suggest, reason: `${why}, and no other ChatGPT account is under ${t.targetBelow}% — switch the session to /model ${suggest}` };
}
// One-time move from the proxy's own directory into the account store, so the live path becomes a link we can retarget.
// Replacing a directory with a symlink cannot be atomic (rename refuses to put a link over a non-empty directory), so
// there is a window of a few milliseconds where the path does not exist; a request landing in it fails once and retries.
function codexMigrate(name) {
  let st = null; try { st = fs.lstatSync(CODEX_LIVE); } catch { /* nothing to migrate */ }
  if (!st) return { action: 'none', reason: 'no gateway credential on this machine' };
  if (st.isSymbolicLink()) return { action: 'none', reason: `already a link to ${codexActive() || 'an unknown target'}` };
  let auth = null; try { auth = readJson(path.join(CODEX_LIVE, 'auth.json')); } catch { /* reported below */ }
  if (!auth?.access) throw new UsageError(`${CODEX_LIVE} holds no credential to migrate`, 'usage');
  if (codexNames().includes(name)) throw new UsageError(`${name} already exists in the store`, 'usage');
  fs.mkdirSync(CODEX_STORE, { recursive: true, mode: 0o700 });
  const aside = `${CODEX_LIVE}.migrating-${process.pid}`;
  fs.renameSync(CODEX_LIVE, aside);      // the directory itself, credential and all — never a copy
  try { fs.renameSync(aside, codexDir(name)); }
  catch (e) { fs.renameSync(aside, CODEX_LIVE); throw e; } // put it back rather than leave the gateway with no credential
  fs.symlinkSync(codexDir(name), CODEX_LIVE);
  const seen = readJson(path.join(CODEX_LIVE, 'auth.json')); // through the link, the way the proxy reads it
  if (!seen?.access) throw new UsageError('migrated, but the credential is unreadable through the new link', 'none');
  return { action: 'migrated', to: name };
}

// ---------- refusals: what a run saw while the meter could not say ----------
// Claude Code writes a limit refusal into the session transcript as an assistant turn with model "<synthetic>" (memory
// daemon-model-pinned). Every run on the machine — the Seven daemon, a -p job, an interactive session — lands under
// <cfg>/projects, so one scan covers them all without touching a single unit. Only files modified since `since` are
// opened, only their tail is read, and a turn counts only if its own timestamp is newer too: a transcript appended for
// another reason must not replay an old refusal. Returns the newest refusal, or null.
function limitRefusals({ since }) {
  const root = path.join(CFG, 'projects');
  let dirs = [];
  try { dirs = fs.readdirSync(root, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => path.join(root, d.name)); } catch { return null; }
  let newest = null;
  for (const dir of dirs) {
    let files = [];
    try { files = fs.readdirSync(dir).filter((f) => f.endsWith('.jsonl')); } catch { continue; }
    for (const f of files) {
      const file = path.join(dir, f);
      let st; try { st = fs.statSync(file); } catch { continue; }
      if (st.mtimeMs < since - MTIME_SLACK_MS) continue; // slack: see MTIME_SLACK_MS — the turn timestamp still filters exactly
      let text;
      try {
        const fd = fs.openSync(file, 'r');
        try { const len = Math.min(st.size, REFUSAL_TAIL_BYTES); const buf = Buffer.alloc(len); fs.readSync(fd, buf, 0, len, st.size - len); text = buf.toString('utf8'); }
        finally { fs.closeSync(fd); }
      } catch { continue; }
      for (const line of text.split('\n')) {
        if (!line.includes('<synthetic>')) continue; // cheap filter before JSON.parse; also drops the torn first line of a tail
        let r; try { r = JSON.parse(line); } catch { continue; }
        if (r?.type !== 'assistant' || r.message?.model !== '<synthetic>') continue;
        const at = Date.parse(r.timestamp || '');
        if (!Number.isFinite(at) || at < since) continue;
        const said = (Array.isArray(r.message.content) ? r.message.content : []).map((c) => c?.text || '').join(' ');
        if (!REFUSAL_RE.test(said)) continue;
        if (!newest || at > newest.atMs) newest = { atMs: at, text: said.slice(0, 160), file };
      }
    }
  }
  return newest && { at: new Date(newest.atMs).toISOString(), text: newest.text, file: newest.file };
}

// ---------- telling Louis ----------
// Linux: Telegram through the channel bot (send-only, never getUpdates — the Seven daemon holds the one poller), prefix per
// the private infra repo's naming rules (docs/naming.md there). macOS: a desktop notification. Always a line in auto.log.
const TAG = 'claude-usage';
const TG_DIR = path.join(HOME, '.claude', 'channels', 'telegram');
// Quote char captured separately (group 2) so a value's own '#' is preserved when quoted, stripped as a comment when not; \r
// excluded from the value class so a CRLF .env never leaves a trailing carriage return baked into the token.
function parseEnv(f) { const out = {}; try { for (const l of fs.readFileSync(f, 'utf8').split('\n')) { const m = l.match(/^\s*(?:export\s+)?([A-Z0-9_]+)=(["']?)([^"'\r\n]*)["']?\s*$/); if (m) out[m[1]] = m[2] ? m[3] : m[3].replace(/\s+#.*$/, ''); } } catch { /* no file */ } return out; }
function logLine(text) { try { fs.mkdirSync(ROOT, { recursive: true, mode: 0o700 }); fs.appendFileSync(AUTO_LOG, `${new Date().toISOString()} ${text}\n`, { mode: 0o600 }); } catch { /* best effort */ } }
async function notify(text) {
  logLine(text);
  if (DARWIN) { try { spawnSync('osascript', ['-e', `display notification ${JSON.stringify(text)} with title "claude-usage"`]); } catch { /* best effort */ } return; }
  const token = parseEnv(path.join(TG_DIR, '.env')).TELEGRAM_BOT_TOKEN; let chat = null;
  try { chat = readJson(path.join(TG_DIR, 'access.json')).allowFrom?.[0] ?? null; } catch { /* no channel on this box */ }
  if (!token || chat === null) return;
  const msg = `🖥️ [${TAG}] ${os.hostname().toLowerCase()}: ${text}`;
  const url = `https://api.telegram.org/bot${token}/sendMessage`; // built once so a thrown e.message that echoes it can be redacted below
  const redact = (s) => String(s).split(token).join('<token>'); // never let the bot token reach the log
  // net.http RETURNS a status, it does not throw one: without this a revoked bot token or a wrong chat id 4xxs in silence
  // and every failover alert vanishes with it.
  try { const r = await net.http('POST', url, { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat_id: chat, text: msg }) });
    if (r.status !== 200) logLine(`telegram ${r.status}: ${redact(r.text || '').slice(0, 120)}`); }
  catch (e) { logLine(`telegram failed: ${redact(e.message)}`); }
}

// `prices` with no flag shows what the table says; `--refresh` replaces it from one unauthenticated GET, which is
// what makes keeping it current a cron line rather than a conversation.
async function cmdPrices(opts = {}) {
  const C = require('./cost.js');
  if (opts.refresh) {
    const { doc, disagreements } = await C.fetchPrices({ check: !opts['no-check'] });
    writeJsonAtomic(C.PRICES_LIVE, doc);
    log(`prices: ${Object.keys(doc.models).length} models from ${doc.source}`);
    if (disagreements.length) {
      log(paint(`\n${disagreements.length} model(s) where the cross-check disagrees by >1% — the primary value was kept:`, 'yel'));
      for (const d of disagreements.slice(0, 12)) log(`  ${d.id}: primary $${d.primary[0]}/$${d.primary[1]} vs check $${d.check[0]}/$${d.check[1]}`);
      log(paint('A disagreement usually means one source is quoting a batch or flex rate. Settle it against the', 'dim'));
      log(paint('provider\'s own pricing page and put the answer in ~/.claude/claude-usage/prices.override.json.', 'dim'));
    } else log(paint('cross-check agrees', 'dim'));
    return;
  }
  const prices = C.loadPrices();
  const { sessions } = C.scanAll({});
  const seen = new Set();
  for (const s2 of Object.values(sessions)) for (const m of Object.keys(s2.models)) seen.add(m);
  const rows = [...seen].sort().map((m) => {
    const r = C.rateFor(m, prices);
    const tier = r && (r.tiers || []).find((t) => t?.tier?.type === 'context');
    return [{ t: m.slice(0, 34) }, { t: r ? `$${r.input}` : '—', c: r ? null : 'yel' }, { t: r?.output != null ? `$${r.output}` : '—' },
      { t: r?.cacheRead != null ? `$${r.cacheRead}` : '—', c: r && r.cacheRead == null ? 'yel' : null },
      { t: r?.cacheWrite != null ? `$${r.cacheWrite}` : '—', c: r && r.cacheWrite == null ? 'yel' : null },
      { t: tier ? `>${Math.round(tier.tier.size / 1000)}k: $${tier.input}/$${tier.output}` : '', c: 'dim' },
      { t: r ? r.layer : 'UNPRICED', c: r ? 'dim' : 'yel' }];
  });
  table(['model (seen in transcripts)', 'in/M', 'out/M', 'cache rd', 'cache wr', 'long-context tier', 'source'], rows);
  const prov = Object.entries(prices.sources).map(([l, m]) => `${l} ${m.count}${m.at ? ` @ ${String(m.at).slice(0, 10)}` : ''}`).join(' · ');
  log(paint(`\n${prov} · claude-usage prices --refresh to update · override in ~/.claude/claude-usage/prices.override.json`, 'dim'));
}

// ---------- what it cost, in API-price equivalent ----------
// Claude Code's own figure prices an unrecognised gateway id with its fallback table (an Astra turn came back at
// roughly Opus-5 rates, about half Astra's real published price), so this recomputes from the raw token counts in the
// transcripts against a table we can audit and refresh. See cost.js for why the transcripts are the right source.
function cmdCost(opts = {}) {
  const C = require('./cost.js');
  const prices = C.loadPrices();
  const { sessions, read, skipped } = C.scanAll({ quiet: !opts.verbose });
  const group = opts.by || 'model';
  if (!['model', 'session', 'repo', 'day'].includes(group)) die('--by expects model, session, repo or day');
  const rootFilter = opts.roots ? String(opts.roots).split(',').map((x) => x.trim()).filter(Boolean) : null;
  const { rows, unpriced, rootScope, allocationCoverage } = C.summarize({ sessions, prices, since: opts.since || null, until: opts.until || null, group, cwdFilter: opts.repo || null, rootFilter });
  if (opts.json) {
    // Lineage travels with the rows: `parent` is derived from path nesting (the filesystem's own statement), NOT from
    // verified metadata, so a consumer attributing work to a campaign root should key on explicit ids, not on this.
    // The metadata describes exactly the sessions the rows cover. Emitting every session on the machine even under
    // --roots put the scope line and the session list in open disagreement, and a consumer that trusted the list
    // described work the report had deliberately excluded.
    // Two files can also carry the SAME session id — a repo move leaves one under the old project directory and one
    // under the new — so they are MERGED here. Keyed by id alone, whichever was written last erased the other: the
    // tokens stayed in the totals, the second transcript's existence did not.
    const inWindow = (s2) => (!opts.since && !opts.until)
      || Object.keys(s2.byDay || {}).some((d) => (!opts.since || d >= opts.since) && (!opts.until || d <= opts.until));
    const inScope = (s2) => (!rootScope || rootScope.selected.has(s2.file))
      && (!opts.repo || (s2.cwds || []).some((c) => C.underPath(c, opts.repo))) && inWindow(s2);
    const meta = {};
    for (const s2 of Object.values(sessions)) {
      if (!inScope(s2)) continue;
      const m = meta[s2.id] || (meta[s2.id] = { kind: s2.kind, parent: s2.parent, cwd: s2.cwd, cwds: [], first: null, last: null, file: s2.file, files: [] });
      m.files.push(s2.file);
      if (!m.cwd) m.cwd = s2.cwd;
      for (const c of s2.cwds || []) if (!m.cwds.includes(c)) m.cwds.push(c);
      if (s2.first && (!m.first || s2.first < m.first)) m.first = s2.first;
      if (s2.last && (!m.last || s2.last > m.last)) m.last = s2.last;
    }
    log(JSON.stringify({
      at: new Date().toISOString(), group, unpriced, lineageFrom: 'path-nesting (not metadata-verified)',
      ...C.fingerprint(prices),
      // The rates actually used, for the models actually in these rows, so a receipt can be recomputed later even
      // after the fetched or override table has moved on.
      effectiveRates: Object.fromEntries([...new Set(rows.flatMap((r) => r.models))].sort()
        .map((m) => { const r = C.rateFor(m, prices); return [m, r ? { input: r.input, output: r.output, cacheRead: r.cacheRead, cacheWrite: r.cacheWrite, cacheWrite1h: r.cacheWrite1h ?? (r.provider === 'anthropic' ? r.input * 2 : null), provider: r.provider, tiers: r.tiers || null, layer: r.layer, sourcedAt: r.sourcedAt } : null]; })),
      // `allocationCoverage` answers a question `partial` cannot: `partial` says whether what this scope WAS GIVEN
      // could be priced, while this says whether the scope was given everything its own sources contain. A message
      // shared with a source outside the selection is counted there, once, correctly — and missing from here.
      scope: { roots: rootFilter, resolvedRoots: rootScope?.resolved || null, unknownRoots: rootScope?.unknown || null,
        repo: opts.repo || null, repoSemantics: opts.repo ? 'sessions that touched the subtree, counted in full' : null,
        since: opts.since || null, until: opts.until || null, allocationCoverage },
      rows, sessions: meta,
    }, null, 2));
    return rows;
  }
  if (!rows.length) { log('nothing to price yet'); return rows; }

  const M = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)}G` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}k` : String(n));
  // One deep path would otherwise pad every row in the table out to its width.
  const shortKey = (g, k) => {
    const t = g === 'repo' ? String(k).replace(HOME, '~') : String(k);
    return t.length <= 40 ? t : (g === 'repo' ? `…${t.slice(-39)}` : `${t.slice(0, 39)}…`); // keep the tail of a path, the head of an id
  };
  const money = (r) => (r.usd ? `$${r.usd < 10 ? r.usd.toFixed(2) : Math.round(r.usd)}` : '—') + (r.partial ? '*' : '');
  const head = [group, 'turns', 'in', 'out', 'cache rd', 'cache wr', 'API-equivalent'];
  const body = rows.map((r) => [
    { t: shortKey(group, r.key) },
    { t: String(r.turns) }, { t: M(r.in) }, { t: M(r.out) }, { t: M(r.cacheRead) }, { t: M(r.cacheWrite) },
    { t: money(r), c: r.partial ? 'yel' : null },
  ]);
  const total = rows.reduce((a, r) => ({ turns: a.turns + r.turns, in: a.in + r.in, out: a.out + r.out, cacheRead: a.cacheRead + r.cacheRead, cacheWrite: a.cacheWrite + r.cacheWrite, usd: a.usd + r.usd, partial: a.partial || r.partial }), { turns: 0, in: 0, out: 0, cacheRead: 0, cacheWrite: 0, usd: 0, partial: false });
  table(head, [...body, [{ t: 'TOTAL', c: 'bold' }, { t: String(total.turns), c: 'bold' }, { t: M(total.in), c: 'bold' }, { t: M(total.out), c: 'bold' }, { t: M(total.cacheRead), c: 'bold' }, { t: M(total.cacheWrite), c: 'bold' }, { t: money(total), c: 'bold' }]]);

  const prov = Object.entries(prices.sources).map(([layer, m]) => `${layer} ${m.count} models${m.at ? ` @ ${String(m.at).slice(0, 10)}` : ''}`).join(' · ') || 'no price table';
  log(paint(`\nprices: ${prov} · claude-usage prices --refresh updates them`, 'dim'));
  if (rootScope?.unknown?.length) log(paint(`\n${rootScope.unknown.length} requested root(s) matched no transcript: ${rootScope.unknown.join(', ')}`, 'yel'));
  if (opts.repo) log(paint(`scope: sessions that TOUCHED ${String(opts.repo).replace(HOME, '~')} (whole sessions, not a per-turn allocation — a session that moved between repos is counted in full)`, 'dim'));
  // Loud on purpose: this figure is NOT complete for what was asked, and no `*` marker would have said so — the rows
  // priced everything they were given, and what they were given was short.
  if (allocationCoverage && !allocationCoverage.complete) {
    log(paint(`\n! this subtotal is NOT COMPLETE for the requested scope: ${allocationCoverage.allocatedElsewhereIds} shared message id(s) that the selected sources contain are allocated to a source OUTSIDE the selected scope, and are counted there instead (once, corpus-wide).`, 'yel'));
    log(paint('  Source allocation says which transcript a turn is counted under — never which agent produced it. Widen the selection to include the owning source if you need those turns here.', 'dim'));
  }
  const tiered = rows.reduce((a, r) => a + (r.tieredTurns || 0), 0);
  if (tiered) log(paint(`${tiered} turn(s) priced at a long-context tier (a prompt over the provider's threshold costs about double)`, 'dim'));
  // Why the figure is incomplete is not one story. A missing rate means the number sits BELOW the truth; two counts
  // contradicting each other means we refused to guess in either direction. Announcing a missing rate whatever the
  // cause was not just vague but false — it once described a 2x OVER-charge as a floor.
  if (total.partial) {
    const causes = new Set(rows.flatMap((r) => r.partialReasons || []));
    const withheld = rows.reduce((a, r) => a + (r.withheldTurns || 0), 0);
    log(paint('* incomplete: this is the KNOWN PRICED SUBTOTAL, not the bill —', 'yel'));
    if (causes.has('rate-missing')) log(paint('  · a model or a cache rate is missing from the price table, so those tokens are left out and the subtotal sits BELOW the truth', 'yel'));
    if (causes.has('unknown-ttl')) log(paint('  · some cache writes do not say which TTL they used, and a 1-hour write costs 60% more than a 5-minute one, so they are not priced at a guess', 'yel'));
    if (causes.has('count-conflict')) log(paint(`  · ${withheld} turn(s) left unpriced because their counts contradict each other: a flat cache-write total disagrees with that same turn's per-TTL split, so neither quantity can be billed`, 'yel'));
  }
  if (unpriced.length) log(paint(`unpriced models: ${unpriced.join(', ')}`, 'yel'));
  log(paint(`${read} transcripts read, ${skipped} unchanged (cache: ~/.claude/claude-usage/cost-cache.json)`, 'dim'));
  return rows;
}

// ---------- adding a ChatGPT account ----------
// The login runs against an ISOLATED config root (CCP_CONFIG_DIR replaces the whole tree, src/paths.rs:24 — upstream
// added it so "separate proxy configs can keep separate logins"), so a login that is still in progress, or abandoned,
// can never overwrite the account the gateway is serving right now.
const PROXY_BIN = path.join(HOME, '.claude', 'model-gateway', 'bin', 'claude-code-proxy');
async function cmdAddGpt(name) {
  if (!name || !/^[\w.-]+$/.test(name)) die('usage: claude-usage add-gpt <name>   (letters, digits, dot, dash, underscore)');
  if (codexNames().includes(name)) die(`${name} already exists — claude-usage remove-gpt ${name} first, or pick another name`);
  if (names().includes(name)) die(`${name} is already a Claude account — names are one namespace, pick another`);
  if (!fs.existsSync(PROXY_BIN)) die(`no gateway proxy at ${PROXY_BIN} — install the model-gateway plugin first`);
  const tmpRoot = fs.mkdtempSync(path.join(ROOT, 'addgpt-'));
  try {
    log(`signing in to ChatGPT for "${name}" — open the printed URL and enter the code. The account the gateway is on now is untouched.`);
    const r = spawnSync(PROXY_BIN, ['codex', 'auth', 'device'], { stdio: 'inherit', env: { ...process.env, CCP_CONFIG_DIR: tmpRoot } });
    if (r.status !== 0) die('the ChatGPT device login did not complete');
    const got = path.join(tmpRoot, 'codex', 'auth.json');
    let auth = null; try { auth = readJson(got); } catch { /* reported below */ }
    if (!auth?.access || !auth?.refresh) die('the login left no credential behind');
    // Identify it before storing, so a second login of the SAME account is caught instead of silently shadowing the first.
    const probe = { name, access: auth.access, accountId: auth.accountId };
    const u = await getCodexUsage(probe);
    if (u.status !== 200) die(`signed in, but the usage endpoint answered ${u.status} — not storing a credential we cannot read`);
    const email = u.data?.email || '?', plan = codexPlanLabel(u.data?.plan_type);
    for (const other of codexNames()) {
      const o = loadCodexAccount(other);
      if (o.accountId && auth.accountId && o.accountId === auth.accountId) die(`that is the same ChatGPT account as "${other}" (${o.email}) — nothing stored`);
    }
    fs.mkdirSync(codexDir(name), { recursive: true, mode: 0o700 });
    writeJsonAtomic(codexAuthFile(name), auth);
    saveCodexMeta(name, { email, plan, addedAt: new Date().toISOString(), dead: null });
    log(`stored ${name}: ${email} (${plan})`);
    if (!codexActive()) { await switchCodex(name); log(`and made it active (nothing was)`); }
    else log(`active account is still ${codexActive()} — \`claude-usage switch ${name}\` moves the gateway to this one`);
  } finally { fs.rmSync(tmpRoot, { recursive: true, force: true }); }
}
// Lower priority = preferred. A deprioritised account is taken only when no preferred one has room, and the alert says so.
function cmdPriority(name, n) {
  const all = [...names().map((x) => loadAccount(x)), ...codexNames().map((x) => loadCodexAccount(x))];
  if (!name) {
    for (const a of all) log(`${String(a.priority || 0)}  ${a.name}${(a.priority || 0) > 0 ? '  (last resort)' : ''}`);
    log('\nlower is preferred; `claude-usage priority <name> 1` makes an account a last resort');
    return;
  }
  const a = all.find((x) => x.name === name);
  if (!a) die(`no account named ${name}`);
  if (n === undefined) { log(`${name} priority ${a.priority || 0}`); return; }
  const v = Number(n);
  if (!Number.isInteger(v) || v < 0 || v > 9) die('priority must be an integer 0-9 (0 = preferred)');
  if (a.provider === 'openai') saveCodexMeta(name, { priority: v });
  else { const rec = loadAccount(name); rec.priority = v; saveAccount(rec); }
  log(`${name} priority ${v}${v > 0 ? ' (last resort: taken only when nothing preferred has room)' : ''}`);
}
function cmdRemoveGpt(name) {
  if (!codexNames().includes(name)) die(`no ChatGPT account named ${name}`);
  if (name === codexActive()) die(`${name} is the account the gateway is serving — claude-usage switch <other> first`);
  fs.rmSync(codexDir(name), { recursive: true, force: true });
  log(`removed ${name}`);
}

// ---------- which terminal hit the wall ----------
// An alert that says "the weekly limit is spent" leaves Louis hunting through a dozen panes for the session that spent
// it. The session id is already in the evidence — a transcript path on the Claude side, a request-route line on the
// Codex one — so the pane is recoverable: find the process holding that transcript, walk its parents until a pid
// matches a tmux pane, and name it. Everything here is best-effort and returns null rather than throwing: attribution
// is a nicety, and it must never be the reason an alert does not go out.
const REQUEST_ROUTE_LOG = path.join(HOME, '.claude', 'model-gateway', 'logs', 'request-routes.jsonl');
function tmuxPanes() {
  try {
    const r = spawnSync('tmux', ['list-panes', '-a', '-F', '#{pane_pid}\t#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_path}\t#{window_name}'], { encoding: 'utf8', timeout: 5000 });
    if (r.status !== 0 || !r.stdout) return [];
    return r.stdout.trim().split('\n').filter(Boolean).map((l) => { const [pid, addr, cwd, win] = l.split('\t'); return { pid: Number(pid), addr, cwd, win }; });
  } catch { return []; }
}
function ppidOf(pid) {
  // /proc/<pid>/stat field 4, read after the last ')' because a process name can contain spaces and parentheses.
  try { const st = fs.readFileSync(`/proc/${pid}/stat`, 'utf8'); return Number(st.slice(st.lastIndexOf(')') + 2).split(' ')[1]) || 0; }
  catch { return 0; }
}
function paneForPid(pid, panes, ppid = ppidOf) {
  const byPid = new Map(panes.map((p) => [p.pid, p]));
  let cur = Number(pid) || 0;
  for (let hops = 0; hops < 40 && cur > 1; hops++) { if (byPid.has(cur)) return byPid.get(cur); cur = ppid(cur); }
  return null;
}
// Claude Code appends to a transcript and closes it, so no process holds one open — checked on the live machine, the
// /proc/*/fd route finds nothing and is not worth the scan. What DOES exist: every session's transcript records its
// `cwd`, every pane's claude process exposes its own cwd, and a resumed session carries its id in argv. So argv is the
// exact signal when present, and cwd narrows to the pane (or the two or three panes) sitting in that repo.
function claudeProcs() {
  const out = [];
  let dirs = [];
  try { dirs = fs.readdirSync('/proc').filter((d) => /^\d+$/.test(d)); } catch { return out; }
  for (const d of dirs) {
    let cmd = '';
    try { cmd = fs.readFileSync(`/proc/${d}/cmdline`, 'utf8').replace(/\0/g, ' ').trim(); } catch { continue; } // gone, or not ours
    if (!cmd.includes('claude')) continue;
    let cwd = null; try { cwd = fs.readlinkSync(`/proc/${d}/cwd`); } catch { /* another uid */ }
    out.push({ pid: Number(d), cmd, cwd });
  }
  return out;
}
const tildify = (p) => (p && p.startsWith(HOME) ? `~${p.slice(HOME.length)}` : p || '');
// Pure, so the matching is testable without a machine: panes and procs are injected.
function attribute({ sessionId = null, cwd = null, panes = [], procs = [], ppid = ppidOf } = {}) {
  if (!panes.length) return null;
  const named = (pane) => `tmux ${pane.addr}${pane.win ? ` [${pane.win}]` : ''}`;
  if (sessionId) {
    for (const pr of procs) {
      if (!pr.cmd.includes(sessionId)) continue;
      const pane = paneForPid(pr.pid, panes, ppid);
      if (pane) return `${named(pane)} ${tildify(pr.cwd || pane.cwd)}`; // argv carries the id: exact
    }
  }
  if (!cwd) return null;
  const hits = [];
  for (const pr of procs) {
    if (pr.cwd !== cwd) continue;
    const pane = paneForPid(pr.pid, panes, ppid);
    if (pane && !hits.some((h) => h.addr === pane.addr)) hits.push(pane);
  }
  if (hits.length === 1) return `${named(hits[0])} ${tildify(cwd)}`;
  if (hits.length) return `one of tmux ${hits.map((h) => h.addr).join(', ')} (${tildify(cwd)})`; // several panes in that repo
  return null;
}
// The transcript a session id belongs to, wherever Claude Code filed it.
function transcriptFor(sessionId) {
  if (!sessionId) return null;
  const root = path.join(CFG, 'projects');
  try {
    for (const d of fs.readdirSync(root)) {
      const f = path.join(root, d, `${sessionId}.jsonl`);
      if (fs.existsSync(f)) return f;
    }
  } catch { /* none */ }
  return null;
}
const cwdOfTranscript = (f) => { try { for (const l of fs.readFileSync(f, 'utf8').split('\n', 40)) { const c = (() => { try { return JSON.parse(l).cwd; } catch { return null; } })(); if (c) return c; } } catch { /* none */ } return null; };
function whereFromSession(sessionId) {
  try {
    const transcript = transcriptFor(sessionId);
    return attribute({ sessionId, cwd: transcript && cwdOfTranscript(transcript), panes: tmuxPanes(), procs: claudeProcs() });
  } catch { return null; } // attribution is a nicety and must never be why an alert does not go out
}
// The last Codex request the gateway logged at or before the rejection is the session that hit the wall.
function codexCulprit(blocked) {
  try {
    const cutoff = (Date.parse(blocked?.observedAt || '') || Date.now()) + 2000;
    const lines = fs.readFileSync(REQUEST_ROUTE_LOG, 'utf8').trim().split('\n').slice(-800);
    let best = null;
    for (const l of lines) {
      let d = null; try { d = JSON.parse(l); } catch { continue; }
      if (d?.backend !== 'codex' || !d.sessionId) continue;
      if ((Date.parse(d.at || '') || 0) <= cutoff) best = d;
    }
    return best?.sessionId || null;
  } catch { return null; }
}
function readCodexBlocked() { try { return readJson(CODEX_BLOCKED); } catch { return null; } }

// ---------- one watcher iteration ----------
// One clause per account for the exhausted alert: the soonest reset among the windows that are actually spent, each
// under its own bound (5-hour: sessionHot, weekly: switchAt, Fable: fableCeiling), else its worst percentage.
function resetsLine(rows, t) {
  const sessionHot = t.sessionHot ?? THRESHOLDS.sessionHot, fableCeiling = t.fableCeiling ?? THRESHOLDS.fableCeiling;
  return rows.filter((r) => r.usage).map((r) => {
    const u = r.usage;
    const fable = (u.scoped || []).find((s) => s.label === 'Fable');
    const spent = [[u.session, sessionHot], [u.weekly, t.switchAt], [fable, fableCeiling]].filter(([w, bound]) => w && pctOf(w) !== null && pctOf(w) >= bound);
    const soon = spent.map(([w]) => w.resetsAt).filter(Boolean).sort()[0];
    const worst = worstOf(u);
    return soon ? `${r.name} resets in ${until(soon)}` : `${r.name} ${worst === null ? 'unreadable' : `${worst}%`}`;
  }).join(', ');
}
// exhaustedSince/switchFailedSince/tickFailedSince are three independent episodes (both accounts tapped out, a switch that
// keeps failing, this function itself throwing); whichever ones this iteration proves over, it clears — never left stale
// by a branch that had nothing to do with them.
async function tick() {
  let rows = await collect({ includeLive: true }); // also writes meter.json for the status line
  const s = state(); const t = thresholds(); const at = new Date().toISOString();
  // A limit refuses every request on the account at once, so a refusal written since the last look is blamed on whoever
  // is live now. `at` is taken before the scan, so a refusal written while it runs is caught by the next one, never lost.
  const limited = Object.fromEntries(Object.entries(s.limited || {}).filter(([, v]) => Date.now() - Date.parse(v) < LIMIT_COOLDOWN_MS));
  // …but never one written before that account was checked out: it belongs to whatever was live then.
  let liveSince = 0; try { liveSince = Date.parse(loadAccount(s.live).checkedOut) || 0; } catch { /* no record: the scan window alone */ }
  const ev = s.live ? limitRefusals({ since: Math.max(Date.parse(s.refusalScanAt || '') || Date.now() - 5 * 60e3, liveSince) }) : null;
  if (ev) limited[s.live] = ev.at;
  setState({ refusalScanAt: at, limited });
  const blind = s.blind || 0;
  const blocked = readCodexBlocked();
  const ask = () => decide({ rows, live: s.live, thresholds: t, exhaustedSince: s.exhaustedSince || null, blind, limited, lastSwitchAt: s.lastSwitchAt || 0 });
  const askCodex = () => decideCodex({ rows, active: codexActive(), thresholds: t, blocked, exhaustedSince: s.codexExhaustedSince || null });
  let d = ask(); let cd = askCodex();
  // Moving the machine is the one decision worth spending requests on: re-read everything and ask again, so a switch is
  // never made on a cached number. A 429 here costs nothing — collect falls back to the same reading we just decided on.
  // One forced re-read serves both domains: it is the same call, and the Anthropic meter has a budget to respect.
  if (d.action === 'failover' || d.action === 'rebalance' || cd.action === 'failover') {
    rows = await collect({ includeLive: true, force: true });
    d = ask(); cd = askCodex();
  }
  // A readable live row, or a switch that landed, ends the blind streak; anything else unreadable extends it.
  const blindNext = d.liveUnreadable ? blind + 1 : 0;
  if (d.action === 'failover' || d.action === 'rebalance') {
    try {
      const r = await switchTo(d.to, { failover: true }); // stamps lastSwitchAt itself
      markMeterLive(r.to); // meter.json still carries the pre-switch flags; the status line would mark the old account ● until the next tick
      // A successful move is the normal course of business now — proactive or forced — and is logged, never paged
      // (Louis, 2026-09-21: page on harm, not on change). The line carries what the page used to, so the log is the record.
      logLine(`${d.action} ${r.from} → ${r.to} · ${d.reason}${d.warn ? ` · ⚠ ${d.warn}` : ''}`);
      setState({ lastDecision: { at, ...d }, exhaustedSince: null, switchFailedSince: null, tickFailedSince: null, blind: 0 });
    } catch (e) {
      if (!s.switchFailedSince) await notify(`${d.action} to ${d.to} FAILED: ${e.message}`);
      setState({ lastDecision: { at, ...d, error: e.message }, switchFailedSince: s.switchFailedSince || at, exhaustedSince: null, tickFailedSince: null, blind: blindNext });
    }
  } else if (d.action === 'exhausted') {
    if (d.notify) {
      const from = ev?.file ? whereFromSession(path.basename(ev.file, '.jsonl')) : null;
      await notify(`no account can take the machine — ${d.reason} · ${resetsLine(rows, t)}; no switch${from ? ` · last refusal from ${from}` : ''}`);
      setState({ exhaustedSince: at });
    }
    setState({ lastDecision: { at, ...d }, switchFailedSince: null, tickFailedSince: null, blind: blindNext });
  } else {
    setState({ lastDecision: { at, ...d }, exhaustedSince: null, switchFailedSince: null, tickFailedSince: null, blind: blindNext });
  }
  await actOnCodex({ cd, at, s, t, blocked });
  log(`${at} ${d.action}: ${d.reason}${cd.action === 'none' ? '' : ` · codex ${cd.action}: ${cd.reason}`}`);
}
// The ChatGPT half of a tick. Separate function, separate state keys, and it never touches a Claude credential: the
// two domains are not substitutes, so a spent ChatGPT account can only ever move to another ChatGPT account.
async function actOnCodex({ cd, at, s, t, blocked }) {
  if (cd.action === 'failover') {
    try {
      const r = await switchCodex(cd.to);
      const from = whereFromSession(codexCulprit(blocked));
      await notify(`gateway ChatGPT ${r.from || 'none'} → ${r.to} · ${cd.reason}${cd.warn ? ` · ⚠ ${cd.warn}` : ''}${from ? ` · spent by ${from}` : ''}`);
      setState({ codexLastDecision: { at, ...cd }, codexExhaustedSince: null });
    } catch (e) {
      await notify(`gateway ChatGPT switch to ${cd.to} FAILED: ${e.message}`);
      setState({ codexLastDecision: { at, ...cd, error: e.message } });
    }
    return;
  }
  if (cd.action === 'exhausted') {
    if (cd.notify) {
      const from = whereFromSession(codexCulprit(blocked));
      await notify(`every ChatGPT account is spent — switch the session to /model ${cd.suggest} · ${cd.reason}${from ? ` · spent by ${from}` : ''}`);
      setState({ codexExhaustedSince: at });
    }
    setState({ codexLastDecision: { at, ...cd } });
    return;
  }
  setState({ codexLastDecision: { at, ...cd }, codexExhaustedSince: null });
}
// The two flags the status line reads, refreshed in place after a switch: no network, and a failed write costs nothing
// because the next tick rewrites meter.json whole.
function markMeterLive(to) {
  try { const m = readJson(METER); const h = state().home; for (const [n, r] of Object.entries(m.accounts)) { r.checkedOut = n === to; r.home = n === h; } writeJsonAtomic(METER, m); } catch { /* best effort */ }
}
// The watcher timer calls this directly: nothing above may ever throw out of here, or one bad iteration reddens the unit.
async function cmdTick() {
  try { return await tick(); }
  catch (e) {
    const msg = e?.message ?? String(e); // e itself could be nullish; reading .message straight from a catch arg must never re-throw
    const at = new Date().toISOString(), s = state();
    if (!s.tickFailedSince) await notify(`tick FAILED: ${msg}`).catch(() => {});
    try { setState({ lastDecision: { at, action: 'error', reason: msg }, tickFailedSince: s.tickFailedSince || at }); } catch { /* state unwritable: the log line is the trace */ }
    logLine(`tick failed: ${msg}`);
  }
}

const die = (m) => { console.error(`claude-usage: ${m}`); process.exit(1); };
const log = (m = '') => console.log(m);
class UsageError extends Error { constructor(m, kind) { super(m); this.kind = kind; } }

// ---------- files ----------
function readJson(f) { return JSON.parse(fs.readFileSync(f, 'utf8')); }
function writeJsonAtomic(file, obj, mode = 0o600) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2) + '\n', { mode });
  fs.renameSync(tmp, file); fs.chmodSync(file, mode);
}
function state() { try { return readJson(STATE); } catch { return {}; } }
function setState(p) { writeJsonAtomic(STATE, { ...state(), ...p }); }
function acctFile(n) { return path.join(ACCOUNTS, `${n}.json`); }
function names() { try { return fs.readdirSync(ACCOUNTS).filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5)).sort(); } catch { return []; } }
function loadAccount(n) { const a = readJson(acctFile(n)); a.name = n; return a; }
function saveAccount(a) { const { name, ...rest } = a; writeJsonAtomic(acctFile(name), rest); }
function h8(s) { return crypto.createHash('sha256').update(s).digest('hex').slice(0, 8); }
function security(args) {
  const r = spawnSync('/usr/bin/security', args, { encoding: 'utf8', timeout: 20000 });
  return { ok: r.status === 0, out: (r.stdout || '').trim(), err: (r.stderr || '').trim() };
}

// ---------- claude version → User-Agent (the token endpoint rate-limits unknown agents) ----------
function claudeVersion() {
  for (const c of [path.join(HOME, '.local', 'bin', 'claude'), '/opt/homebrew/bin/claude', '/usr/local/bin/claude']) {
    try { const m = fs.realpathSync(c).match(/(\d+\.\d+\.\d+)/); if (m) return m[1]; } catch { /* next */ }
  }
  try { const r = spawnSync('claude', ['--version'], { encoding: 'utf8', timeout: 15000 }); const m = (r.stdout || '').match(/(\d+\.\d+\.\d+)/); if (m) return m[1]; } catch { /* none */ }
  return '2.1.270';
}
let UA = null;
function ua() { return UA || (UA = `claude-cli/${claudeVersion()} (external, cli)`); }

async function httpImpl(method, url, { headers = {}, body, timeout = 25000 } = {}) {
  const res = await fetch(url, { method, headers: { 'User-Agent': ua(), ...headers }, body, signal: AbortSignal.timeout(timeout) });
  const text = await res.text();
  let data = null; try { data = JSON.parse(text); } catch { /* not json */ }
  return { status: res.status, data, text };
}
const net = { http: httpImpl }; // tests replace net.http; every outbound call goes through it

// ---------- tokens ----------
// One lock per parked grant, taken here across the POST and the save, and by `switch` across park+install. Without it a
// refresh that started before a switch POSTs a refresh token the switch has already rotated, gets a 400, finds no token
// on disk (the switch stripped the record) and writes its own stale in-memory copy back with `dead` — resurrecting a
// revoked pair over a healthy checked-out record, and blinding the watcher. `locked` is switchTo's own call: it holds it.
const acctLock = (name) => path.join(ROOT, `refresh-${name}.lock`);
async function refresh(acct, { locked = false } = {}) {
  const release = locked ? () => {} : takeLock(acctLock(acct.name), ACCT_LOCK_STALE, LOCK_WAIT.account);
  try { return await refreshUnderLock(acct, locked); } finally { release(); }
}
// `add <name> --force` is the remedy for a PARKED grant only: run it on the checked-out one and it parks a brand-new grant
// and drops the live browser login on the floor. The record on disk, not our in-memory copy, says which case this is.
const deadRemedy = (name) => { let d = null; try { d = readJson(acctFile(name)); } catch { /* removed under us */ } return d?.checkedOut ? `it is checked out: claude-usage switch <other> first, then claude-usage add ${name} --force` : `re-add: claude-usage add ${name} --force`; };
async function refreshUnderLock(acct, locked) {
  if (!locked) { // under the lock, the record on disk is the truth: a switch may have moved this grant while we waited for it
    let disk = null; try { disk = readJson(acctFile(acct.name)); } catch { /* removed under us */ }
    if (disk && (disk.checkedOut || !(disk.accessToken && disk.refreshToken))) { stripTokens(acct); delete acct.dead; Object.assign(acct, disk); return acct; } // checked out: Claude Code owns and refreshes it now, we must not post its old token
    if (disk?.refreshToken && disk.refreshToken !== acct.refreshToken) { Object.assign(acct, disk); delete acct.dead; return acct; }                            // a sibling already rotated it: adopt, never fight
  }
  const posted = acct.refreshToken; // the client's own compare-and-swap: if a sibling refresh already moved the record past
  // this token, adopt its pair instead of fighting it — never mark dead over a refresh token that is simply stale on our side.
  const sibling = () => {
    let disk = null; try { disk = readJson(acctFile(acct.name)); } catch { /* none: account removed under us */ }
    if (disk?.refreshToken && disk.refreshToken !== posted) { Object.assign(acct, disk); delete acct.dead; return true; }
    return false;
  };
  const r = await net.http('POST', TOKEN_URL, { headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ grant_type: 'refresh_token', refresh_token: acct.refreshToken, client_id: CLIENT_ID, scope: (acct.scopes || DEFAULT_SCOPES).join(' ') }) });
  if (r.status === 429) throw new UsageError('token endpoint rate-limited; retry in a minute', 'rate_limited');
  if (r.status !== 200) {
    const code = r.data?.error?.type || r.data?.error || r.status;
    if (r.status === 400 || r.status === 401) { if (sibling()) return acct; acct.dead = `refresh rejected (${code}) — ${deadRemedy(acct.name)}`; saveAccount(acct); }
    throw new UsageError(`refresh failed HTTP ${r.status} (${code})`, 'refresh_failed');
  }
  const t = r.data;
  if (!t.access_token) throw new UsageError('token response had no access_token', 'refresh_failed');
  if (sibling()) return acct;
  Object.assign(acct, {
    accessToken: t.access_token,
    refreshToken: t.refresh_token || acct.refreshToken,
    expiresAt: Date.now() + Number(t.expires_in || 28800) * 1000,
    refreshTokenExpiresAt: t.refresh_token_expires_in ? Date.now() + Number(t.refresh_token_expires_in) * 1000 : acct.refreshTokenExpiresAt,
    refreshedAt: new Date().toISOString(),
  });
  delete acct.dead;
  if (typeof t.scope === 'string' && t.scope) acct.scopes = t.scope.split(' ');
  const email = t.account?.email || t.account?.email_address; if (email) acct.email = email;
  if (t.organization?.rate_limit_tier) acct.tier = t.organization.rate_limit_tier;
  saveAccount(acct); // saved before anything else: the old refresh token is already revoked server-side
  return acct;
}

async function getUsage(token) {
  return net.http('GET', `${API}/api/oauth/usage`, { headers: { Authorization: `Bearer ${token}`, 'anthropic-beta': BETA } });
}

async function usageFor(acct) {
  if (acct.live || isCheckedOut(acct)) {
    if (inTransition(acct)) throw new UsageError('in transition (a switch was interrupted) — claude-usage doctor --fix', 'transition');
    const token = acct.live ? acct.accessToken : liveStoreRead()?.claudeAiOauth?.accessToken;
    if (!token) throw new UsageError('no live login', 'none');
    const r = await getUsage(token);
    if (r.status === 401) throw new UsageError(acct.live ? 'live token expired — a running claude session refreshes it' : 'idle — live token expired, a session refreshes it', 'idle');
    if (r.status === 429) throw new UsageError('usage endpoint rate-limited', 'rate_limited');
    if (r.status !== 200) throw new UsageError(`usage HTTP ${r.status}: ${(r.data?.error?.message || r.text).slice(0, 80)}`, 'http');
    return parseUsage(r.data);
  }
  if (acct.dead) throw new UsageError(acct.dead, 'dead');
  // refresh() may have adopted a record that a switch checked out while we waited for its lock: meter it live instead of
  // reaching for tokens it no longer has.
  if (!acct.accessToken || (acct.expiresAt || 0) - Date.now() < REFRESH_AHEAD_MS) { await refresh(acct); if (isCheckedOut(acct)) return usageFor(acct); }
  let r = await getUsage(acct.accessToken);
  if (r.status === 401) { await refresh(acct); if (isCheckedOut(acct)) return usageFor(acct); r = await getUsage(acct.accessToken); }
  if (r.status === 429) throw new UsageError('usage endpoint rate-limited', 'rate_limited');
  if (r.status !== 200) throw new UsageError(`usage HTTP ${r.status}: ${(r.data?.error?.message || r.text).slice(0, 80)}`, 'http');
  return parseUsage(r.data);
}

function sev(p) { return p >= 90 ? 'critical' : p >= 75 ? 'warning' : 'normal'; }
function parseUsage(d) {
  const lim = Array.isArray(d.limits) ? d.limits : [];
  const win = (o) => (o && typeof o.utilization === 'number') ? { percent: Math.round(o.utilization), resetsAt: o.resets_at || null, severity: sev(o.utilization), locked: o.locked_reason || null } : null;
  const fromLim = (k) => { const l = lim.find((x) => x.kind === k); return l ? { percent: Number(l.percent) || 0, resetsAt: l.resets_at || null, severity: l.severity || sev(l.percent), locked: null } : null; };
  const session = fromLim('session') || win(d.five_hour);
  const weekly = fromLim('weekly_all') || win(d.seven_day);
  if (session && d.five_hour?.locked_reason) session.locked = d.five_hour.locked_reason;
  if (weekly && d.seven_day?.locked_reason) weekly.locked = d.seven_day.locked_reason;
  const scoped = lim.filter((x) => x.kind === 'weekly_scoped').map((l) => ({ label: l.scope?.model?.display_name || l.scope?.surface || 'scoped', percent: Number(l.percent) || 0, resetsAt: l.resets_at || null, severity: l.severity || sev(l.percent) }));
  if (!lim.length) for (const [k, label] of [['seven_day_opus', 'Opus'], ['seven_day_sonnet', 'Sonnet']]) { const w = win(d[k]); if (w) scoped.push({ label, ...w }); }
  const others = lim.filter((x) => !['session', 'weekly_all', 'weekly_scoped'].includes(x.kind)).map((l) => ({ label: l.kind, percent: Number(l.percent) || 0, resetsAt: l.resets_at || null, severity: l.severity || sev(l.percent) }));
  const spend = d.spend || {};
  const extra = { enabled: !!d.extra_usage?.is_enabled, percent: spend.percent ?? (typeof d.extra_usage?.utilization === 'number' ? Math.round(d.extra_usage.utilization) : null),
    currency: spend.limit?.currency || d.extra_usage?.currency || null, usedMinor: spend.used?.amount_minor ?? null, limitMinor: spend.limit?.amount_minor ?? null, exponent: spend.limit?.exponent ?? 2 };
  return { session, weekly, scoped: [...scoped, ...others], extra, breakdown: d.seven_day_breakdown?.rows || null, at: Date.now() };
}

// ---------- the ChatGPT (Codex) accounts behind the local model gateway ----------
// A different animal from a Claude account, in two ways that decide the whole design.
//
// 1. The proxy re-reads its credential on EVERY upstream call (claude-code-proxy 0.1.40,
//    providers/codex/auth/manager.rs:52-80 → auth.rs:288-293; the manager holds no credential field), so switching
//    account is instant and needs no restart.
// 2. A stale credential is DESTROYED, not rejected: the refresh token rotates on every use, and on a 401/403 from the
//    token endpoint the proxy calls clear_auth(), which deletes auth.json (manager.rs:125-137, auth.rs:183-192).
//
// So we never copy a credential — we point at it. Each account owns a directory with its own auth.json, and the path
// the proxy opens is a symlink to whichever is active. It must be the DIRECTORY: the proxy saves a rotated credential
// as <path>.tmp-<uuid> renamed over auth.json (auth.rs:338-355), which would replace a file-level symlink with a real
// file on the first refresh and silently pin one account forever.
const CODEX_STORE = path.join(ROOT, 'codex');                                   // one directory per account
const CODEX_LIVE = path.join(HOME, '.config', 'claude-code-proxy', 'codex');    // the symlink the proxy opens
const CODEX_BLOCKED = path.join(HOME, '.claude', 'model-gateway', 'codex-upstream-blocked.json');
const CODEX_USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage';
const CODEX_TOKEN_URL = 'https://auth.openai.com/oauth/token';
const CODEX_CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann';                          // the Codex CLI's own client
const CODEX_BLOCKED_COOLDOWN_MS = 60 * 60e3;                                     // the blocked file is sticky; age it out like a refusal
const CODEX_FIVE_HOUR_S = 5 * 3600, CODEX_WEEK_S = 7 * 24 * 3600;
// Used ONLY in the alert when every ChatGPT account is spent: which Claude model sits at the tier Louis was using.
// Nothing rewrites a request — the gateway serves what was asked for or fails, and the substitution is Louis's to make.
const CODEX_TIER_FALLBACK = { 'gpt-6-astra': 'claude-fable-5-1', 'gpt-5.6-sol': 'claude-opus-5', 'gpt-5.6-terra': 'claude-sonnet-5', 'gpt-5.6-luna': 'claude-haiku-4-5' };

const codexDir = (name) => path.join(CODEX_STORE, name);
const codexAuthFile = (name) => path.join(codexDir(name), 'auth.json');
const codexMetaFile = (name) => path.join(codexDir(name), 'meta.json'); // ours; the proxy only ever touches auth.json
function codexNames() {
  try { return fs.readdirSync(CODEX_STORE, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name).sort(); }
  catch { return []; }
}
// Which account the gateway is on: read the link, never trust a stored copy of the answer.
function codexActive() {
  try {
    const target = fs.readlinkSync(CODEX_LIVE);
    const name = path.basename(path.resolve(path.dirname(CODEX_LIVE), target));
    return codexNames().includes(name) ? name : null;
  } catch { return null; } // not a symlink (un-migrated real directory), or absent
}
function loadCodexAccount(name) {
  let auth = {}; try { auth = readJson(codexAuthFile(name)); } catch { /* no credential yet */ }
  let meta = {}; try { meta = readJson(codexMetaFile(name)); } catch { /* never metered yet */ }
  return { name, provider: 'openai', email: meta.email || '?', plan: meta.plan || '?', dead: meta.dead || null, priority: meta.priority || 0,
    access: auth.access || null, refresh: auth.refresh || null, expires: auth.expires || 0, accountId: auth.accountId || null };
}
function saveCodexMeta(name, patch) {
  let meta = {}; try { meta = readJson(codexMetaFile(name)); } catch { /* first write */ }
  writeJsonAtomic(codexMetaFile(name), { ...meta, ...patch });
}
const codexRefreshLock = (name) => path.join(ROOT, `codex-refresh-${name}.lock`);
const CODEX_SWITCH_LOCK = path.join(ROOT, 'codex-switch.lock');
// No `originator: codex_cli_rs` and no Codex user-agent: the endpoint answers 200 without them (verified 2026-09-15),
// so this reads a meter without adding to the client fingerprinting the gateway already does.
async function getCodexUsage(acct) {
  return net.http('GET', CODEX_USAGE_URL, { headers: { Authorization: `Bearer ${acct.access}`, 'chatgpt-account-id': acct.accountId || '', Accept: 'application/json' } });
}
function codexPlanLabel(p) {
  const known = { prolite: 'Pro Lite', pro: 'Pro', plus: 'Plus', team: 'Team', business: 'Business', enterprise: 'Enterprise', free: 'Free' };
  return known[p] || (p ? p[0].toUpperCase() + p.slice(1) : '?');
}
function codexWindow(w) {
  if (!w || typeof w.used_percent !== 'number') return null;
  const percent = Math.round(w.used_percent);
  // reset_at is epoch SECONDS here, where the Anthropic meter gives an ISO string; until() parses the string, so convert.
  return { percent, resetsAt: Number.isFinite(w.reset_at) ? new Date(w.reset_at * 1000).toISOString() : null, severity: sev(percent), locked: null, seconds: w.limit_window_seconds ?? null };
}
// Which windows exist depends on the plan: on prolite the ONLY top-level window is the weekly one, and a plan with both
// can present them in either slot. Classify by length, never by primary/secondary position, or a 7-day number ends up
// rendered in the 5-hour column and the row lies.
function parseCodexUsage(d) {
  const rl = d.rate_limit || {};
  const wins = [codexWindow(rl.primary_window), codexWindow(rl.secondary_window)].filter(Boolean);
  const strip = (w) => (w ? { percent: w.percent, resetsAt: w.resetsAt, severity: w.severity, locked: w.locked } : null);
  const byLen = (s) => strip(wins.find((w) => w.seconds === s));
  const scoped = [];
  for (const a of Array.isArray(d.additional_rate_limits) ? d.additional_rate_limits : []) {
    const ws = [codexWindow(a.rate_limit?.primary_window), codexWindow(a.rate_limit?.secondary_window)].filter(Boolean);
    if (!ws.length) continue;
    const worst = ws.reduce((m, w) => (w.percent > m.percent ? w : m));
    const span = worst.seconds === CODEX_WEEK_S ? ' wk' : worst.seconds === CODEX_FIVE_HOUR_S ? ' 5h' : '';
    scoped.push({ label: `${a.limit_name || a.metered_feature || 'scoped'}${span}`, percent: worst.percent, resetsAt: worst.resetsAt, severity: worst.severity });
  }
  const c = d.credits || {};
  const extra = { enabled: !!(c.has_credits || c.unlimited), percent: null, currency: null, usedMinor: null, limitMinor: null, exponent: 2, balance: c.balance ?? null, unlimited: !!c.unlimited };
  return { session: byLen(CODEX_FIVE_HOUR_S), weekly: byLen(CODEX_WEEK_S), scoped, extra, breakdown: null, at: Date.now() };
}
// The ownership rule, inherited from the Claude side with the proxy in Claude Code's seat: the ACTIVE account is
// refreshed by the proxy alone, parked ones by this tool alone. Two refreshers spending one rotating token is what
// revoked a grant on the Claude side, and here the loser is deleted outright.
async function refreshCodex(acct) {
  if (acct.name === codexActive()) throw new UsageError(`${acct.name} is the active gateway account — the proxy refreshes it, never this tool`, 'usage');
  if (!acct.refresh) throw new UsageError(`${acct.name} holds no credential — claude-usage add-gpt ${acct.name}`, 'dead');
  const release = takeLock(codexRefreshLock(acct.name), ACCT_LOCK_STALE, LOCK_WAIT.account);
  try {
    const body = new URLSearchParams({ client_id: CODEX_CLIENT_ID, grant_type: 'refresh_token', refresh_token: acct.refresh }).toString();
    const r = await net.http('POST', CODEX_TOKEN_URL, { headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
    if (r.status !== 200 || !r.data?.access_token) {
      // The proxy would delete auth.json here. We only record why, so a re-login is a choice and not a surprise.
      const why = `refresh rejected (${r.status}) — claude-usage add-gpt ${acct.name}`;
      saveCodexMeta(acct.name, { dead: why });
      throw new UsageError(`${acct.name}: ${why}`, 'dead');
    }
    let prev = {}; try { prev = readJson(codexAuthFile(acct.name)); } catch { /* none */ }
    const next = { ...prev, access: r.data.access_token, refresh: r.data.refresh_token || acct.refresh,
      expires: Date.now() + (Number(r.data.expires_in) || 0) * 1000 };
    writeJsonAtomic(codexAuthFile(acct.name), next); // the rotated refresh token MUST land, or the next refresh spends a dead one
    saveCodexMeta(acct.name, { dead: null });
    Object.assign(acct, { access: next.access, refresh: next.refresh, expires: next.expires, dead: null });
    return acct;
  } finally { release(); }
}
async function codexUsageForAccount(acct) {
  if (acct.dead) throw new UsageError(acct.dead, 'dead');
  if (!acct.access) throw new UsageError(`no credential — claude-usage add-gpt ${acct.name}`, 'dead');
  const active = acct.name === codexActive();
  // A parked token we may refresh; the active one we may not, so if it has gone stale we say so and let the proxy fix it.
  if (!active && (acct.expires || 0) - Date.now() < REFRESH_AHEAD_MS) await refreshCodex(acct);
  const r = await getCodexUsage(acct);
  if (r.status === 401) throw new UsageError(active ? 'token stale — the proxy refreshes it on its next request' : 'login expired — claude-usage add-gpt ' + acct.name, 'idle');
  if (r.status === 429) throw new UsageError('usage endpoint rate-limited', 'rate_limited');
  if (r.status !== 200) throw new UsageError(`usage HTTP ${r.status}: ${(r.data?.detail || r.text || '').slice(0, 80)}`, 'http');
  const d = r.data || {};
  const plan = codexPlanLabel(d.plan_type), email = d.email || '?';
  if (plan !== acct.plan || email !== acct.email) saveCodexMeta(acct.name, { plan, email });
  return { usage: parseCodexUsage(d), plan, email };
}
// Switching is one directory entry. A symlink-over-symlink rename is atomic, so no request can see a half-switch.
async function switchCodex(name) {
  if (!codexNames().includes(name)) throw new UsageError(`no ChatGPT account named ${name} — claude-usage add-gpt ${name}`, 'usage');
  const acct = loadCodexAccount(name);
  if (!acct.access || !acct.refresh) throw new UsageError(`${name} holds no credential — claude-usage add-gpt ${name}`, 'dead');
  let st = null; try { st = fs.lstatSync(CODEX_LIVE); } catch { /* absent is fine */ }
  if (st && !st.isSymbolicLink()) throw new UsageError(`${CODEX_LIVE} is a real directory, not a link — claude-usage doctor --fix migrates it first`, 'usage');
  const from = codexActive();
  const release = takeLock(CODEX_SWITCH_LOCK, 600e3, LOCK_WAIT.switch);
  try {
    fs.mkdirSync(path.dirname(CODEX_LIVE), { recursive: true });
    const tmp = `${CODEX_LIVE}.switching-${process.pid}`;
    try { fs.unlinkSync(tmp); } catch { /* none */ }
    fs.symlinkSync(codexDir(name), tmp);
    fs.renameSync(tmp, CODEX_LIVE);
    // Prove it through the path the proxy actually opens, not through the store.
    let seen = null; try { seen = readJson(path.join(CODEX_LIVE, 'auth.json')); } catch { /* reported below */ }
    if (!seen?.access) throw new UsageError(`${name} is linked but its credential is unreadable through ${CODEX_LIVE}`, 'none');
    return { from, to: name };
  } finally { release(); }
}

// ---------- the live login when it is not (yet) a stored account: read-only, shown as "live" ----------
function liveLogin() {
  const o = liveStoreRead()?.claudeAiOauth;
  if (!o?.accessToken) return null;
  const a = claudeJsonAccount();
  return { name: 'live', live: true, email: a?.emailAddress || '?', accountUuid: a?.accountUuid || null, tier: a?.organizationRateLimitTier || o.rateLimitTier || '', plan: o.subscriptionType || '', accessToken: o.accessToken, expiresAt: o.expiresAt || null };
}

// ---------- rendering ----------
const TTY = process.stdout.isTTY && !process.argv.includes('--no-color') && !process.env.NO_COLOR;
const C = { reset: '\x1b[0m', dim: '\x1b[2m', bold: '\x1b[1m', red: '\x1b[31m', yel: '\x1b[33m', grn: '\x1b[32m' };
const paint = (t, c) => (TTY && c ? `${C[c]}${t}${C.reset}` : t);
function planLabel(a) {
  const t = a.tier || '';
  const m = t.match(/max_(\d+)x/); if (m) return `Max ${m[1]}x`;
  if (/pro/.test(t)) return 'Pro'; if (/team/.test(t)) return 'Team'; if (/enterprise/.test(t)) return 'Enterprise'; if (/free/.test(t)) return 'Free';
  if (t) return t.replace(/^default_claude_/, '');
  return a.plan ? a.plan[0].toUpperCase() + a.plan.slice(1) : '?';
}
function until(iso) {
  if (!iso) return '?'; const ms = Date.parse(iso) - Date.now(); if (!(ms > 0)) return 'now';
  const m = Math.floor(ms / 60e3), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (h < 1) return `${m}m`; if (d < 1) return `${h}h${String(m % 60).padStart(2, '0')}`; return `${d}d${h % 24}h`;
}
function bar(p) { const n = Math.max(0, Math.min(10, Math.round(p / 10))); return '█'.repeat(n) + '░'.repeat(10 - n); }
function colorFor(p, severity) { if (severity === 'critical' || p >= 90) return 'red'; if (severity === 'warning' || p >= 70) return 'yel'; return 'grn'; }
function cellWindow(w, ageMs = 0) {
  if (!w) return { t: '—', c: 'dim' };
  // Age only on the first window, and only once it is worth knowing: a reading the tool could not refresh must not read as current.
  const old = ageMs > 2 * 60e3 ? ` ${Math.round(ageMs / 60e3)}m old` : '';
  return { t: `${bar(w.percent)} ${String(w.percent).padStart(3)}% ↻${until(w.resetsAt)}${w.locked ? ' LOCKED' : ''}${old}`, c: colorFor(w.percent, w.severity) };
}
function cellScoped(list) {
  if (!list?.length) return { t: '', c: null };
  const worst = Math.max(...list.map((s) => s.percent));
  return { t: list.map((s) => `${s.label} ${s.percent}%`).join(' '), c: colorFor(worst, list.find((s) => s.percent === worst)?.severity) };
}
function cellExtra(e) {
  if (!e) return { t: '', c: null };
  // Before the enabled check: a Codex row with no credits is "credits 0", not "off" — "off" is the Anthropic meter
  // saying extra usage is disabled, which is a different fact.
  if (e.percent == null && (e.unlimited || e.balance != null)) return { t: e.unlimited ? 'credits inf' : `credits ${e.balance}`, c: e.balance === '0' ? 'dim' : null };
  if (!e.enabled) return { t: 'off', c: 'dim' };
  const money = e.usedMinor != null && e.limitMinor != null ? ` ${(e.usedMinor / 10 ** e.exponent).toFixed(0)}/${(e.limitMinor / 10 ** e.exponent).toFixed(0)} ${e.currency || ''}`.trimEnd() : '';
  // The Codex row buys headroom with credits, not a spend cap, so it has a balance where a Claude row has a percentage.
  return { t: `on ${e.percent ?? '?'}%${money}`, c: (e.percent ?? 0) >= 80 ? 'yel' : null };
}
// The EDF column: what the next tick would do with this row. ▶ the row a rotation would enter next, ✓ another usable
// row, otherwise why it cannot take the machine, in unusable()'s words. Blank for a ChatGPT row (never a candidate)
// and for the live/checked-out row (already holding the machine, not a parked one candidates() would rank).
function cellEdf(r, edf) {
  if ((r.provider || 'anthropic') !== 'anthropic' || r.checkedOut) return { t: '', c: null };
  const p = edf.parked.find((x) => x.name === r.name);
  if (!p) return { t: '', c: 'dim' };
  if (edf.next && edf.next.name === r.name) return { t: '▶ next', c: 'grn' };
  if (!p.blocked) return { t: p.warm ? '✓' : '✓ 5h warm', c: 'grn' };
  return { t: p.blocked, c: 'red' };
}
function table(head, rows) {
  const all = [head.map((h) => ({ t: h, c: 'bold' })), ...rows];
  const w = head.map((_, i) => Math.max(...all.map((r) => (r[i]?.t || '').length)));
  for (const r of all) log(r.map((cell, i) => paint((cell?.t || '').padEnd(w[i]), cell?.c)).join('  ').trimEnd());
}

// ---------- stats ----------
// The last reading we have for each account, from the meter the previous run wrote.
function meterRows() { try { return readJson(METER).accounts || {}; } catch { return {}; } }
// A 429 is the endpoint saying "not now": keep the last reading, and hold off for a while before asking again.
function backoffAfter429(name) {
  const rl = { ...(state().rateLimited || {}) };
  const tries = (rl[name]?.tries || 0) + 1;
  rl[name] = { tries, until: Date.now() + RL_BACKOFF_MS[Math.min(tries, RL_BACKOFF_MS.length) - 1] };
  setState({ rateLimited: rl });
}
function clearBackoff(name) {
  const rl = state().rateLimited || {};
  if (!rl[name]) return;
  const { [name]: _gone, ...rest } = rl;
  setState({ rateLimited: rest });
}
async function collect({ includeLive = true, force = false } = {}) {
  const accounts = names().map(loadAccount);
  const live = includeLive ? liveLogin() : null;
  if (live && !accounts.some((a) => a.accountUuid && a.accountUuid === live.accountUuid)) accounts.push(live);
  // The ChatGPT accounts ride along as more rows: same TTL, same 429 backoff, same meter file. They are not Claude
  // grants, so they come from their own store rather than accounts/ — and `provider` is what keeps them out of
  // decide()'s failover candidates, which can only ever check out a Claude grant.
  for (const name of codexNames()) accounts.push(loadCodexAccount(name));
  const out = [];
  const prev = meterRows();
  const cooling = state().rateLimited || {};
  for (const a of accounts) { // sequential: the token endpoint rate-limits bursts
    const checkedOut = a.provider === 'openai' ? a.name === codexActive() : isCheckedOut(a);
    const row = { name: a.name, provider: a.provider || 'anthropic', priority: a.priority || 0, live: !!a.live, checkedOut, home: a.name === state().home, email: a.email || '?', plan: planLabel(a), tier: a.tier || '', accountUuid: a.accountUuid || null, refreshTokenExpiresAt: a.refreshTokenExpiresAt || null };
    if (a.provider === 'openai') { row.plan = a.plan; row.email = a.email; }
    const cached = prev[a.name]?.usage?.at ? prev[a.name].usage : null;
    const age = cached ? Date.now() - cached.at : Infinity;
    const until = cooling[a.name]?.until || 0;
    const held = until > Date.now();
    // The hold comes first, cached reading or not: an account we have never read is exactly the one whose bucket is
    // emptiest, and asking it every tick is what kept it there.
    if (held && !force) {
      row.rateLimited = true;
      if (cached) { row.usage = cached; row.cached = true; }
      else { row.error = `usage endpoint rate-limited; asking again at ${new Date(until).toLocaleTimeString()}`; row.errorKind = 'rate_limited'; }
      out.push(row); continue;
    }
    if (cached && !force && age < (checkedOut ? METER_TTL.checkedOut : METER_TTL.parked)) {
      row.usage = cached; row.cached = true;
      out.push(row); continue;
    }
    try {
      if (a.provider === 'openai') { const g = await codexUsageForAccount(a); row.usage = g.usage; row.plan = g.plan; row.email = g.email; }
      else row.usage = await usageFor(a);
      clearBackoff(a.name);
    }
    catch (e) {
      if (e.kind === 'rate_limited') backoffAfter429(a.name);
      // A rate-limited read is not news about the account: the reading we have still describes it, so show that and say how old it is.
      if (e.kind === 'rate_limited' && cached) { row.usage = cached; row.cached = true; row.rateLimited = true; }
      else { row.error = e.message; row.errorKind = e.kind || 'error'; }
    }
    out.push(row);
  }
  // A row that failed keeps the last reading we had for it in the file (not on the rendered row, which stays ✗): without
  // this the first failure erases the very fallback the next rate-limited read needs, and one 429 blinds the tool for good.
  const meter = { at: Date.now(), accounts: Object.fromEntries(out.map((r) => [r.name, r.usage ? r : { ...r, usage: prev[r.name]?.usage }])) };
  writeJsonAtomic(METER, meter);
  return out;
}
function renderRows(rows) {
  if (!rows.length) { log('no accounts yet — `claude-usage add <name>` stores one (and the login of this terminal shows as "live")'); return; }
  const live = rows.find((r) => r.checkedOut && (r.provider || 'anthropic') === 'anthropic')?.name;
  const edf = candidates({ rows, live, thresholds: thresholds(), limited: state().limited || {} });
  const head = ['account', 'email', 'plan', '5-hour window', 'weekly (all models)', 'weekly per model', 'edf', 'extra'];
  table(head, rows.map((r) => {
    const name = { t: `${r.checkedOut ? '● ' : '  '}${r.name}${r.home ? ' ⌂' : ''}${r.live ? ' (this terminal, not stored)' : ''}`, c: r.live ? 'dim' : r.checkedOut ? 'bold' : null };
    if (r.error) return [name, { t: r.email }, { t: r.plan }, { t: `✗ ${r.error}`, c: 'red' }];
    const u = r.usage;
    const age = u.at ? Date.now() - u.at : 0;
    return [name, { t: r.email.length > 28 ? r.email.slice(0, 27) + '…' : r.email }, { t: r.plan }, cellWindow(u.session, age), cellWindow(u.weekly), cellScoped(u.scoped), cellEdf(r, edf), cellExtra(u.extra)];
  }));
}
async function cmdStats({ json = false, quiet = false, includeLive = true, force = false } = {}) {
  const rows = await collect({ includeLive, force });
  if (quiet) { const bad = rows.filter((r) => r.error); if (bad.length) console.error(bad.map((r) => `${r.name}: ${r.error}`).join('\n')); return rows; }
  if (json) { log(JSON.stringify({ at: new Date().toISOString(), accounts: rows }, null, 2)); return rows; }
  renderRows(rows);
  log(paint(`\n${new Date().toLocaleTimeString()} · ↻ = resets in · ● = checked out into Claude Code · ⌂ = home · meters = api.anthropic.com/api/oauth/usage (what /usage shows) and, for chatgpt, chatgpt.com/backend-api/wham/usage (the gateway's login)`, 'dim'));
  return rows;
}
async function cmdWatch(every) {
  const s = Math.max(30, Number(every) || 120);
  for (;;) {
    process.stdout.write('\x1b[2J\x1b[H');
    try { await cmdStats(); } catch (e) { log(paint(`✗ ${e.message}`, 'red')); }
    log(paint(`every ${s}s · ctrl-c to stop`, 'dim'));
    await new Promise((r) => setTimeout(r, s * 1000));
  }
}

// ---------- add / remove / list ----------
function extractLogin(dir) {
  for (const d of [dir, safeReal(dir)]) {
    const service = `Claude Code-credentials-${h8(d)}`;
    let raw = security(['find-generic-password', '-s', service, '-a', KEYCHAIN_USER, '-w']).out;
    if (!raw) { try { raw = fs.readFileSync(path.join(d, '.credentials.json'), 'utf8'); } catch { /* none */ } }
    if (!raw) continue;
    let oauth; try { oauth = JSON.parse(raw).claudeAiOauth; } catch { continue; }
    if (!oauth?.accessToken || !oauth?.refreshToken) continue;
    let account = null; try { account = readJson(path.join(d, '.claude.json')).oauthAccount || null; } catch { /* none */ }
    return { oauth, account };
  }
  return null;
}
function safeReal(p) { try { return fs.realpathSync(p); } catch { return p; } }
function cleanupLogin(dir) {
  for (const d of new Set([dir, safeReal(dir)])) for (const s of [`Claude Code-credentials-${h8(d)}`, `Claude Code-${h8(d)}`]) security(['delete-generic-password', '-s', s, '-a', KEYCHAIN_USER]);
  fs.rmSync(dir, { recursive: true, force: true });
}
// `add` stores a NEW grant as a parked record. Run it on the checked-out account and the browser login Claude Code is
// using right now is simply dropped: the record loses `checkedOut`, and the next reconcile strips the new grant as
// "superseded". With an EMPTY store there is no login to lose, so it is allowed — that is the way back in after a /logout.
function addBlockedBy(name) {
  let rec = null; try { rec = loadAccount(name); } catch { return null; }
  if (!isCheckedOut(rec) || !liveStoreRead()?.claudeAiOauth?.accessToken) return null;
  return `"${name}" is the checked-out account: its grant is the login Claude Code is using right now, and adding would throw it away. \`claude-usage switch <other>\` first (or \`claude-usage remove ${name}\`), then \`claude-usage add ${name} --force\`.`;
}
async function cmdAdd(name, opts = {}) {
  if (!name) die('usage: claude-usage add <name|email> [--email <address>] [--force]');
  if (name.includes('@')) { opts.email = opts.email || name; name = name.split('@')[0].replace(/[^a-z0-9._-]/gi, '-').toLowerCase(); } // `add someone@x.y` = name from the local part
  if (!NAME_RE.test(name) || name === 'live') die('name: letters, digits, . _ - only (and not "live")');
  if (fs.existsSync(acctFile(name)) && !opts.force) die(`"${name}" already exists (--force to replace its grant)`);
  const blocked = addBlockedBy(name); if (blocked) die(blocked);
  if (!process.stdin.isTTY) die(`add "${name}" needs a terminal for the browser sign-in — run it yourself: ! claude-usage add ${opts.email || name}`);
  fs.mkdirSync(LOGINS, { recursive: true, mode: 0o700 });
  const dir = path.join(LOGINS, name);
  cleanupLogin(dir);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  writeJsonAtomic(path.join(dir, '.claude.json'), { hasCompletedOnboarding: true }, 0o600);
  log(`Signing in as "${name}" through Claude Code's own login, in a private config dir (your own login is untouched).`);
  log('  • same machine: complete the browser flow as that person (Incognito window if the browser is signed in to another claude.ai account).');
  log('  • remote family member: send them the printed URL; they sign in as themselves and send back the code the page shows;');
  log('    paste it at "Paste code here if prompted >".');
  log('');
  const env = { ...process.env, CLAUDE_CONFIG_DIR: dir };
  for (const k of ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_GATEWAY', 'ANTHROPIC_BASE_URL']) delete env[k];
  const r = spawnSync('claude', ['auth', 'login', '--claudeai', ...(opts.email ? ['--email', opts.email] : [])], { stdio: 'inherit', env });
  let got = null;
  try { got = extractLogin(dir); } finally { cleanupLogin(dir); }
  if (!got) die(`no login was stored (claude auth login exited ${r.status}); nothing changed`);
  const o = got.oauth; const a = got.account || {};
  const acct = { name, email: a.emailAddress || '?', accountUuid: a.accountUuid || null, orgName: a.organizationName || null, tier: a.organizationRateLimitTier || o.rateLimitTier || '', plan: o.subscriptionType || '',
    accessToken: o.accessToken, refreshToken: o.refreshToken, expiresAt: o.expiresAt || null, refreshTokenExpiresAt: o.refreshTokenExpiresAt || null, scopes: o.scopes || DEFAULT_SCOPES, addedAt: new Date().toISOString() };
  try { const p = await profileOf(acct.accessToken); if (p) { acct.email = p.email || acct.email; acct.accountUuid = p.accountUuid || acct.accountUuid; acct.tier = p.tier || acct.tier; acct.orgName = p.orgName || acct.orgName; acct.plan = p.plan || acct.plan; } } catch { /* profile is a nicety */ }
  const dup = names().find((n) => n !== name && loadAccount(n).accountUuid === acct.accountUuid);
  if (dup && !opts.force) die(`${acct.email} is already stored as "${dup}" (remove it first, or --force to keep both grants)`);
  fs.mkdirSync(ACCOUNTS, { recursive: true, mode: 0o700 });
  saveAccount(acct);
  log(`\nadded "${name}": ${acct.email} (${planLabel(acct)}); grant refresh valid ${untilMs(acct.refreshTokenExpiresAt)}`);
  const rows = await collect({ includeLive: false });
  renderRows(rows.filter((x) => x.name === name));
}
function untilMs(ms) { if (!ms) return '?'; const d = (ms - Date.now()) / 864e5; return d < 0 ? 'EXPIRED' : d < 1 ? `${Math.round(d * 24)}h` : `${d.toFixed(0)}d`; }
function cmdRemove(name) {
  if (!name || !fs.existsSync(acctFile(name))) die(`no stored account "${name}" (have: ${names().join(', ') || 'none'})`);
  fs.rmSync(acctFile(name));
  log(`removed "${name}" from the meter. The grant itself stays valid until its refresh token expires (≤ 3 weeks); revoke it under claude.ai settings → sessions if you want it gone now.`);
}
function cmdList() {
  const ns = names(); const live = liveLogin();
  if (!ns.length && !live) { log('no accounts. `claude-usage add <name>`'); return; }
  const rows = ns.map((n) => { const a = loadAccount(n); return [{ t: n }, { t: a.email || '?' }, { t: planLabel(a) }, { t: a.dead ? `✗ ${a.dead}` : inTransition(a) ? 'IN TRANSITION — claude-usage doctor --fix' : isCheckedOut(a) ? 'checked out (Claude Code refreshes it)' : `parked · token ${untilMs(a.expiresAt)} · refresh ${untilMs(a.refreshTokenExpiresAt)}`, c: a.dead || inTransition(a) ? 'red' : null }, { t: `added ${(a.addedAt || '').slice(0, 10)}`, c: 'dim' }]; });
  if (live) rows.push([{ t: 'live (this terminal)', c: 'dim' }, { t: live.email }, { t: planLabel(live) }, { t: `token ${untilMs(live.expiresAt)} · refreshed by claude itself`, c: 'dim' }, { t: ns.some((n) => loadAccount(n).accountUuid === live.accountUuid) ? 'same account as a stored grant → hidden in stats' : '', c: 'dim' }]);
  table(['account', 'email', 'plan', 'grant', ''], rows);
}

// ---------- the background watcher: systemd user timer on Linux, launchd on macOS ----------
const SYSTEMD_DIR = path.join(HOME, '.config', 'systemd', 'user');
const UNIT = 'claude-usage';
function systemdUnits(interval) {
  const nodeDir = path.dirname(process.execPath);
  return {
    [`${UNIT}.service`]: `# ABOUTME: One claude-usage tick: meter every account, fail over / back between them at the thresholds, notify.
# ABOUTME: Fired by ${UNIT}.timer; state under ~/.claude/claude-usage (never hand-edited); written by \`claude-usage auto on\`.
[Unit]
Description=claude-usage tick (meter + account failover)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# A wedged tick (a lock it cannot take, a hung POST) has to die: the timer stays "active" either way, so nothing else notices.
TimeoutStartSec=300
# PATH is load-bearing: direnv does not run under systemd and node lives in ${nodeDir}.
Environment=PATH=${nodeDir}:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${process.execPath} ${path.join(ROOT, 'claude-usage.js')} auto tick
`,
    [`${UNIT}.timer`]: `# ABOUTME: Runs the claude-usage tick every ${interval}s; a missed tick is just picked up by the next one.
# ABOUTME: Written by \`claude-usage auto on\`; \`auto off\` removes it.
[Unit]
Description=claude-usage tick every ${interval}s

[Timer]
OnBootSec=2min
OnUnitActiveSec=${interval}s
AccuracySec=10s

[Install]
WantedBy=timers.target
`,
  };
}
function sysctl(args) { return spawnSync('systemctl', ['--user', ...args], { encoding: 'utf8' }); }
function systemdOn(interval) {
  fs.mkdirSync(SYSTEMD_DIR, { recursive: true });
  for (const [name, text] of Object.entries(systemdUnits(interval))) fs.writeFileSync(path.join(SYSTEMD_DIR, name), text, { mode: 0o644 });
  sysctl(['daemon-reload']);
  const r = sysctl(['enable', '--now', `${UNIT}.timer`]); // enabled AND started: enabled-but-not-started dies at the next reboot
  if (r.status !== 0) die(`systemctl enable --now failed: ${r.stderr || r.stdout}`);
}
function systemdOff() {
  sysctl(['disable', '--now', `${UNIT}.timer`]);
  for (const n of [`${UNIT}.timer`, `${UNIT}.service`]) { try { fs.rmSync(path.join(SYSTEMD_DIR, n)); } catch { /* none */ } }
  sysctl(['daemon-reload']);
}
function launchdOn(interval) {
  const uid = process.getuid();
  spawnSync('launchctl', ['bootout', `gui/${uid}`, LAUNCHD_PLIST], { stdio: 'ignore' });
  fs.mkdirSync(path.dirname(LAUNCHD_PLIST), { recursive: true });
  const esc = (x) => String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key><array><string>${esc(process.execPath)}</string><string>${esc(path.join(ROOT, 'claude-usage.js'))}</string><string>auto</string><string>tick</string></array>
  <key>StartInterval</key><integer>${interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>${esc(HOME)}</string><key>PATH</key><string>${esc(path.dirname(process.execPath))}:/usr/bin:/bin:/usr/sbin:/sbin:${esc(path.join(HOME, '.local', 'bin'))}</string></dict>
  <key>StandardOutPath</key><string>${esc(AUTO_LOG)}</string>
  <key>StandardErrorPath</key><string>${esc(AUTO_LOG)}</string>
</dict></plist>
`;
  fs.writeFileSync(LAUNCHD_PLIST, plist, { mode: 0o644 });
  const r = spawnSync('launchctl', ['bootstrap', `gui/${uid}`, LAUNCHD_PLIST], { encoding: 'utf8' });
  if (r.status !== 0) die(`launchctl bootstrap failed: ${r.stderr || r.stdout}`);
}
function launchdOff() { spawnSync('launchctl', ['bootout', `gui/${process.getuid()}`, LAUNCHD_PLIST], { stdio: 'ignore' }); try { fs.rmSync(LAUNCHD_PLIST); } catch { /* none */ } }
function watcherLoaded() {
  if (DARWIN) return spawnSync('launchctl', ['print', `gui/${process.getuid()}/${LAUNCHD_LABEL}`], { encoding: 'utf8' }).status === 0;
  return sysctl(['is-active', '--quiet', `${UNIT}.timer`]).status === 0;
}
async function cmdAuto(sub, opts = {}) {
  if (sub === 'tick') return cmdTick(); // the timer's own entry point: cmdTick never throws
  if (!sub || sub === 'status') {
    const s = state(); const t = thresholds();
    log(`auto: ${s.auto ? 'on' : 'off'} (${DARWIN ? 'launchd job' : 'systemd user timer'} ${watcherLoaded() ? 'active' : 'not active'}) · every ${s.interval || 60}s · hard rule: weekly ≥${t.switchAt}% · 5h ≥${t.sessionHot}% · Fable ≥${t.fableCeiling}% · locked · rotate when a usable account leads by ≥${t.edfLeadMs / 3.6e6}h or live 5h ≥${t.sessionWarm}% · hold ${t.minHoldMs / 6e4} min`);
    log(`live: ${s.live || '?'} · home: ${s.home || '?'}`);
    try {
      const m = readJson(METER); const rows = Object.values(m.accounts);
      const { parked, next } = candidates({ rows, live: s.live, thresholds: t, limited: s.limited || {} });
      const usable = parked.filter((p) => !p.blocked).map((p) => `${p.name} ↻${until(p.resetsAt)}${p.warm ? '' : ' (5h warm)'}`).join(', ');
      log(next ? `edf: next ${next.name} (↻${until(next.resetsAt)}, weekly ${next.weekly}%) · usable: ${usable}` : `edf: nothing to rotate to · usable: ${usable || 'none'}`);
    } catch { log('edf: no meter yet'); }
    if (s.lastDecision) log(`last decision: ${s.lastDecision.at} ${s.lastDecision.action}: ${s.lastDecision.reason}${s.lastDecision.error ? ' ✗ ' + s.lastDecision.error : ''}`);
    try { const m = readJson(METER); log(`last meter: ${new Date(m.at).toLocaleString()} (${Math.round((Date.now() - m.at) / 60000)} min ago)`); } catch { log('last meter: never'); }
    return;
  }
  if (sub === 'off') { setState({ auto: false }); DARWIN ? launchdOff() : systemdOff(); log('auto OFF (watcher removed)'); return; }
  if (sub !== 'on') die('usage: claude-usage auto on [--every <s>] [--switch-at <p>] [--session-hot <p>] [--session-warm <p>] [--fable-ceiling <p>] [--edf-lead-hours <h>] [--min-hold-minutes <n>] [--target-below <p>] | off | status | tick');
  const interval = Math.max(30, Number(opts.every || state().interval || 60));
  const t = { ...thresholds() };
  for (const [k, f] of [['switchAt', 'switch-at'], ['targetBelow', 'target-below'], ['fableCeiling', 'fable-ceiling'], ['sessionHot', 'session-hot'], ['sessionWarm', 'session-warm']]) if (opts[f] !== undefined) t[k] = Math.max(1, Math.min(100, Number(opts[f])));
  if (opts['edf-lead-hours'] !== undefined) t.edfLeadMs = Math.max(0, Number(opts['edf-lead-hours'])) * 3_600_000;
  if (opts['min-hold-minutes'] !== undefined) t.minHoldMs = Math.max(0, Number(opts['min-hold-minutes'])) * 60_000;
  for (const k of ['gapThreshold', 'sessionWeight', 'tieBandPoints', 'homeBelow']) delete t[k]; // the pressure/gap era: never carried forward
  setState({ auto: true, interval, thresholds: t });
  DARWIN ? launchdOn(interval) : systemdOn(interval);
  log(`auto ON: every ${interval}s · hard rule: weekly ≥${t.switchAt}% · 5h ≥${t.sessionHot}% · Fable ≥${t.fableCeiling}% · rotate on a ≥${t.edfLeadMs / 3.6e6}h lead or live 5h ≥${t.sessionWarm}% · hold ${t.minHoldMs / 6e4} min · log ${AUTO_LOG}`);
}

// ---------- status line ----------
// The account THIS session spends first, then every account whose week still has room. Accounts with a red week are
// left out: they are not room, and seven rows of "name 5h/wk!" in a status line said nothing at a glance. The full
// table is `stats`.
// Four levels. Red is the watcher's own hard rule — sessionHot (85) for the 5-hour cell, switchAt (95) for the weekly
// one — so the colour and the failover agree by construction; then orange past 70 and yellow past 50, strict, as Louis
// reads them. A locked window is red whatever its number - locked at 40 % shown green would be a lie. Orange has no
// basic ANSI code, hence 256-colour.
const LEVEL = { red: '\x1b[31m', orange: '\x1b[38;5;208m', yellow: '\x1b[33m', green: '\x1b[32m' };
function levelFor(w, bound = thresholds().switchAt) {
  if (!w || w.percent == null) return null;
  if (w.locked || w.percent >= bound) return 'red'; if (w.percent > 70) return 'orange'; if (w.percent > 50) return 'yellow'; return 'green';
}
function statuslineText(payload = null) {
  let m = null; try { m = readJson(METER); } catch { /* none */ }
  if (!m) return 'CC: no meter yet';
  // A session on a gateway model (GPT-6 Astra, the GPT-5.6 tiers) spends the ChatGPT account the gateway serves, not
  // the checked-out Claude one. Claude Code's JSON names the model; with no payload (a hand run) it is a Claude session.
  const gateway = /^gpt-/.test(payload?.model?.id || '');
  const rows = Object.values(m.accounts);
  const isGpt = (x) => x.provider === 'openai';
  const cur = rows.find((x) => x.checkedOut && isGpt(x) === gateway) || rows.find((x) => x.checkedOut);
  if (!cur) return 'CC: no account checked out';
  const stale = Date.now() - m.at > STALE_MS ? ` ${C.dim}(stale)${C.reset}` : '';
  const paint = (lvl, t) => (lvl ? `${LEVEL[lvl]}${t}${C.reset}` : t);
  const hot = thresholds().sessionHot;
  const cell = (glyph, x, bound) => (levelFor(x, bound) ? paint(levelFor(x, bound), `${glyph}${x.percent}%`) : '');
  // An account's level is its name's colour: the weekly one - the budget that decides the week - unless the 5-hour
  // window is at the wall, which is the thing that stops a session right now. An unreadable row has no level.
  const accountLevel = (r) => (r.error ? null : levelFor(r.usage.session, hot) === 'red' ? 'red' : levelFor(r.usage.weekly));
  // A plan without a 5-hour window (Pro Lite) simply has no ⏳ cell.
  const one = (r) => (r.error ? `${r.name} ✗` : [paint(accountLevel(r), r.name), cell('⏳', r.usage.session, hot), cell('📅', r.usage.weekly, thresholds().switchAt)].filter(Boolean).join(' '));
  // Room is a matter of the week: an account whose 5-hour window is at the wall is back in a couple of hours, one
  // whose week is spent is out for days, so only a red WEEK drops an account from the line. The current account's own
  // kind first - the accounts a switch can reach - then the other kind, each most room first. An unreadable account
  // is not room and goes last in its kind, shown as ✗ rather than dropped: it exists and nobody could read it.
  const room = (r) => (r.error ? Infinity : (worstOf(r.usage) ?? Infinity));
  const others = rows.filter((r) => r !== cur && levelFor(r.usage?.weekly) !== 'red')
    .sort((a, b) => (isGpt(a) !== isGpt(cur)) - (isGpt(b) !== isGpt(cur)) || room(a) - room(b) || a.name.localeCompare(b.name));
  return [cur, ...others].map(one).join(' | ') + stale;
}
function cmdStatusline(sub) {
  if (sub === 'install') return installStatusline();
  if (sub === 'uninstall') return uninstallStatusline();
  // Claude Code pipes its JSON in; a hand run from a terminal has a TTY on stdin and must not sit waiting for it.
  let payload = null;
  if (!process.stdin.isTTY) { try { payload = JSON.parse(fs.readFileSync(0, 'utf8')); } catch { /* empty or not JSON: a Claude session */ } }
  process.stdout.write(statuslineText(payload));
}
function statuslineWrapper(prev) {
  const mine = `"${process.execPath}" "${path.join(ROOT, 'claude-usage.js')}" statusline`;
  // Claude Code's JSON arrives on stdin and can only be read once, so capture it and hand the SAME bytes to each
  // segment: the previous status line needs it for the model, context and session id; ours for the model that says
  // which account this session spends. Without this the previous segment silently got nothing the moment a second
  // segment was appended. `$(...)` strips the previous segment's trailing newlines however many lines it prints, so
  // the meter always lands on its own line right under it - an earlier ` · ` joiner assumed a one-line predecessor
  // and left a stray dot at the start of the meter's line under Louis's two-line one.
  return `#!/bin/sh\n# claude-usage statusline: the previous status line first, then this session's account meter on its own line.\npayload=$(cat)\n${prev ? `printf '%s\\n' "$(printf '%s' "$payload" | ${prev})"\n` : ''}printf '%s' "$payload" | ${mine}\n`;
}
function installStatusline() {
  let settings = {}; try { settings = readJson(USER_SETTINGS); } catch { /* none */ }
  const prev = settings.statusLine?.type === 'command' ? settings.statusLine.command : '';
  fs.mkdirSync(ROOT, { recursive: true, mode: 0o700 }); // a machine with no account yet has no ROOT either
  if (prev && prev.includes(STATUSLINE_SH)) {
    // Already ours: rewrite the wrapper from the remembered predecessor so a newer wrapper reaches an old install
    // without an uninstall/install round trip through settings.json.
    fs.writeFileSync(STATUSLINE_SH, statuslineWrapper(state().statuslinePrev || ''), { mode: 0o700 });
    log(`statusline already installed; wrapper rewritten (${STATUSLINE_SH})`); return;
  }
  fs.writeFileSync(STATUSLINE_SH, statuslineWrapper(prev), { mode: 0o700 });
  setState({ statuslinePrev: prev || null });
  settings.statusLine = { type: 'command', command: STATUSLINE_SH };
  writeJsonAtomic(USER_SETTINGS, settings, 0o644);
  log(`statusline installed (${STATUSLINE_SH}); new sessions show "<name> ⏳5h% 📅wk%" for the account they spend. Turn on the background meter: claude-usage auto on`);
}
function uninstallStatusline() {
  let settings = {}; try { settings = readJson(USER_SETTINGS); } catch { /* none */ }
  const prev = state().statuslinePrev;
  if (prev) settings.statusLine = { type: 'command', command: prev }; else delete settings.statusLine;
  writeJsonAtomic(USER_SETTINGS, settings, 0o644);
  try { fs.rmSync(STATUSLINE_SH); } catch { /* none */ }
  log(`statusline restored to ${prev ? 'the previous command' : 'none'}`);
}

// ---------- doctor ----------
async function cmdDoctor({ fix = false } = {}) {
  let bad = 0; const check = (ok, m) => { log(`${ok ? 'PASS' : 'FAIL'}  ${m}`); if (!ok) bad++; };
  if (fix) { const live = liveStoreRead(); if (live) { const o = await reconcile(live); log(`reconciled: live login is ${o ? o.name : 'not a stored account'}`); } else log('no live login in Claude Code — nothing to reconcile'); }
  const co = names().map(loadAccount).filter(isCheckedOut);
  check(co.length <= 1, `at most one checked-out account (${co.map((a) => a.name).join(', ') || 'none'})`);
  const tr = co.filter(hasTokens);
  check(!tr.length, `no account in transition${tr.length ? ' (' + tr.map((a) => a.name).join(', ') + ') — claude-usage doctor --fix' : ''}`);
  check(!co.length || state().live === co[0].name, `state.live (${state().live || 'unset'}) matches the checked-out account`);
  // "at most one" passes on ZERO checked-out records, which is exactly what a crashed race leaves behind: the machine then
  // meters nothing, the watcher decides `none` forever and nobody is told. This is the check that fails on it.
  const ln = state().live;
  check(!ln || (names().includes(ln) && isCheckedOut(loadAccount(ln))), `state.live (${ln || 'unset'}) names a checked-out record${ln && !names().includes(ln) ? ' — no such record' : ''}`);
  check(!state().auto || watcherLoaded(), `watcher ${state().auto ? 'active' : 'off (fine)'}`);
  check(fs.existsSync(ROOT) && (fs.statSync(ROOT).mode & 0o077) === 0, `${ROOT} is 0700`);
  for (const n of names()) {
    const f = acctFile(n); check((fs.statSync(f).mode & 0o077) === 0, `${n}.json is 0600`);
    const a = loadAccount(n); check(!a.dead, `${n}: grant alive${a.dead ? ' (' + a.dead + ')' : ''}`);
    // A checked-out record holds no tokens by design (Claude Code has them and refreshes them): asking it for a refresh-token expiry is asking the wrong question.
    check(isCheckedOut(a) || (a.refreshTokenExpiresAt || 0) > Date.now() + 2 * 864e5, `${n}: ${isCheckedOut(a) ? 'checked out (Claude Code holds and refreshes its tokens)' : `refresh token valid > 2 days (${untilMs(a.refreshTokenExpiresAt)})`}`);
  }
  let leftovers = []; try { leftovers = fs.readdirSync(LOGINS); } catch { /* none */ }
  check(!leftovers.length, `no leftover login dirs under ${LOGINS}${leftovers.length ? ' (' + leftovers.join(', ') + ') — an interrupted add; rerun add' : ''}`);
  const dump = spawnSync('/usr/bin/security', ['dump-keychain'], { encoding: 'utf8', timeout: 30000 }).stdout || '';
  const stray = [...dump.matchAll(/"svce"<blob>="(Claude Code-credentials-[0-9a-f]{8})"/g)].map((m) => m[1]);
  const profiles = (() => { try { return fs.readdirSync(path.join(HOME, '.claude-profiles')).map((p) => `Claude Code-credentials-${h8(path.join(HOME, '.claude-profiles', p))}`); } catch { return []; } })();
  const unexplained = stray.filter((s) => !profiles.includes(s));
  check(!unexplained.length, `no stray per-dir Keychain items${unexplained.length ? ' (' + unexplained.join(', ') + ')' : ''}`);
  try { const m = readJson(METER); check(Date.now() - m.at < STALE_MS || !state().auto, `meter fresh (${Math.round((Date.now() - m.at) / 60000)} min old)`); } catch { check(!state().auto, 'meter file exists (auto is off, fine)'); }
  log(bad ? `\n${bad} check(s) failed` : '\nall checks passed'); process.exitCode = bad ? 1 : 0;
}

function help() {
  log(`claude-usage — every family Claude account's rate-limit meter in one table

  stats [--json] [--no-live]   (default) 5-hour window, weekly window, per-model weekly, extra usage — per account
  watch [--every <s>]          same table, refreshed in place (default 120 s)
  list                         stored accounts and grant expiry, no network
  add <name|email> [--email]   store an account: Claude Code's own \`auth login\` in a private config dir, then the
                               grant moves into ${ACCOUNTS} and the temp dir + Keychain item are deleted
  remove <name>                forget an account
  adopt <name>                 take the login of this terminal as the stored account <name> (no browser); it is
                               then "checked out": its tokens live in Claude Code's store and Claude Code refreshes them
  switch [<name>] [--failover] hand every session on this machine to <name> (no name = the other one). The live pair
                               is parked into its record, <name>'s pair goes into Claude Code's store; sessions and
                               their subagents follow at their next request. --failover leaves "home" unchanged
  auto on [--every <s>] [--switch-at <p>] [--session-hot <p>] [--session-warm <p>] [--fable-ceiling <p>]
          [--edf-lead-hours <h>] [--min-hold-minutes <n>] [--target-below <p>] | off | status | tick
                               watcher (systemd user timer / launchd): meter every account. Hard rule — the live
                               account cannot serve (a locked window, 5-hour ≥85%, weekly ≥95%, Fable ≥90%): move
                               now, to the usable account whose weekly reset is soonest (EDF), perso only if nothing
                               preferred is usable. Otherwise rotate to the EDF pick when it leads by ≥2h or the live
                               5-hour window is ≥65% (warm), at most once per hold (10 min), never onto an account
                               whose 5-hour window is warm itself, never onto perso. Successful moves go to auto.log;
                               Telegram only when nothing can take the machine or a switch failed. A target must have
                               answered on BOTH windows and have neither locked: an unread window is not room
  doctor [--fix]               checks; --fix reconciles a switch that died halfway
  statusline [install|uninstall]         "alice ⏳40% 📅12% | bob ⏳0% 📅3%": the account this session spends, then every account
                                         whose week is not red; red = the watcher's hard rule (⏳ at session-hot 85, 📅 at
                                         switch-at 95), orange >70, yellow >50

Exactly one stored account is checked out at a time (● in stats); the rest are parked and refreshed here only.
A "live (this terminal, not stored)" row means the login in Claude Code is not a stored account yet — \`claude-usage adopt <name>\` takes it, no browser.
Never /logout (it revokes the live grant server-side — switch instead); never set CLAUDE_CODE_OAUTH_TOKEN in a session
that should follow switches. Never share tokens; files are 0600 under ${ROOT}.`);
}

// Flags that take the NEXT argv token as their value (`--session-hot 80`). A flag missing from this list parses as the
// boolean true and its value becomes a stray positional — which is how five threshold flags shipped broken once
// (2026-09-21): every flag `auto on` reads must be here, and a retired one must not.
const VALUE_FLAGS = ['email', 'every', 'switch-at', 'target-below', 'fable-ceiling', 'session-hot', 'session-warm', 'edf-lead-hours', 'min-hold-minutes', 'by', 'since', 'until', 'repo', 'roots'];
function parseArgv(rest) {
  const flags = new Map(); const args = [];
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a.startsWith('--')) { const [k, v] = a.slice(2).split('='); if (v !== undefined) flags.set(k, v); else if (VALUE_FLAGS.includes(k) && rest[i + 1] && !rest[i + 1].startsWith('--')) flags.set(k, rest[++i]); else flags.set(k, true); }
    else args.push(a);
  }
  return { flags, args };
}

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  const { flags, args } = parseArgv(rest);
  switch (cmd) {
    case undefined: case 'stats': case 'usage': case 'status': await cmdStats({ json: flags.has('json'), quiet: flags.has('quiet'), includeLive: !flags.has('no-live'), force: flags.has('fresh') }); break;
    case 'watch': await cmdWatch(flags.get('every')); break;
    case 'list': case 'ls': cmdList(); break;
    case 'add': await cmdAdd(args[0], { email: flags.get('email'), force: flags.has('force') }); break;
    case 'remove': case 'rm': cmdRemove(args[0]); break;
    case 'add-gpt': await cmdAddGpt(args[0]); break;
    case 'priority': cmdPriority(args[0], args[1]); break;
    case 'prices': await cmdPrices({ refresh: flags.has('refresh'), 'no-check': flags.has('no-check') }); break;
    case 'session-cost': {
      // Printed for the status line: just the number, and nothing on stderr that could end up on Louis's prompt.
      const C = require('./cost.js');
      const r = C.sessionCost(args[0], C.loadPrices());
      if (r) process.stdout.write(`${r.usd.toFixed(2)}${r.partial ? '?' : ''}`);
      break;
    }
    case 'cost': cmdCost({ by: flags.get('by'), since: flags.get('since'), until: flags.get('until'), repo: flags.get('repo'), roots: flags.get('roots'), json: flags.has('json'), verbose: flags.has('verbose') }); break;
    case 'remove-gpt': cmdRemoveGpt(args[0]); break;
    case 'migrate-gpt': { const r = codexMigrate(args[0] || 'gpt-default'); log(`${r.action}: ${r.reason || r.to}`); break; }
    case 'switch': {
      // One namespace: the name itself says which domain to move. A ChatGPT switch is a symlink, a Claude switch is
      // a credential hand-off, and neither has anything to say about the other.
      const target = args[0];
      if (target && codexNames().includes(target)) { const r = await switchCodex(target); log(`gateway ChatGPT account: ${r.from || 'none'} -> ${r.to} (next request, no restart)`); }
      else await cmdSwitch(target, { failover: flags.has('failover') });
      break;
    }
    case 'adopt': await adoptLive(args[0]); break;
    case 'auto': await cmdAuto(args[0], { every: flags.get('every'), 'switch-at': flags.get('switch-at'), 'target-below': flags.get('target-below'), 'fable-ceiling': flags.get('fable-ceiling'), 'session-hot': flags.get('session-hot'), 'session-warm': flags.get('session-warm'), 'edf-lead-hours': flags.get('edf-lead-hours'), 'min-hold-minutes': flags.get('min-hold-minutes') }); break;
    case 'statusline': cmdStatusline(args[0]); break;
    case 'doctor': await cmdDoctor({ fix: flags.has('fix') }); break;
    case 'help': case '--help': case '-h': help(); break;
    default: help(); process.exitCode = 1;
  }
}
if (require.main === module) main().catch((e) => die(e.message));
else module.exports = { extractLogin, cleanupLogin, parseUsage, parseCodexUsage, cmdAddGpt, cmdAuto, parseArgv, VALUE_FLAGS, cmdCost, cmdPrices, attribute, whereFromSession, claudeProcs, paneForPid, codexCulprit, transcriptFor, readCodexBlocked, actOnCodex, codexNames, codexActive, loadCodexAccount, refreshCodex, codexUsageForAccount, switchCodex, decideCodex, codexMigrate, CODEX_STORE, CODEX_LIVE, CODEX_BLOCKED, CODEX_TIER_FALLBACK, h8, keychainService, acctLock, SWITCH_TRACE, renderRows, addBlockedBy, deadRemedy, liveLogin, net, DARWIN, CFG, CREDS_FILE, CLAUDE_JSON, REFRESH_LOCK, WRITE_LOCK, AUTO_LOG, METER, state, setState, names, loadAccount, saveAccount, liveStoreRead, liveStoreWrite, takeLock, LOCK_WAIT, sleepSync, usageFor, collect, hasTokens, isCheckedOut, inTransition, stripTokens, parkPair, pairFromRecord, claudeJsonAccount, profileOf, liveIdentity, reconcile, adoptLive, writeClaudeJsonAccount, switchTo, refresh, THRESHOLDS, thresholds, worstOf, fablePctOf, unusable, edfCompare, candidates, decide, limitRefusals, notify, resetsLine, cmdTick, TAG, systemdUnits, statuslineText, cmdStatusline, USER_SETTINGS, STATUSLINE_SH, cmdDoctor };
