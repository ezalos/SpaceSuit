// ABOUTME: Tests for claude-usage: the pure EDF failover decision, its candidate table and hysteresis, the credential hand-off and its locks, reconcile/adopt, the tick.
// ABOUTME: Everything runs against a temp HOME with a stubbed network; no real token is ever involved.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

const MOD = path.join(__dirname, 'claude-usage.js');

// A fresh module instance bound to a fresh temp HOME (os.homedir() follows $HOME on Linux and macOS).
function fresh({ cfgSub } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'cu-'));
  process.env.HOME = home;
  process.env.CLAUDE_USAGE_STORE = 'file';
  if (cfgSub) process.env.CLAUDE_CONFIG_DIR = path.join(home, cfgSub); else delete process.env.CLAUDE_CONFIG_DIR; // cfgSub: a second Claude Code profile, as CLAUDE_CONFIG_DIR makes one
  delete require.cache[MOD];
  const m = require(MOD);
  console.log = () => {}; // adoptLive/reconcile/switchTo log by design; node:test's reporter doesn't use console.log, keep stdout pristine
  fs.mkdirSync(path.join(home, '.claude'), { recursive: true, mode: 0o700 });
  fs.mkdirSync(m.CFG, { recursive: true, mode: 0o700 });
  return { home, m, cfg: m.CFG };
}

test('module binds to $HOME and exposes the network seam', () => {
  const t = fresh();
  assert.equal(t.m.CFG, path.join(t.home, '.claude'));
  assert.equal(t.m.CREDS_FILE, path.join(t.home, '.claude', '.credentials.json'));
  assert.equal(t.m.CLAUDE_JSON, path.join(t.home, '.claude.json'));
  assert.equal(typeof t.m.net.http, 'function');
  assert.equal(t.m.DARWIN, false);
});

const pair = (tag) => ({ accessToken: `at-${tag}`, refreshToken: `rt-${tag}`, expiresAt: Date.now() + 7 * 36e5, refreshTokenExpiresAt: Date.now() + 20 * 864e5, scopes: ['user:inference', 'user:profile'], subscriptionType: 'max', rateLimitTier: 'default_claude_max_20x' });
function seedLive(t, tag, extra = {}) { fs.writeFileSync(t.m.CREDS_FILE, JSON.stringify({ claudeAiOauth: pair(tag), mcpOAuth: { notion: 'keep-me' }, ...extra }), { mode: 0o600 }); }

const iso = (ms) => new Date(Date.now() + ms).toISOString(); // a reset time, ms from now
const usage = (five, week) => ({ five_hour: { utilization: five, resets_at: new Date(Date.now() + 36e5).toISOString() }, seven_day: { utilization: week, resets_at: new Date(Date.now() + 864e5).toISOString() } });
const usageAt = (five, week, resetInMs) => ({ five_hour: { utilization: five, resets_at: iso(36e5) }, seven_day: { utilization: week, resets_at: iso(resetInMs) } });
const profile = (uuid, email) => ({ account: { uuid, email, has_claude_max: true }, organization: { name: 'org', rate_limit_tier: 'default_claude_max_20x' } });
// Route every outbound call by the bearer it carries: usage/profile answers keyed by token, refresh answered by refresh token.
function stubNet(m, { usageByToken = {}, profileByToken = {}, refreshByToken = {} } = {}) {
  const calls = [];
  m.net.http = async (method, url, { headers = {}, body } = {}) => {
    const tok = (headers.Authorization || '').replace('Bearer ', '');
    calls.push({ method, url, tok });
    if (url.endsWith('/api/oauth/profile')) { const p = profileByToken[tok]; return p ? { status: 200, data: p, text: '' } : { status: 401, data: null, text: '' }; }
    if (url.includes('/api/oauth/usage')) { const u = usageByToken[tok]; return u ? { status: 200, data: u, text: '' } : { status: 401, data: null, text: '' }; }
    if (url.endsWith('/oauth/token')) { const rt = JSON.parse(body).refresh_token; const r = refreshByToken[rt]; return r ? { status: 200, data: r, text: '' } : { status: 400, data: { error: 'invalid_grant' }, text: '' }; }
    throw new Error(`unexpected ${method} ${url}`);
  };
  return calls;
}
function seedParked(t, name, tag, uuid, email) { t.m.saveAccount({ name, email, accountUuid: uuid, orgName: 'org', tier: 'default_claude_max_20x', plan: 'max', ...pair(tag), addedAt: new Date().toISOString() }); }
function seedCheckedOut(t, name, uuid, email) { t.m.saveAccount({ name, email, accountUuid: uuid, orgName: 'org', tier: 'default_claude_max_20x', plan: 'max', scopes: ['user:inference'], addedAt: new Date().toISOString(), checkedOut: new Date().toISOString() }); }
function seedClaudeJson(t, uuid, email) { fs.writeFileSync(t.m.CLAUDE_JSON, JSON.stringify({ oauthAccount: { accountUuid: uuid, emailAddress: email, organizationName: 'org', organizationRateLimitTier: 'default_claude_max_20x' }, hasCompletedOnboarding: true }), { mode: 0o600 }); }
// A machine on perso (adopted), with work parked.
async function machineOnPerso(t) {
  seedLive(t, 'perso'); seedClaudeJson(t, 'uuid-p', 'p@x'); seedParked(t, 'work', 'work', 'uuid-w', 'w@x');
  const calls = stubNet(t.m, { profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.adoptLive('perso');
  return calls;
}
const row = (name, five, week, extra = {}) => ({ name, usage: { session: { percent: five }, weekly: { percent: week }, scoped: [] }, ...extra });
const T = { switchAt: 95, targetBelow: 85, fableCeiling: 90, sessionHot: 85, sessionWarm: 65, edfLeadMs: 7_200_000 };

test('live store: absent → null; write is 0600, atomic, keeps sibling keys, bumps mtime', () => {
  const t = fresh();
  assert.equal(t.m.liveStoreRead(), null);
  seedLive(t, 'perso');
  const before = fs.statSync(t.m.CREDS_FILE).mtimeMs;
  t.m.sleepSync(20);
  const cur = t.m.liveStoreRead();
  t.m.liveStoreWrite({ ...cur, claudeAiOauth: pair('work') });
  const after = t.m.liveStoreRead();
  assert.equal(after.claudeAiOauth.accessToken, 'at-work');
  assert.deepEqual(after.mcpOAuth, { notion: 'keep-me' });
  assert.equal(fs.statSync(t.m.CREDS_FILE).mode & 0o777, 0o600);
  assert.ok(fs.statSync(t.m.CREDS_FILE).mtimeMs > before, 'mtime must move: that is what the client watches');
  assert.deepEqual(fs.readdirSync(t.cfg).filter((f) => f.endsWith('.tmp')), []);
});

test('takeLock: exclusive, times out with kind lock, reclaims a stale holder', () => {
  const t = fresh();
  const dir = path.join(t.cfg, '.storage-write.lock');
  const release = t.m.takeLock(dir, 15000, 300);
  assert.throws(() => t.m.takeLock(dir, 15000, 300), (e) => e.kind === 'lock');
  release();
  assert.equal(fs.existsSync(dir), false);
  fs.mkdirSync(dir); const old = new Date(Date.now() - 20000); fs.utimesSync(dir, old, old); // a holder that died 20 s ago
  const r2 = t.m.takeLock(dir, 15000, 300); r2();
  assert.equal(fs.existsSync(dir), false);
});

test('a checked-out record is metered with the live token, read-only', async () => {
  const t = fresh(); seedLive(t, 'perso'); seedCheckedOut(t, 'perso', 'uuid-p', 'p@x');
  const calls = stubNet(t.m, { usageByToken: { 'at-perso': usage(42, 10) } });
  const u = await t.m.usageFor(t.m.loadAccount('perso'));
  assert.equal(u.session.percent, 42);
  assert.deepEqual(calls.map((c) => c.method), ['GET'], 'no refresh POST for a checked-out grant');
});

test('a checked-out record whose live token is expired reads as idle, never refreshed', async () => {
  const t = fresh(); seedLive(t, 'perso'); seedCheckedOut(t, 'perso', 'uuid-p', 'p@x');
  stubNet(t.m, {});
  await assert.rejects(t.m.usageFor(t.m.loadAccount('perso')), (e) => e.kind === 'idle');
});

test('an in-transition record is never refreshed', async () => {
  const t = fresh(); seedParked(t, 'work', 'work', 'uuid-w', 'w@x');
  const a = t.m.loadAccount('work'); a.checkedOut = new Date().toISOString(); t.m.saveAccount(a);
  stubNet(t.m, {});
  await assert.rejects(t.m.usageFor(t.m.loadAccount('work')), (e) => e.kind === 'transition');
});

test('pairFromRecord rebuilds what the client wrote; parkPair captures it', () => {
  const t = fresh();
  const rec = { name: 'x', scopes: ['a'], plan: 'max', tier: 't', ...pair('x') };
  const p = t.m.pairFromRecord(rec);
  assert.equal(p.accessToken, 'at-x'); assert.equal(p.subscriptionType, 'max'); assert.equal(p.rateLimitTier, 'default_claude_max_20x');
  const parked = t.m.parkPair({ name: 'y' }, { ...pair('y'), clientId: 'cid' });
  assert.equal(parked.refreshToken, 'rt-y'); assert.equal(parked.oauthExtra.clientId, 'cid'); assert.ok(parked.parkedAt);
  assert.equal(t.m.pairFromRecord(parked).clientId, 'cid');
});

test('liveLogin reads the file on Linux', () => {
  const t = fresh(); seedLive(t, 'perso');
  fs.writeFileSync(t.m.CLAUDE_JSON, JSON.stringify({ oauthAccount: { accountUuid: 'uuid-p', emailAddress: 'p@x' } }));
  const l = t.m.liveLogin();
  assert.equal(l.email, 'p@x'); assert.equal(l.accountUuid, 'uuid-p'); assert.equal(l.live, true);
});

test('adopt: the live login becomes a checked-out record, home follows on first adopt', async () => {
  const t = fresh(); seedLive(t, 'perso'); seedClaudeJson(t, 'uuid-p', 'p@x');
  stubNet(t.m, { profileByToken: { 'at-perso': profile('uuid-p', 'p@x') } });
  await t.m.adoptLive('perso');
  const a = t.m.loadAccount('perso');
  assert.equal(a.accountUuid, 'uuid-p'); assert.ok(a.checkedOut); assert.equal(t.m.hasTokens(a), false);
  assert.deepEqual(a.claudeJsonAccount.emailAddress, 'p@x');
  assert.equal(t.m.state().live, 'perso'); assert.equal(t.m.state().home, 'perso');
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-perso', 'the live store is untouched');
});

test('adopt over an existing parked grant supersedes it', async () => {
  const t = fresh(); seedLive(t, 'perso2'); seedClaudeJson(t, 'uuid-p', 'p@x'); seedParked(t, 'perso', 'perso1', 'uuid-p', 'p@x');
  stubNet(t.m, { profileByToken: { 'at-perso2': profile('uuid-p', 'p@x') } });
  await t.m.adoptLive('perso');
  const a = t.m.loadAccount('perso');
  assert.equal(t.m.hasTokens(a), false); assert.ok(a.checkedOut);
});

test('adopt refuses when the live account is stored under another name', async () => {
  const t = fresh(); seedLive(t, 'perso'); seedClaudeJson(t, 'uuid-p', 'p@x'); seedParked(t, 'me', 'old', 'uuid-p', 'p@x');
  stubNet(t.m, { profileByToken: { 'at-perso': profile('uuid-p', 'p@x') } });
  await assert.rejects(t.m.adoptLive('perso'), /already stored as "me"/);
});

test('reconcile repairs a switch that died after park (live store still holds the old pair)', async () => {
  const t = fresh(); seedLive(t, 'perso'); seedClaudeJson(t, 'uuid-p', 'p@x');
  // perso: parked copy + checkedOut (in transition); work: checkedOut + tokens (in transition). Live store = perso's pair.
  t.m.saveAccount({ name: 'perso', accountUuid: 'uuid-p', email: 'p@x', ...pair('perso'), checkedOut: 'x' });
  t.m.saveAccount({ name: 'work', accountUuid: 'uuid-w', email: 'w@x', ...pair('work'), checkedOut: 'x' });
  stubNet(t.m, { profileByToken: { 'at-perso': profile('uuid-p', 'p@x') } });
  const owner = await t.m.reconcile(t.m.liveStoreRead());
  assert.equal(owner.name, 'perso');
  assert.equal(t.m.hasTokens(t.m.loadAccount('perso')), false); assert.ok(t.m.loadAccount('perso').checkedOut);
  assert.equal(t.m.hasTokens(t.m.loadAccount('work')), true); assert.equal(t.m.loadAccount('work').checkedOut, undefined);
  assert.equal(t.m.state().live, 'perso');
});

test('reconcile repairs a switch that died after install (live store holds the new pair)', async () => {
  const t = fresh(); seedLive(t, 'work'); seedClaudeJson(t, 'uuid-p', 'p@x'); // ~/.claude.json still says perso: the profile wins
  t.m.saveAccount({ name: 'perso', accountUuid: 'uuid-p', email: 'p@x', ...pair('perso'), checkedOut: 'x' });
  t.m.saveAccount({ name: 'work', accountUuid: 'uuid-w', email: 'w@x', ...pair('work'), checkedOut: 'x' });
  stubNet(t.m, { profileByToken: { 'at-work': profile('uuid-w', 'w@x') } });
  const owner = await t.m.reconcile(t.m.liveStoreRead());
  assert.equal(owner.name, 'work');
  assert.equal(t.m.hasTokens(t.m.loadAccount('work')), false); assert.ok(t.m.loadAccount('work').checkedOut);
  assert.equal(t.m.hasTokens(t.m.loadAccount('perso')), true); assert.equal(t.m.loadAccount('perso').checkedOut, undefined);
  assert.equal(t.m.state().live, 'work');
});

test('reconcile falls back to ~/.claude.json when the live token is expired', async () => {
  const t = fresh(); seedLive(t, 'perso'); seedClaudeJson(t, 'uuid-p', 'p@x'); seedCheckedOut(t, 'perso', 'uuid-p', 'p@x');
  stubNet(t.m, {});
  const owner = await t.m.reconcile(t.m.liveStoreRead());
  assert.equal(owner.name, 'perso');
});

test('switch: the pairs trade places under both locks, sessions see a new mtime, identity follows', async () => {
  const t = fresh(); const calls = await machineOnPerso(t);
  const before = fs.statSync(t.m.CREDS_FILE).mtimeMs; t.m.sleepSync(20);
  const r = await t.m.switchTo('work');
  assert.deepEqual(r, { from: 'perso', to: 'work', parked: true }); // parked: false only when the credential store was empty (a /logout)
  const live = t.m.liveStoreRead();
  assert.equal(live.claudeAiOauth.accessToken, 'at-work'); assert.equal(live.claudeAiOauth.refreshToken, 'rt-work');
  assert.equal(live.claudeAiOauth.rateLimitTier, 'default_claude_max_20x');
  assert.deepEqual(live.mcpOAuth, { notion: 'keep-me' });
  assert.ok(fs.statSync(t.m.CREDS_FILE).mtimeMs > before); assert.equal(fs.statSync(t.m.CREDS_FILE).mode & 0o777, 0o600);
  const w = t.m.loadAccount('work'); assert.ok(w.checkedOut); assert.equal(t.m.hasTokens(w), false);
  const p = t.m.loadAccount('perso'); assert.equal(p.checkedOut, undefined); assert.equal(p.accessToken, 'at-perso'); assert.equal(p.refreshToken, 'rt-perso'); assert.ok(p.parkedAt);
  assert.equal(p.oauthExtra.subscriptionType, 'max'); assert.equal(p.claudeJsonAccount.accountUuid, 'uuid-p');
  assert.equal(t.m.state().live, 'work'); assert.equal(t.m.state().home, 'work');
  assert.equal(JSON.parse(fs.readFileSync(t.m.CLAUDE_JSON, 'utf8')).oauthAccount.emailAddress, 'w@x');
  assert.equal(JSON.parse(fs.readFileSync(t.m.CLAUDE_JSON, 'utf8')).hasCompletedOnboarding, true, 'the rest of ~/.claude.json survives');
  assert.equal(fs.existsSync(t.m.REFRESH_LOCK), false); assert.equal(fs.existsSync(t.m.WRITE_LOCK), false);
  assert.ok(!calls.some((c) => c.method === 'POST'), 'a fresh target needs no refresh');
});

test('switch --failover moves live but not home; a bare switch toggles', async () => {
  const t = fresh(); await machineOnPerso(t);
  await t.m.switchTo('work', { failover: true });
  assert.equal(t.m.state().live, 'work'); assert.equal(t.m.state().home, 'perso');
  const r = await t.m.switchTo(); // toggle back
  assert.deepEqual(r, { from: 'work', to: 'perso', parked: true });
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-perso');
  assert.equal(t.m.loadAccount('work').refreshToken, 'rt-work', 'work is parked again with its pair intact');
});

test('switch: a hand switch stamps lastSwitchAt, so the hold window covers it too', async () => {
  const t = fresh(); await machineOnPerso(t);
  assert.equal(t.m.state().lastSwitchAt, undefined);
  await t.m.switchTo('work');
  assert.ok(Date.now() - t.m.state().lastSwitchAt < 5000);
});

test('switch refuses: same account, dead target, unknown live login', async () => {
  const t = fresh(); await machineOnPerso(t);
  await assert.rejects(t.m.switchTo('perso'), /already on perso/);
  const w = t.m.loadAccount('work'); w.dead = 'refresh rejected'; t.m.saveAccount(w);
  await assert.rejects(t.m.switchTo('work'), /refresh rejected/);
  seedLive(t, 'stranger'); seedClaudeJson(t, 'uuid-s', 's@x'); // someone ran /login with an unstored account (Claude Code rewrites oauthAccount at login)
  stubNet(t.m, { profileByToken: { 'at-stranger': profile('uuid-s', 's@x') } }); // a live /login means a verifiable token, not a fallback identity
  await assert.rejects(t.m.switchTo('work'), /not a stored account/);
});

test('switch refreshes a target whose access token is about to expire, before installing it', async () => {
  const t = fresh(); await machineOnPerso(t);
  const w = t.m.loadAccount('work'); w.expiresAt = Date.now() + 60e3; t.m.saveAccount(w);
  stubNet(t.m, { profileByToken: { 'at-perso': profile('uuid-p', 'p@x') }, refreshByToken: { 'rt-work': { access_token: 'at-work2', refresh_token: 'rt-work2', expires_in: 28800 } } });
  const inner = t.m.net.http; t.m.net.http = async (m, u, o) => { if (u.endsWith('/oauth/token')) assert.equal(fs.existsSync(t.m.REFRESH_LOCK), false, 'refresh must run before the client locks'); return inner(m, u, o); };
  await t.m.switchTo('work');
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-work2');
});

test('switch reclaims a stale refresh lock and gives up on a live one', async () => {
  const t = fresh(); await machineOnPerso(t);
  fs.mkdirSync(t.m.REFRESH_LOCK); const old = new Date(Date.now() - 90e3); fs.utimesSync(t.m.REFRESH_LOCK, old, old);
  await t.m.switchTo('work');
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-work');
  fs.mkdirSync(t.m.WRITE_LOCK); t.m.LOCK_WAIT.write = 300; // a client mid-write
  await assert.rejects(t.m.switchTo('perso'), (e) => e.kind === 'lock');
  assert.equal(fs.existsSync(t.m.REFRESH_LOCK), false); assert.equal(fs.existsSync(path.join(t.home, '.claude', 'claude-usage', 'switch.lock')), false);
  fs.rmdirSync(t.m.WRITE_LOCK);
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-work', 'nothing changed while the lock was held');
  assert.equal(t.m.loadAccount('work').checkedOut !== undefined, true);
  await t.m.switchTo('perso'); // the tool's own lock was never left behind: a plain switch works right after
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-perso');
});

test('switch: a failed target refresh leaves everything untouched and no lock held', async () => {
  const t = fresh(); await machineOnPerso(t);
  const w = t.m.loadAccount('work'); w.expiresAt = Date.now() + 60e3; t.m.saveAccount(w);
  stubNet(t.m, { profileByToken: { 'at-perso': profile('uuid-p', 'p@x') }, refreshByToken: {} }); // no entry: the POST 400s (invalid_grant)
  await assert.rejects(t.m.switchTo('work'), (e) => e.kind === 'refresh_failed');
  assert.ok(t.m.loadAccount('work').dead);
  const p = t.m.loadAccount('perso'); assert.ok(p.checkedOut); assert.equal(t.m.hasTokens(p), false);
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-perso');
  assert.equal(t.m.state().live, 'perso');
  assert.equal(fs.existsSync(t.m.REFRESH_LOCK), false); assert.equal(fs.existsSync(t.m.WRITE_LOCK), false);
  assert.equal(fs.existsSync(path.join(t.home, '.claude', 'claude-usage', 'switch.lock')), false);
});

test('reconcile refuses a destructive repair on fallback identity', async () => {
  const t = fresh(); seedLive(t, 'work'); seedClaudeJson(t, 'uuid-p', 'p@x'); // profile unreachable; ~/.claude.json still names perso
  t.m.saveAccount({ name: 'perso', accountUuid: 'uuid-p', email: 'p@x', ...pair('perso'), checkedOut: 'x' });
  t.m.saveAccount({ name: 'work', accountUuid: 'uuid-w', email: 'w@x', ...pair('work'), checkedOut: 'x' });
  stubNet(t.m, {}); // no profile for at-work: liveIdentity falls back to ~/.claude.json, source 'claudeJson'
  await assert.rejects(t.m.reconcile(t.m.liveStoreRead()), /run one claude session/);
  assert.ok(t.m.loadAccount('perso').checkedOut); assert.equal(t.m.hasTokens(t.m.loadAccount('perso')), true);
  assert.ok(t.m.loadAccount('work').checkedOut); assert.equal(t.m.hasTokens(t.m.loadAccount('work')), true);
});

test('reconcile repairs a stale ~/.claude.json on a verified identity', async () => {
  const t = fresh(); await machineOnPerso(t);
  seedClaudeJson(t, 'uuid-w', 'w@x'); // stale: still names work from before perso was adopted
  await t.m.reconcile(t.m.liveStoreRead());
  assert.equal(JSON.parse(fs.readFileSync(t.m.CLAUDE_JSON, 'utf8')).oauthAccount.accountUuid, 'uuid-p');
});

test("refresh adopts a sibling's newer pair instead of marking dead", async () => {
  const t = fresh(); seedParked(t, 'work', 'work', 'uuid-w', 'w@x');
  const a = t.m.loadAccount('work'); // this in-memory copy still carries the posted (now stale) refresh token
  const d = t.m.loadAccount('work'); d.refreshToken = 'rt-work2'; d.accessToken = 'at-work2'; t.m.saveAccount(d); // a sibling refreshed first
  stubNet(t.m, { refreshByToken: {} }); // the posted (stale) token is rejected server-side
  await t.m.refresh(a);
  assert.equal(a.refreshToken, 'rt-work2'); assert.equal(a.dead, undefined);
  assert.equal(t.m.loadAccount('work').dead, undefined);
});

test('decide: the hard rule moves to the soonest-resetting usable account, not the emptiest', () => {
  const t = fresh();
  const rows = [row('perso', 40, 96, { checkedOut: true }),
    { name: 'work', usage: { session: { percent: 30 }, weekly: { percent: 60, resetsAt: iso(3600e3) }, scoped: [] } },
    { name: 'spare', usage: { session: { percent: 10 }, weekly: { percent: 5, resetsAt: iso(5 * 864e5) }, scoped: [] } }];
  assert.equal(t.m.decide({ rows, live: 'perso', thresholds: T, exhaustedSince: null }).to, 'work', 'work resets in an hour: its 40% of headroom is about to be lost; spare keeps for five days');
  assert.equal(t.m.decide({ rows: [row('perso', 40, 94, { checkedOut: true }), row('work', 30, 80)], live: 'perso', thresholds: T, exhaustedSince: null }).action, 'none', 'one under every bound, no lead, no warm window');
});

test('decide: the weekly window and the Fable floor count too', () => {
  const t = fresh();
  const rows = [{ name: 'perso', checkedOut: true, usage: { session: { percent: 10 }, weekly: { percent: 20 }, scoped: [{ label: 'Fable', percent: 97 }] } }, row('work', 30, 20)];
  assert.equal(t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null }).action, 'failover');
});

test('decide: exhausted notifies once per episode', () => {
  const t = fresh();
  const rows = [row('perso', 40, 99, { checkedOut: true }), row('work', 90, 20)];
  const d1 = t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null });
  assert.equal(d1.action, 'exhausted'); assert.equal(d1.notify, true);
  const d2 = t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: '2026-09-15T00:00:00Z' });
  assert.equal(d2.action, 'exhausted'); assert.equal(d2.notify, false);
});

test('decide: home is a marker, not a rule — the hand-picked account comes back only when EDF says so', () => {
  const t = fresh();
  const live = { name: 'work', checkedOut: true, usage: { session: { percent: 30 }, weekly: { percent: 25, resetsAt: iso(3 * 864e5) }, scoped: [] } };
  const home = (resetMs, sess = 49) => ({ name: 'perso', usage: { session: { percent: sess }, weekly: { percent: 40, resetsAt: iso(resetMs) }, scoped: [] } });
  assert.equal(t.m.decide({ rows: [live, home(3 * 864e5 - 3 * 3600e3)], live: 'work', thresholds: T, exhaustedSince: null }).action, 'rebalance', 'a 3 h lead brings it back');
  assert.equal(t.m.decide({ rows: [live, home(3 * 864e5 - 3600e3)], live: 'work', thresholds: T, exhaustedSince: null }).action, 'none', 'a 1 h lead does not, however "recovered" it looks');
  assert.equal(t.m.decide({ rows: [live, home(3 * 864e5 - 3 * 3600e3, 90)], live: 'work', thresholds: T, exhaustedSince: null }).action, 'none', 'a hot 5-hour window is never entered — the 22:36 failback that walked into 97%');
});

test('decide: rows in error are never a target, an unreadable live row means no decision', () => {
  const t = fresh();
  assert.equal(t.m.decide({ rows: [row('perso', 40, 99, { checkedOut: true }), { name: 'work', error: 'dead' }], live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null }).action, 'exhausted');
  assert.equal(t.m.decide({ rows: [{ name: 'perso', error: 'idle', checkedOut: true }, row('work', 1, 1)], live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null }).action, 'none');
});

// ---------- a window nobody could read is not a window at 0% ----------
// 2026-09-18, reported by Louis: the watcher could hand the machine to an account whose availability it had never
// established. Both windows have to answer before an account is a target, and "no answer" is not "empty".
test('worstOf: nothing readable is unknown, not 0%', () => {
  const t = fresh();
  assert.equal(t.m.worstOf({ session: null, weekly: null, scoped: [] }), null);
  assert.equal(t.m.worstOf({ session: { percent: null }, weekly: null, scoped: [] }), null, 'a window with no number is no evidence');
  assert.equal(t.m.worstOf({ session: { percent: 0 }, weekly: null, scoped: [] }), 0, 'a real 0% is still a real 0%');
});

test('fablePctOf: reads only the Fable-labelled scoped window, 0 when absent', () => {
  const t = fresh();
  assert.equal(t.m.fablePctOf({ scoped: [{ label: 'Fable', percent: 91 }, { label: 'Sonnet', percent: 10 }] }), 91);
  assert.equal(t.m.fablePctOf({ scoped: [{ label: 'Sonnet', percent: 10 }] }), 0);
  assert.equal(t.m.fablePctOf({ scoped: [] }), 0);
});

test('unusable: the 5-hour window alone can rule an account out, under the bound the caller chooses', () => {
  const t = fresh();
  assert.match(t.m.unusable(row('x', 90, 5), T, T.sessionHot), /5h 90% ≥ 85% cap/);
  assert.equal(t.m.unusable(row('y', 70, 5), T, T.sessionHot), null, 'under the hard-rule bound, 70 is room');
  assert.match(t.m.unusable(row('y', 70, 5), T, T.sessionWarm), /5h 70% ≥ 65% cap/, 'under the warm bound it is not enterable');
  assert.match(t.m.unusable(row('z', 0, 96), T, T.sessionHot), /weekly 96% ≥ 95% ceiling/);
  assert.match(t.m.unusable({ name: 'f', usage: { session: { percent: 0 }, weekly: { percent: 10 }, scoped: [{ label: 'Fable', percent: 91 }] } }, T, T.sessionHot), /Fable 91% ≥ 90% floor/);
  assert.match(t.m.unusable({ name: 'l', usage: { session: { percent: 5, locked: 'five_hour_limit_reached' }, weekly: { percent: 10 }, scoped: [] } }, T, T.sessionHot), /locked/);
  assert.match(t.m.unusable({ name: 'h', usage: { session: { percent: 3 }, weekly: null, scoped: [] } }, T, T.sessionHot), /weekly window unreadable/);
});

test('edfCompare: priority first, then the soonest weekly reset, then the most headroom; unknown reset last', () => {
  const t = fresh();
  const c = (priority, resetsAtMs, weekly) => ({ priority, resetsAtMs, weekly });
  assert.ok(t.m.edfCompare(c(1, 1000, 0), c(0, 9000, 90)) > 0, 'a last-resort account sorts after a preferred one whatever its reset');
  assert.ok(t.m.edfCompare(c(0, 1000, 80), c(0, 120e3, 5)) < 0, 'the soonest reset wins over the emptier account (different minutes)');
  assert.ok(t.m.edfCompare(c(0, 1000, 80), c(0, 9000, 5)) > 0, 'eight seconds apart is the same deadline: the emptier account goes first');
  assert.ok(t.m.edfCompare(c(0, 60e3, 10), c(0, 90e3, 40)) < 0, 'same minute: the most headroom (lowest weekly %) first');
  assert.ok(t.m.edfCompare(c(0, Infinity, 5), c(0, 9000, 90)) > 0, 'an unknown reset sorts last');
  assert.equal(t.m.edfCompare(c(0, Infinity, 10), c(0, Infinity, 10)), 0, 'two unknowns tie on headroom, not NaN');
});

test('candidates: EDF-ordered, with the hard-rule reason and the warm flag per row', () => {
  const t = fresh();
  const rows = [
    row('live', 0, 10, { checkedOut: true }),
    { name: 'a', usage: { session: { percent: 80 }, weekly: { percent: 20, resetsAt: iso(3600e3) }, scoped: [] } },
    { name: 'b', usage: { session: { percent: 10 }, weekly: { percent: 20, resetsAt: iso(864e5) }, scoped: [] } },
    { name: 'c', usage: { session: { percent: 90 }, weekly: { percent: 20, resetsAt: iso(60e3) }, scoped: [] } },
    { name: 'gpt', provider: 'openai', usage: { session: null, weekly: { percent: 1 }, scoped: [] } },
  ];
  const { parked, next } = t.m.candidates({ rows, live: 'live', thresholds: T });
  assert.deepEqual(parked.map((p) => p.name), ['c', 'a', 'b'], 'soonest reset first; the ChatGPT row is never a candidate');
  assert.match(parked[0].blocked, /5h 90%/);
  assert.equal(parked[1].blocked, null); assert.equal(parked[1].warm, false, 'a at 5h 80: usable in an emergency, not enterable proactively');
  assert.equal(parked[2].warm, true);
  assert.equal(next.name, 'b', 'the first row a proactive rotation may enter');
  const refusedNow = t.m.candidates({ rows, live: 'live', thresholds: T, limited: { b: new Date().toISOString() } });
  assert.match(refusedNow.parked.find((p) => p.name === 'b').blocked, /refused/);
  assert.equal(refusedNow.next, null);
});

test('decide: EDF — the soonest weekly reset wins among usable candidates', () => {
  const t = fresh();
  const rows = [
    { name: 'live', checkedOut: true, usage: { session: { percent: 0 }, weekly: { percent: 96 }, scoped: [] } },
    { name: 'day', usage: { session: { percent: 0 }, weekly: { percent: 30, resetsAt: iso(864e5) }, scoped: [] } },
    { name: 'hour', usage: { session: { percent: 0 }, weekly: { percent: 55, resetsAt: iso(3600e3) }, scoped: [] } },
    { name: 'week', usage: { session: { percent: 0 }, weekly: { percent: 5, resetsAt: iso(5 * 864e5) }, scoped: [] } },
  ];
  const d = t.m.decide({ rows, live: 'live', thresholds: T, exhaustedSince: null });
  assert.equal(d.action, 'failover'); assert.equal(d.to, 'hour', 'not the emptiest (week), not the middle one: the one whose headroom vanishes first');
});

test('decide: an account whose windows did not parse is never the failover target', () => {
  const t = fresh();
  const blank = { name: 'blank', usage: { session: null, weekly: null, scoped: [] } };
  const d = t.m.decide({ rows: [row('work', 99, 99, { checkedOut: true }), blank], live: 'work', home: 'work', thresholds: T });
  assert.equal(d.action, 'exhausted', 'scored as 0% it sorted ahead of every real candidate and won every failover');
  assert.notEqual(d.to, 'blank');
});

test('decide: half a reading is not a reading — both windows must answer', () => {
  const t = fresh();
  // 5-hour at 3% looks like the emptiest account on the table; the weekly window, the one that actually blocks, is missing.
  const half = { name: 'half', usage: { session: { percent: 3 }, weekly: null, scoped: [] } };
  const d = t.m.decide({ rows: [row('work', 40, 99, { checkedOut: true }), half], live: 'work', home: 'work', thresholds: T });
  assert.equal(d.action, 'exhausted', 'an unread weekly window is not room in the weekly window');

  // ...and the same the other way round, so neither half can carry the decision alone.
  const other = { name: 'other', usage: { session: null, weekly: { percent: 3 }, scoped: [] } };
  assert.equal(t.m.decide({ rows: [row('work', 40, 99, { checkedOut: true }), other], live: 'work', home: 'work', thresholds: T }).action, 'exhausted');
});

test('decide: a locked window is unavailable at any percentage', () => {
  const t = fresh();
  const locked = { name: 'locked', usage: { session: { percent: 5 }, weekly: { percent: 10, locked: 'weekly_limit_reached' }, scoped: [] } };
  const d = t.m.decide({ rows: [row('work', 40, 99, { checkedOut: true }), locked], live: 'work', home: 'work', thresholds: T });
  assert.equal(d.action, 'exhausted', 'the number describes the budget, the lock describes the door');
});

test('decide: an unreadable live row is unreadable, not an account sitting at 0%', () => {
  const t = fresh();
  const rows = [{ name: 'work', checkedOut: true, usage: { session: null, weekly: null, scoped: [] } }, row('spare', 10, 10)];
  const d = t.m.decide({ rows, live: 'work', home: 'work', thresholds: T, blind: 0 });
  assert.equal(d.liveUnreadable, true, 'reading 0% it would have reported a healthy machine forever');
  assert.match(d.reason, /no window readable/);
  assert.equal(t.m.decide({ rows, live: 'work', home: 'work', thresholds: T, blind: 3 }).to, 'spare', 'and the blind streak still moves it');
});

test('decide: the warning names why a preferred account had no room, never a percentage it does not have', () => {
  const t = fresh();
  const rows = [row('work', 99, 99, { checkedOut: true }), row('perso', 0, 0, { priority: 1 }),
    { name: 'work2', usage: { session: { percent: 4 }, weekly: null, scoped: [] } }];
  const d = t.m.decide({ rows, live: 'work', home: 'work', thresholds: T });
  assert.equal(d.to, 'perso');
  assert.match(d.warn, /work2 weekly window unreadable/, 'saying "work2 4%" would read as though it had room and was passed over');
});

// ---------- hard-rule triggers: switchAt backstop, Fable floor, locked window, and the EDF hold/hysteresis window ----------
test('decide: fableCeiling fires independently of the other windows, and bypasses the hold window', () => {
  const t = fresh();
  const rows = [{ name: 'live', checkedOut: true, usage: { session: { percent: 0 }, weekly: { percent: 20 }, scoped: [{ label: 'Fable', percent: 91 }] } }, row('spare', 10, 5)];
  const d = t.m.decide({ rows, live: 'live', home: 'live', thresholds: T, exhaustedSince: null, lastSwitchAt: Date.now() });
  assert.equal(d.action, 'failover'); assert.equal(d.to, 'spare');
  assert.match(d.reason, /Fable 91% ≥ 90% floor/);
});

test('decide: the hold window suppresses a rotation, never the hard rule', () => {
  const t = fresh();
  const cand = { name: 'cand', usage: { session: { percent: 5 }, weekly: { percent: 10, resetsAt: iso(3600e3) }, scoped: [] } };
  const live = (week) => ({ name: 'live', checkedOut: true, usage: { session: { percent: 0 }, weekly: { percent: week, resetsAt: iso(5 * 864e5) }, scoped: [] } });
  assert.equal(t.m.decide({ rows: [live(60), cand], live: 'live', thresholds: T, exhaustedSince: null, lastSwitchAt: Date.now() }).action, 'none', 'a five-day lead, but inside the hold');
  assert.equal(t.m.decide({ rows: [live(60), cand], live: 'live', thresholds: T, exhaustedSince: null, lastSwitchAt: Date.now() - 11 * 60e3 }).action, 'rebalance');
  assert.equal(t.m.decide({ rows: [live(96), cand], live: 'live', thresholds: T, exhaustedSince: null, lastSwitchAt: Date.now() }).action, 'failover', 'the hard rule is never held');
});

test('decide: a locked window on live forces a move regardless of the other windows', () => {
  const t = fresh();
  const rows = [
    { name: 'live', checkedOut: true, usage: { session: { percent: 100, locked: 'five_hour_limit_reached' }, weekly: { percent: 10 }, scoped: [] } },
    row('spare', 10, 5),
  ];
  const d = t.m.decide({ rows, live: 'live', home: 'live', thresholds: T, exhaustedSince: null });
  assert.equal(d.action, 'failover'); assert.equal(d.to, 'spare');
  assert.match(d.reason, /locked/);
});

test('decide: the 2026-09-21 incident — a 98% 5-hour window on live moves the machine', () => {
  const t = fresh();
  const rows = [
    { name: 'work7', checkedOut: true, usage: { session: { percent: 98 }, weekly: { percent: 22 }, scoped: [{ label: 'Fable', percent: 12 }] } },
    { name: 'work6', usage: { session: { percent: 0 }, weekly: { percent: 26 }, scoped: [{ label: 'Fable', percent: 11 }] } },
    { name: 'perso', priority: 1, usage: { session: { percent: 53 }, weekly: { percent: 19 }, scoped: [{ label: 'Fable', percent: 8 }] } },
    { name: 'work', usage: { session: { percent: 0 }, weekly: { percent: 100 }, scoped: [{ label: 'Fable', percent: 100 }] } },
  ];
  const d = t.m.decide({ rows, live: 'work7', thresholds: T, exhaustedSince: null });
  assert.equal(d.action, 'failover'); assert.equal(d.to, 'work6', 'the usable preferred account, never perso');
  assert.match(d.reason, /5h 98%/);
});

test('decide: a usable live account never rotates onto the last-resort tier (the 22:47 log line)', () => {
  const t = fresh();
  const rows = [
    { name: 'work7', checkedOut: true, usage: { session: { percent: 70 }, weekly: { percent: 16 }, scoped: [{ label: 'Fable', percent: 10 }] } },
    { name: 'perso', priority: 1, usage: { session: { percent: 0 }, weekly: { percent: 7 }, scoped: [{ label: 'Fable', percent: 4 }] } },
    row('work6', 0, 100), row('work2', 0, 100),
  ];
  const d = t.m.decide({ rows, live: 'work7', thresholds: T, exhaustedSince: null });
  assert.equal(d.action, 'none', 'live can serve (5h 70 < 85, weekly 16, Fable 10): perso is reachable only through the hard rule');
  const hot = { ...rows[0], usage: { ...rows[0].usage, session: { percent: 96 } } };
  const d2 = t.m.decide({ rows: [hot, ...rows.slice(1)], live: 'work7', thresholds: T, exhaustedSince: null });
  assert.equal(d2.action, 'failover'); assert.equal(d2.to, 'perso'); assert.match(d2.warn, /perso is deprioritised/);
});

test('decide: a preferred account becoming usable pulls the machine off the last-resort tier at once', () => {
  const t = fresh();
  const perso = { name: 'perso', checkedOut: true, priority: 1, usage: { session: { percent: 20 }, weekly: { percent: 30, resetsAt: iso(6 * 864e5) }, scoped: [] } };
  const cool = { name: 'work', usage: { session: { percent: 10 }, weekly: { percent: 40, resetsAt: iso(6 * 864e5 - 3600e3) }, scoped: [] } }; // leads by 1 h only — under edfLead
  const d = t.m.decide({ rows: [perso, cool], live: 'perso', thresholds: T, exhaustedSince: null });
  assert.equal(d.action, 'rebalance'); assert.equal(d.to, 'work');
  const warmish = { ...cool, usage: { ...cool.usage, session: { percent: 70 } } };
  assert.equal(t.m.decide({ rows: [perso, warmish], live: 'perso', thresholds: T, exhaustedSince: null }).action, 'none', 'over sessionWarm it is not enterable proactively; perso stays until the hard rule or a cooler account');
});

test('decide: hysteresis — rotate for a real EDF lead or a warm live 5-hour window, not for an hour', () => {
  const t = fresh();
  const live = (sess) => ({ name: 'live', checkedOut: true, usage: { session: { percent: sess }, weekly: { percent: 30, resetsAt: iso(3 * 864e5) }, scoped: [] } });
  const cand = (resetMs) => ({ name: 'cand', usage: { session: { percent: 5 }, weekly: { percent: 40, resetsAt: iso(resetMs) }, scoped: [] } });
  assert.equal(t.m.decide({ rows: [live(10), cand(3 * 864e5 - 3600e3)], live: 'live', thresholds: T, exhaustedSince: null }).action, 'none', 'a 1 h lead is under edfLead');
  assert.equal(t.m.decide({ rows: [live(10), cand(3 * 864e5 - 3 * 3600e3)], live: 'live', thresholds: T, exhaustedSince: null }).action, 'rebalance', 'a 3 h lead is a rotation');
  assert.equal(t.m.decide({ rows: [live(70), cand(3 * 864e5 - 3600e3)], live: 'live', thresholds: T, exhaustedSince: null }).action, 'rebalance', 'a warm live window rotates even without a lead');
});

test('decide: a live account with no weekly deadline yet rotates to a known reset, and the log line says so', () => {
  const t = fresh();
  // A fresh account: the meter reports resetsAt null on a window nothing has been spent on. An unknown deadline is no
  // urgency, so a known one leads it by "infinity" — the reason must say that in words, never print Infinityh.
  const live = { name: 'live', checkedOut: true, usage: { session: { percent: 0 }, weekly: { percent: 0, resetsAt: null }, scoped: [] } };
  const cand = { name: 'cand', usage: { session: { percent: 5 }, weekly: { percent: 40, resetsAt: iso(864e5) }, scoped: [] } };
  const d = t.m.decide({ rows: [live, cand], live: 'live', thresholds: T, exhaustedSince: null });
  assert.equal(d.action, 'rebalance'); assert.equal(d.to, 'cand');
  assert.match(d.reason, /live has no weekly deadline yet/);
  assert.doesNotMatch(d.reason, /Infinity|NaN/);
});

test('decide: the two 5-hour bounds — rotation never enters a warm account, the hard rule takes any room', () => {
  const t = fresh();
  const cand = { name: 'cand', usage: { session: { percent: 80 }, weekly: { percent: 10, resetsAt: iso(3600e3) }, scoped: [] } };
  const warmLive = { name: 'live', checkedOut: true, usage: { session: { percent: 70 }, weekly: { percent: 30, resetsAt: iso(5 * 864e5) }, scoped: [] } };
  assert.equal(t.m.decide({ rows: [warmLive, cand], live: 'live', thresholds: T, exhaustedSince: null }).action, 'none', 'cand leads by five days, but at 5h 80 it is not enterable');
  const hotLive = { ...warmLive, usage: { ...warmLive.usage, session: { percent: 90 } } };
  const d = t.m.decide({ rows: [hotLive, cand], live: 'live', thresholds: T, exhaustedSince: null });
  assert.equal(d.action, 'failover'); assert.equal(d.to, 'cand', 'in an emergency, 80 is room');
});

test('decide: two accounts resetting minutes apart never ping-pong inside a hold window', () => {
  const t = fresh();
  const a = iso(864e5), b = iso(864e5 + 10 * 60e3);
  const mk = (live, liveSess, otherSess) => [
    { name: 'A', checkedOut: live === 'A', usage: { session: { percent: live === 'A' ? liveSess : otherSess }, weekly: { percent: 30, resetsAt: a }, scoped: [] } },
    { name: 'B', checkedOut: live === 'B', usage: { session: { percent: live === 'B' ? liveSess : otherSess }, weekly: { percent: 30, resetsAt: b }, scoped: [] } },
  ];
  const d1 = t.m.decide({ rows: mk('A', 70, 10), live: 'A', thresholds: T, exhaustedSince: null, lastSwitchAt: 0 });
  assert.equal(d1.action, 'rebalance'); assert.equal(d1.to, 'B', 'A is warm, B is cool: rotate');
  const d2 = t.m.decide({ rows: mk('B', 20, 60), live: 'B', thresholds: T, exhaustedSince: null, lastSwitchAt: Date.now() - 60e3 });
  assert.equal(d2.action, 'none', 'A leads by ten minutes only and B is cool: nothing to do');
  assert.match(d2.reason, /lead .* < 2h/, 'no trigger, not a hold');
  const d3 = t.m.decide({ rows: mk('B', 70, 10), live: 'B', thresholds: T, exhaustedSince: null, lastSwitchAt: Date.now() - 5 * 60e3 });
  assert.equal(d3.action, 'none', 'B warmed, but inside the hold: at most one move per window');
  assert.match(d3.reason, /\(hold\)/, 'a trigger, held');
  const d4 = t.m.decide({ rows: mk('B', 70, 10), live: 'B', thresholds: T, exhaustedSince: null, lastSwitchAt: Date.now() - 11 * 60e3 });
  assert.equal(d4.action, 'rebalance'); assert.equal(d4.to, 'A', 'past the hold, one rotation back');
});

test('decide: the hard rule fires per window, each on its own', () => {
  const t = fresh();
  const spare = row('spare', 10, 5);
  const on = (usage) => t.m.decide({ rows: [{ name: 'live', checkedOut: true, usage }, spare], live: 'live', thresholds: T, exhaustedSince: null, lastSwitchAt: Date.now() });
  assert.equal(on({ session: { percent: 85 }, weekly: { percent: 10 }, scoped: [] }).action, 'failover', '5-hour at sessionHot');
  assert.equal(on({ session: { percent: 0 }, weekly: { percent: 95 }, scoped: [] }).action, 'failover', 'weekly at switchAt');
  assert.equal(on({ session: { percent: 60 }, weekly: { percent: 94 }, scoped: [{ label: 'Fable', percent: 89 }] }).action, 'none', 'one under each bound (and under warm): nothing fires');
});

test('decideCodex: a plan with no 5-hour window is still a target; one with no reading at all is not', () => {
  const t = fresh();
  const gpt = (name, u, extra = {}) => ({ name, provider: 'openai', usage: u, ...extra });
  // Pro Lite genuinely has no 5-hour window, so session: null there is the shape of the plan, not a failed read.
  const lite = gpt('gpt-lite', { session: null, weekly: { percent: 2 }, scoped: [] });
  const d = t.m.decideCodex({ rows: [gpt('gpt-work', { session: null, weekly: { percent: 99 }, scoped: [] }, { checkedOut: true }), lite], active: 'gpt-work', thresholds: T });
  assert.equal(d.to, 'gpt-lite');

  const dark = gpt('gpt-dark', { session: null, weekly: null, scoped: [] });
  const d2 = t.m.decideCodex({ rows: [gpt('gpt-work', { session: null, weekly: { percent: 99 }, scoped: [] }, { checkedOut: true }), dark], active: 'gpt-work', thresholds: T });
  assert.equal(d2.action, 'exhausted');
});

test('thresholds: defaults, overridden by state', () => {
  const t = fresh();
  const defaults = { switchAt: 95, targetBelow: 85, fableCeiling: 90, sessionHot: 85, sessionWarm: 65, edfLeadMs: 7_200_000, minHoldMs: 600_000, blindAfter: 3 };
  assert.deepEqual(t.m.thresholds(), defaults);
  t.m.setState({ thresholds: { switchAt: 90 } });
  assert.deepEqual(t.m.thresholds(), { ...defaults, switchAt: 90 });
});

test('cmdAuto on: the EDF thresholds are settable by flag, persist, and the retired keys are dropped', async () => {
  const t = fresh();
  t.m.setState({ thresholds: { gapThreshold: 100, sessionWeight: 1, tieBandPoints: 15, homeBelow: 50 } }); // what the interim mitigation left behind
  await t.m.cmdAuto('on', { every: 60, 'session-hot': 80, 'session-warm': 60, 'edf-lead-hours': 3, 'min-hold-minutes': 5, 'fable-ceiling': 88 });
  const th = t.m.thresholds();
  assert.equal(th.sessionHot, 80); assert.equal(th.sessionWarm, 60); assert.equal(th.edfLeadMs, 3 * 3_600_000);
  assert.equal(th.minHoldMs, 5 * 60_000); assert.equal(th.fableCeiling, 88);
  for (const dead of ['gapThreshold', 'sessionWeight', 'tieBandPoints', 'homeBelow']) assert.equal(th[dead], undefined, `${dead} is retired`);
});

test('parseArgv: every flag `auto on` reads takes a space-separated value', () => {
  const t = fresh();
  for (const f of ['every', 'switch-at', 'target-below', 'fable-ceiling', 'session-hot', 'session-warm', 'edf-lead-hours', 'min-hold-minutes']) assert.ok(t.m.VALUE_FLAGS.includes(f), f);
  for (const f of ['gap-threshold', 'session-weight', 'tie-band', 'home-below']) assert.ok(!t.m.VALUE_FLAGS.includes(f), `${f} is retired`);
  const { flags, args } = t.m.parseArgv(['on', '--session-hot', '80', '--edf-lead-hours', '3', '--fresh']);
  assert.equal(flags.get('session-hot'), '80'); assert.equal(flags.get('edf-lead-hours'), '3'); assert.equal(flags.get('fresh'), true);
  assert.deepEqual(args, ['on']);
});

function seedTelegram(t) { const d = path.join(t.home, '.claude', 'channels', 'telegram'); fs.mkdirSync(d, { recursive: true }); fs.writeFileSync(path.join(d, '.env'), 'TELEGRAM_BOT_TOKEN=bot-x\n'); fs.writeFileSync(path.join(d, 'access.json'), JSON.stringify({ allowFrom: [42] })); }

test('notify: always logs; sends through the channel bot with the naming prefix when creds exist', async () => {
  const t = fresh();
  await t.m.notify('hello');
  assert.match(fs.readFileSync(t.m.AUTO_LOG, 'utf8'), /hello/);
  seedTelegram(t);
  const calls = []; t.m.net.http = async (method, url, { body }) => { calls.push({ method, url, body: JSON.parse(body) }); return { status: 200, data: {}, text: '' }; };
  await t.m.notify('failover perso → work');
  assert.equal(calls.length, 1); assert.match(calls[0].url, /api\.telegram\.org\/botbot-x\/sendMessage/);
  assert.equal(calls[0].body.chat_id, 42); assert.match(calls[0].body.text, /^🖥️ \[claude-usage\] \S+: failover perso → work$/);
});

test('resetsLine: names the window that is actually spent, per its own bound', () => {
  const t = fresh();
  const hot = { name: 'h', usage: { session: { percent: 90, resetsAt: iso(3600e3) }, weekly: { percent: 10, resetsAt: iso(864e5) }, scoped: [] } };
  const fine = { name: 'f', usage: { session: { percent: 50 }, weekly: { percent: 40 }, scoped: [] } };
  const line = t.m.resetsLine([hot, fine], T);
  assert.match(line, /h resets in (59m|1h00)/, 'the 5-hour window is the spent one, so its reset is the one that matters');
  assert.match(line, /f 50%/);
});

test('tick: the hard rule fails over and logs without paging; home comes back only through hysteresis', async () => {
  const t = fresh(); await machineOnPerso(t); seedTelegram(t);
  const usageBy = { 'at-perso': usage(0, 96), 'at-work': usage(20, 12) };
  stubNet(t.m, { usageByToken: usageBy, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'work'); assert.equal(t.m.state().home, 'perso');
  assert.equal(t.m.state().lastDecision.action, 'failover');
  const log1 = fs.readFileSync(t.m.AUTO_LOG, 'utf8');
  assert.match(log1, /failover perso → work/); assert.doesNotMatch(log1, /telegram/, 'a successful move is logged, not paged');
  usageBy['at-perso'] = usage(0, 30); usageBy['at-work'] = usage(70, 12); // perso recovered; work's 5-hour window is warm, so rotation is due
  ageMeter(t, { perso: 6 * 60e3, work: 60e3 });
  t.m.setState({ lastSwitchAt: Date.now() - 11 * 60e3 }); // eleven minutes later: past the hold
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'perso'); assert.equal(t.m.state().lastDecision.action, 'rebalance');
});

test('tick: both exhausted notifies once, and the episode ends when the live account drops back', async () => {
  const t = fresh(); await machineOnPerso(t);
  const usageBy = { 'at-perso': usage(0, 99), 'at-work': usage(90, 20) };
  stubNet(t.m, { usageByToken: usageBy, profileByToken: { 'at-perso': profile('uuid-p', 'p@x') } });
  await t.m.cmdTick(); ageMeter(t, { perso: 60e3, work: 6 * 60e3 }); await t.m.cmdTick();
  const lines = fs.readFileSync(t.m.AUTO_LOG, 'utf8').split('\n').filter((l) => l.includes('no switch'));
  assert.equal(lines.length, 1); assert.match(lines[0], /perso resets in/);
  assert.ok(t.m.state().exhaustedSince);
  usageBy['at-perso'] = usage(10, 40);
  ageMeter(t, { perso: 60e3, work: 6 * 60e3 });
  await t.m.cmdTick();
  assert.equal(t.m.state().exhaustedSince, null);
});

test('tick: a failed switch notifies once per episode and records the error', async () => {
  const t = fresh(); await machineOnPerso(t);
  const usageBy = { 'at-perso': usage(0, 96), 'at-work': usage(20, 12) };
  stubNet(t.m, { usageByToken: usageBy, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  fs.mkdirSync(t.m.WRITE_LOCK); t.m.LOCK_WAIT.write = 300; // a client mid-write: switchTo's own write lock never yields
  await t.m.cmdTick();
  assert.equal(t.m.state().lastDecision.action, 'failover'); assert.ok(t.m.state().lastDecision.error);
  assert.ok(t.m.state().switchFailedSince); assert.equal(t.m.state().live, 'perso');
  assert.match(fs.readFileSync(t.m.AUTO_LOG, 'utf8'), /FAILED/);
  await t.m.cmdTick();
  const failedLines = fs.readFileSync(t.m.AUTO_LOG, 'utf8').split('\n').filter((l) => l.includes('FAILED'));
  assert.equal(failedLines.length, 1, 'notified once per episode, not on every retry');
  fs.rmdirSync(t.m.WRITE_LOCK); t.m.LOCK_WAIT.write = 30e3;
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'work'); assert.equal(t.m.state().switchFailedSince, null);
});

test('tick: an EDF lead rotates without paging, and the hold window holds the next one', async () => {
  const t = fresh(); await machineOnPerso(t); seedTelegram(t);
  const usageBy = { 'at-perso': usageAt(0, 60, 864e5), 'at-work': usageAt(0, 5, 3600e3) }; // work resets in an hour, 23 h ahead of perso
  stubNet(t.m, { usageByToken: usageBy, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'work'); assert.equal(t.m.state().lastDecision.action, 'rebalance');
  assert.ok(Date.now() - t.m.state().lastSwitchAt < 5000, 'stamped by the switch itself');
  const log1 = fs.readFileSync(t.m.AUTO_LOG, 'utf8');
  assert.match(log1, /rebalance perso → work/); assert.doesNotMatch(log1, /telegram/);
  usageBy['at-work'] = usageAt(70, 5, 3600e3); // work warms: a rotation back is due, but the machine just moved
  ageMeter(t, { perso: 6 * 60e3, work: 60e3 });
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'work', 'held'); assert.equal(t.m.state().lastDecision.action, 'none');
  assert.match(t.m.state().lastDecision.reason, /\(hold\)/, 'a trigger was present and held — not "no trigger"');
});

test('parseEnv survives CRLF and quotes', async () => {
  const t = fresh();
  const d = path.join(t.home, '.claude', 'channels', 'telegram');
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, '.env'), 'TELEGRAM_BOT_TOKEN="bot-x"\r\n');
  fs.writeFileSync(path.join(d, 'access.json'), JSON.stringify({ allowFrom: [42] }));
  const calls = []; t.m.net.http = async (method, url, { body }) => { calls.push({ method, url, body: JSON.parse(body) }); return { status: 200, data: {}, text: '' }; };
  await t.m.notify('hi');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://api.telegram.org/botbot-x/sendMessage', 'no trailing \\r baked into the token');
});

test('tick: a throw inside the tick is swallowed, notified once, and cleared on recovery', async () => {
  const t = fresh(); await machineOnPerso(t);
  // A stubbed net.http failure alone only fills row.error (collect() catches per-account); decide() then reads 'none' and
  // nothing throws. Force an actual throw out of tick() itself: a directory sitting where meter.json belongs makes
  // collect()'s own writeJsonAtomic(METER, …) throw EISDIR, uncaught inside collect() or tick() — exactly the escape
  // cmdTick's wrapper exists to catch.
  fs.mkdirSync(t.m.METER);
  await t.m.cmdTick();
  assert.equal(t.m.state().lastDecision.action, 'error'); assert.match(t.m.state().lastDecision.reason, /EISDIR/);
  assert.ok(t.m.state().tickFailedSince);
  assert.equal(fs.readFileSync(t.m.AUTO_LOG, 'utf8').split('\n').filter((l) => l.includes('tick FAILED')).length, 1);
  const since1 = t.m.state().tickFailedSince;
  await t.m.cmdTick();
  assert.equal(fs.readFileSync(t.m.AUTO_LOG, 'utf8').split('\n').filter((l) => l.includes('tick FAILED')).length, 1, 'notified once per episode');
  assert.equal(t.m.state().tickFailedSince, since1);
  fs.rmdirSync(t.m.METER);
  stubNet(t.m, { usageByToken: { 'at-perso': usage(10, 10), 'at-work': usage(10, 10) }, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick();
  assert.equal(t.m.state().tickFailedSince, null); assert.equal(t.m.state().lastDecision.action, 'none');
});

test('systemd units: a oneshot tick fired every 60 s with node on PATH', () => {
  const t = fresh();
  const u = t.m.systemdUnits(60);
  assert.match(u['claude-usage.service'], /^# ABOUTME:/m);
  assert.match(u['claude-usage.service'], /Type=oneshot/);
  assert.match(u['claude-usage.service'], /TimeoutStartSec=300/); // a oneshot with no timeout hangs forever on a wedged tick while the timer still reads active
  assert.match(u['claude-usage.service'], new RegExp(`ExecStart=${process.execPath} \\S+claude-usage\\.js auto tick`));
  assert.match(u['claude-usage.service'], new RegExp(`Environment=PATH=${path.dirname(process.execPath)}:`));
  assert.match(u['claude-usage.timer'], /^# ABOUTME:/m);
  assert.match(u['claude-usage.timer'], /OnBootSec=2min/); assert.match(u['claude-usage.timer'], /AccuracySec=10s/);
  assert.match(u['claude-usage.timer'], /OnUnitActiveSec=60s/); assert.match(u['claude-usage.timer'], /WantedBy=timers\.target/);
});

// doctor reads records, state and file modes only — no metering, so no network stub of its own is needed beyond the one
// machineOnPerso already installs for adoptLive's profile call.
test('doctor passes on a correctly checked-out machine', async () => {
  const t = fresh(); await machineOnPerso(t);
  const lines = []; console.log = (m = '') => lines.push(String(m)); // fresh() silenced stdout; collect the report instead of dropping it
  const prev = process.exitCode; await t.m.cmdDoctor(); const code = process.exitCode; process.exitCode = prev; // cmdDoctor reports through process.exitCode
  console.log = () => {};
  assert.ok(!code, `doctor failed on a healthy machine: ${lines.filter((l) => l.startsWith('FAIL')).join(' | ')}`);
  assert.ok(lines.some((l) => /^PASS {2}perso: checked out/.test(l)), 'the checked-out account is reported as checked out, never as a missing refresh token');
});

// ---------- status line: one account, the one this session spends ----------
const ANSI = { red: '\x1b[31m', orange: '\x1b[38;5;208m', yellow: '\x1b[33m', green: '\x1b[32m', reset: '\x1b[0m' };
const plain = (s) => s.replace(/\x1b\[[0-9;]*m/g, '');
function seedMeter(t, accounts, at = Date.now()) {
  fs.mkdirSync(path.dirname(t.m.METER), { recursive: true });
  fs.writeFileSync(t.m.METER, JSON.stringify({ at, accounts }));
}
const anthropicRows = (checkedOut = 'work') => ({
  work: { name: 'work', provider: 'anthropic', checkedOut: checkedOut === 'work', usage: { session: { percent: 12 }, weekly: { percent: 23 } } },
  perso: { name: 'perso', provider: 'anthropic', checkedOut: checkedOut === 'perso', usage: { session: { percent: 96, severity: 'critical' }, weekly: { percent: 57 } } },
  'gpt-work': { name: 'gpt-work', provider: 'openai', checkedOut: true, usage: { session: null, weekly: { percent: 98 } } },
});

test('statusline shows the checked-out Claude account first, then every account whose week is not red, most room first', () => {
  const t = fresh(); seedMeter(t, anthropicRows());
  assert.equal(plain(t.m.statuslineText()), 'work ⏳12% 📅23% | perso ⏳96% 📅57%', 'perso is red on its 5-hour window only: it is back in hours, so it is still room; gpt-work at 98 % of its week is not');
  const rows = anthropicRows();
  rows.perso.usage = { session: { percent: 0 }, weekly: { percent: 60 } };
  rows.spare = { name: 'spare', provider: 'anthropic', usage: { session: { percent: 3 }, weekly: { percent: 2 } } };
  rows.full = { name: 'full', provider: 'anthropic', usage: { session: { percent: 0 }, weekly: { percent: 96 } } };
  rows.dark = { name: 'dark', provider: 'anthropic', error: 'boom', usage: {} };
  rows['gpt-spare'] = { name: 'gpt-spare', provider: 'openai', usage: { session: null, weekly: { percent: 40 } } };
  seedMeter(t, rows);
  assert.equal(plain(t.m.statuslineText()), 'work ⏳12% 📅23% | spare ⏳3% 📅2% | perso ⏳0% 📅60% | dark ✗ | gpt-spare 📅40%',
    'the current account leads even with less room than spare; full is red on the week and left out; the unreadable one is last of its kind; the ChatGPT accounts follow');
});

test('statusline always shows the current account, red or not, and only it when nothing else has room', () => {
  const t = fresh();
  const rows = anthropicRows('perso'); rows.work.usage = { session: { percent: 0 }, weekly: { percent: 95 } };
  seedMeter(t, rows);
  assert.equal(plain(t.m.statuslineText()), 'perso ⏳96% 📅57%', '95 is the switch-at, so red, so not room');
});

test('statusline colours each number on its own level and the name on the weekly one', () => {
  const t = fresh();
  const rows = anthropicRows(); rows.work.usage = { session: { percent: 51 }, weekly: { percent: 71 } };
  rows.perso.usage.weekly.percent = 99; // off the line at every switch-at this test uses: the assertions below are about colours, on one account
  seedMeter(t, rows);
  assert.equal(t.m.statuslineText(), `${ANSI.orange}work${ANSI.reset} ${ANSI.yellow}⏳51%${ANSI.reset} ${ANSI.orange}📅71%${ANSI.reset}`);
  rows.work.usage = { session: { percent: 50 }, weekly: { percent: 94 } }; seedMeter(t, rows);
  assert.equal(t.m.statuslineText(), `${ANSI.orange}work${ANSI.reset} ${ANSI.green}⏳50%${ANSI.reset} ${ANSI.orange}📅94%${ANSI.reset}`, 'thresholds are strict: 50 is green, 94 is still orange');
  rows.work.usage = { session: { percent: 85 }, weekly: { percent: 95 } }; seedMeter(t, rows);
  assert.equal(t.m.statuslineText(), `${ANSI.red}work${ANSI.reset} ${ANSI.red}⏳85%${ANSI.reset} ${ANSI.red}📅95%${ANSI.reset}`, 'red starts where the watcher\'s hard rule does: 85% for the 5-hour wall, 95% for the week');
  // The two bounds are independent: a weekly value between switch-at's old and new setting (90-95) moves colour when
  // switch-at is overridden, while the 5-hour wall — already red at its own fixed sessionHot bound — does not move.
  rows.work.usage = { session: { percent: 85 }, weekly: { percent: 92 } }; seedMeter(t, rows);
  assert.equal(t.m.statuslineText(), `${ANSI.red}work${ANSI.reset} ${ANSI.red}⏳85%${ANSI.reset} ${ANSI.orange}📅92%${ANSI.reset}`, '92% is under switch-at (95) so the week is only orange; the 5-hour wall is already red at its own fixed bound');
  t.m.setState({ thresholds: { switchAt: 90 } }); seedMeter(t, rows);
  assert.equal(t.m.statuslineText(), `${ANSI.red}work${ANSI.reset} ${ANSI.red}⏳85%${ANSI.reset} ${ANSI.red}📅92%${ANSI.reset}`, '`auto on --switch-at 90` moves the week\'s red line onto 92% (92 ≥ 90); the 5-hour wall\'s red line is sessionHot, fixed, so it does not move with it');
});

test('statusline paints the name red when the 5-hour window is red, whatever the week says', () => {
  const t = fresh(); seedMeter(t, anthropicRows('perso'));
  const line = t.m.statuslineText();
  assert.equal(plain(line), 'perso ⏳96% 📅57% | work ⏳12% 📅23%', 'the current account leads whatever its level; work is room');
  assert.ok(line.startsWith(`${ANSI.red}perso${ANSI.reset}`), 'the week is yellow but the 5-hour wall is what stops the session');
});

test('statusline treats a locked window as red at any percentage', () => {
  const t = fresh();
  const rows = anthropicRows(); rows.work.usage = { session: { percent: 40, locked: true }, weekly: { percent: 10 } };
  seedMeter(t, rows);
  assert.ok(t.m.statuslineText().startsWith(`${ANSI.red}work${ANSI.reset} ${ANSI.red}⏳40%${ANSI.reset} ${ANSI.green}📅10%${ANSI.reset}`), t.m.statuslineText());
});

test('statusline follows the ChatGPT accounts when the session runs a gateway model, with no 5-hour cell on Pro Lite', () => {
  const t = fresh();
  const rows = anthropicRows(); rows['gpt-spare'] = { name: 'gpt-spare', provider: 'openai', usage: { session: null, weekly: { percent: 40 } } };
  seedMeter(t, rows);
  assert.equal(plain(t.m.statuslineText({ model: { id: 'gpt-6-astra' } })), 'gpt-work 📅98% | gpt-spare 📅40% | work ⏳12% 📅23% | perso ⏳96% 📅57%', 'the ChatGPT accounts lead: they are where an Astra session can go; the Claude ones follow');
  assert.equal(plain(t.m.statuslineText({ model: { id: 'claude-fable-5-1' } })), 'work ⏳12% 📅23% | perso ⏳96% 📅57% | gpt-spare 📅40%', 'a Claude model id is a Claude session');
  assert.equal(plain(t.m.statuslineText({})), 'work ⏳12% 📅23% | perso ⏳96% 📅57% | gpt-spare 📅40%', 'no model at all is a Claude session');
});

test('statusline marks a stale meter and an unreadable account', () => {
  const t = fresh(); seedMeter(t, anthropicRows(), Date.now() - 20 * 60e3);
  assert.equal(plain(t.m.statuslineText()), 'work ⏳12% 📅23% | perso ⏳96% 📅57% (stale)');
  const rows = anthropicRows(); rows.work.error = 'boom'; seedMeter(t, rows);
  assert.equal(plain(t.m.statuslineText()), 'work ✗ | perso ⏳96% 📅57%', 'the current account unreadable is still the current account, and first');
});

test('statusline install wraps the previous status line and re-running it refreshes the wrapper without touching settings', () => {
  const t = fresh();
  fs.writeFileSync(t.m.USER_SETTINGS, JSON.stringify({ statusLine: { type: 'command', command: 'python3 ~/.claude/statusline.py' }, other: 1 }));
  t.m.cmdStatusline('install');
  const sh = fs.readFileSync(t.m.STATUSLINE_SH, 'utf8');
  assert.match(sh, /^payload=\$\(cat\)$/m, 'the JSON is captured once');
  assert.match(sh, /printf '%s\\n' "\$\(printf '%s' "\$payload" \| python3 ~\/.claude\/statusline.py\)"/, 'the predecessor gets the same bytes and ends on exactly one newline');
  assert.match(sh, /printf '%s' "\$payload" \| ".*" ".*claude-usage.js" statusline\n$/, 'the meter gets the payload too and owns the last line');
  assert.ok(!sh.includes(' · '), 'no joiner: the meter has its own line');
  const settings = JSON.parse(fs.readFileSync(t.m.USER_SETTINGS, 'utf8'));
  assert.equal(settings.statusLine.command, t.m.STATUSLINE_SH); assert.equal(settings.other, 1);
  fs.writeFileSync(t.m.STATUSLINE_SH, '#!/bin/sh\n# an old wrapper\n');
  const before = fs.statSync(t.m.USER_SETTINGS).mtimeMs;
  t.m.cmdStatusline('install');
  assert.equal(fs.readFileSync(t.m.STATUSLINE_SH, 'utf8'), sh, 'the old wrapper is rewritten from the remembered predecessor');
  assert.equal(fs.statSync(t.m.USER_SETTINGS).mtimeMs, before, 'settings.json is not rewritten on a refresh');
});

// ---------- C1: a parked refresh racing a switch (the interleave from the review's race.js) ----------
test('a parked refresh that raced a switch never posts the rotated token nor resurrects the revoked pair', async () => {
  const t = fresh();
  seedLive(t, 'perso'); seedClaudeJson(t, 'uuid-p', 'p@x');
  t.m.saveAccount({ name: 'work', email: 'w@x', accountUuid: 'uuid-w', ...pair('work'), expiresAt: Date.now() + 60e3, addedAt: new Date().toISOString() });
  const rot = { 'rt-work': { access_token: 'at-work2', refresh_token: 'rt-work2', expires_in: 28800 } }; // the endpoint ROTATES: each refresh token works once
  const calls = [];
  t.m.net.http = async (method, url, { headers = {}, body } = {}) => {
    const tok = (headers.Authorization || '').replace('Bearer ', '');
    calls.push({ method, url, tok });
    if (url.endsWith('/api/oauth/profile')) return tok === 'at-perso' ? { status: 200, data: profile('uuid-p', 'p@x'), text: '' } : { status: 401, data: null, text: '' };
    if (url.endsWith('/oauth/token')) { const rt = JSON.parse(body).refresh_token; const r = rot[rt]; delete rot[rt]; return r ? { status: 200, data: r, text: '' } : { status: 400, data: { error: 'invalid_grant' }, text: '' }; }
    return { status: 401, data: null, text: '' };
  };
  await t.m.adoptLive('perso');
  const stale = t.m.loadAccount('work'); // P1 (a `stats` or the watcher's collect) holds the parked record in memory
  await t.m.switchTo('work');            // P2 switches to it: refreshes, installs, strips the record
  await t.m.refresh(stale);              // P1 refreshes its copy a moment later
  assert.equal(calls.filter((c) => c.url.endsWith('/oauth/token')).length, 1, 'P1 must not POST a refresh token the switch already rotated');
  const w = t.m.loadAccount('work');
  assert.equal(w.dead, undefined, 'the account every session is running on must never read dead');
  assert.ok(w.checkedOut, 'the checked-out flag survives the racing refresh');
  assert.equal(t.m.hasTokens(w), false, 'a revoked pair must never be written back into the record');
  assert.equal(t.m.isCheckedOut(stale), true, "P1's in-memory copy adopts the record it found on disk");
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-work2', 'the installed pair is untouched');
  assert.equal(t.m.state().live, 'work');
});

test('a parked refresh cannot run while a switch holds that account lock', async () => {
  const t = fresh(); seedParked(t, 'work', 'work', 'uuid-w', 'w@x');
  const a = t.m.loadAccount('work'); a.expiresAt = Date.now() + 60e3; t.m.saveAccount(a);
  stubNet(t.m, { refreshByToken: { 'rt-work': { access_token: 'at-work2', refresh_token: 'rt-work2', expires_in: 28800 } } });
  const held = t.m.takeLock(t.m.acctLock('work'), 600e3, 300); // stands in for a switch between park and install
  t.m.LOCK_WAIT.account = 300;
  await assert.rejects(t.m.refresh(t.m.loadAccount('work')), (e) => e.kind === 'lock', 'the parked refresh must be mutually exclusive with the switch');
  held();
  const w = await t.m.refresh(t.m.loadAccount('work'));
  assert.equal(w.refreshToken, 'rt-work2', 'once the switch lets go, the refresh runs normally');
});

test('switch: the lock order and the re-read of the target under them are the safety argument', async () => {
  const t = fresh(); await machineOnPerso(t);
  await t.m.switchTo('work');
  // Moving any of these (taking the client locks before the account lock, or installing the copy read before the waits)
  // leaves every other assertion in this file green while reopening the race — so it is pinned here explicitly.
  assert.deepEqual(t.m.SWITCH_TRACE, ['switch.lock', 'refresh-work.lock', '.oauth_refresh.lock', '.storage-write.lock', 'reload:work', 'install']);
});

// ---------- I1: the Keychain item is named per config dir ----------
test('the Keychain service name follows CLAUDE_CONFIG_DIR', () => {
  const a = fresh();
  assert.equal(a.m.keychainService(), 'Claude Code-credentials');
  const b = fresh({ cfgSub: path.join('.claude-profiles', 'work') });
  assert.equal(b.m.keychainService(), `Claude Code-credentials-${b.m.h8(b.m.CFG)}`);
  assert.notEqual(b.m.keychainService(), 'Claude Code-credentials', "installing into the default item under a non-default config dir would overwrite another profile's only grant");
});

// ---------- I2: the synthetic live row is not a failover target ----------
test('decide: the "live" row is never a failover target', () => {
  const t = fresh();
  const rows = [{ name: 'work', checkedOut: true, usage: { session: { percent: 40 }, weekly: { percent: 97 }, scoped: [] } },
    { name: 'live', live: true, usage: { session: { percent: 3 }, weekly: { percent: 2 }, scoped: [] } }];
  const d = t.m.decide({ rows, live: 'work', home: 'work', thresholds: T, exhaustedSince: null });
  assert.equal(d.action, 'exhausted', "switchTo('live') throws: an unstored login is not a parked grant");
  assert.notEqual(d.to, 'live');
});

// ---------- I3: a lock that cannot be cleared must time out, not spin ----------
test('takeLock: a stale lock it cannot clear hits the deadline instead of spinning at 100 % CPU', () => {
  const t = fresh();
  const dir = path.join(t.cfg, '.oauth_refresh.lock');
  fs.mkdirSync(dir); fs.writeFileSync(path.join(dir, 'junk'), ''); // rmdir could never remove this one
  const old = new Date(Date.now() - 120e3); fs.utimesSync(dir, old, old);
  const realRename = fs.renameSync; // and now the rename reclaim cannot either: the deadline is all that is left
  fs.renameSync = (from, to) => { if (String(from).includes('.oauth_refresh.lock')) { const e = new Error('EACCES: not ours to move'); e.code = 'EACCES'; throw e; } return realRename(from, to); };
  const t0 = Date.now();
  try { assert.throws(() => t.m.takeLock(dir, 60e3, 600), (e) => e.kind === 'lock'); } finally { fs.renameSync = realRename; }
  assert.ok(Date.now() - t0 < 5000, 'the deadline check must sit ABOVE the stale branch — below it, the retry never reaches it');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('takeLock: a stale lock with junk inside it is reclaimed by rename, and nothing is left behind', () => {
  const t = fresh();
  const dir = path.join(t.cfg, '.storage-write.lock');
  fs.mkdirSync(dir); fs.writeFileSync(path.join(dir, 'junk'), '');
  const old = new Date(Date.now() - 20000); fs.utimesSync(dir, old, old);
  const release = t.m.takeLock(dir, 15000, 600);
  release();
  assert.equal(fs.existsSync(dir), false);
  assert.deepEqual(fs.readdirSync(t.cfg).filter((f) => f.includes('.stale-')), [], 'the renamed carcass is removed, not left in the config dir');
});

// ---------- I4: the way back in after a /logout ----------
test('switch into an empty credential store installs the parked grant instead of refusing', async () => {
  const t = fresh();
  t.m.saveAccount({ name: 'work', email: 'w@x', accountUuid: 'uuid-w', checkedOut: new Date().toISOString() }); // its pair was in the store /logout emptied
  seedParked(t, 'perso', 'perso', 'uuid-p', 'p@x');
  t.m.setState({ live: 'work', home: 'work' });
  stubNet(t.m, {});
  await assert.rejects(t.m.adoptLive('perso'), /no live login/, 'adopt still refuses: there is nothing to adopt');
  const r = await t.m.switchTo('perso');
  assert.equal(r.parked, false);
  assert.equal(t.m.liveStoreRead().claudeAiOauth.accessToken, 'at-perso', 'the machine has a login again, no browser involved');
  assert.equal(t.m.loadAccount('work').checkedOut, undefined, 'the record claiming the empty store lets the flag go: its grant is revoked');
  const p = t.m.loadAccount('perso');
  assert.ok(p.checkedOut); assert.equal(t.m.hasTokens(p), false);
  assert.equal(t.m.state().live, 'perso');
});

// ---------- I5: adopt keeps the one-checked-out invariant ----------
test('adopt clears checkedOut everywhere else, so exactly one record is checked out', async () => {
  const t = fresh(); await machineOnPerso(t); // perso checked out, work parked
  seedLive(t, 'stranger'); seedClaudeJson(t, 'uuid-s', 's@x'); // a browser /login as a third account
  stubNet(t.m, { profileByToken: { 'at-stranger': profile('uuid-s', 's@x') } });
  await t.m.adoptLive('spare');
  assert.deepEqual(t.m.names().map(t.m.loadAccount).filter(t.m.isCheckedOut).map((a) => a.name), ['spare'], 'a stale checked-out record would meter the new account under the old name');
  assert.equal(t.m.loadAccount('work').refreshToken, 'rt-work', 'the parked grant is untouched');
});

// ---------- I6: a Telegram rejection is not silence ----------
test('notify logs a non-200 from Telegram instead of dropping the alert, and never the token', async () => {
  const t = fresh(); seedTelegram(t);
  t.m.net.http = async () => ({ status: 403, data: null, text: '{"ok":false,"description":"Forbidden: bot was blocked by the user"}' });
  await t.m.notify('failover perso → work');
  const logged = fs.readFileSync(t.m.AUTO_LOG, 'utf8');
  assert.match(logged, /telegram 403: .*Forbidden/, 'a revoked bot token or a wrong chat id must leave a trace');
  assert.ok(!logged.includes('bot-x'), 'the bot token never reaches the log');
});

// ---------- M1: `add` must not throw the browser login away ----------
test('add refuses on the checked-out account, and the dead remedy stops telling Louis to run it', async () => {
  const t = fresh(); await machineOnPerso(t);
  assert.match(t.m.addBlockedBy('perso'), /checked-out account/);
  assert.match(t.m.addBlockedBy('perso'), /switch <other>/);
  assert.equal(t.m.addBlockedBy('work'), null, 'a parked account is always safe to re-add');
  assert.match(t.m.deadRemedy('work'), /^re-add: claude-usage add work --force$/);
  assert.match(t.m.deadRemedy('perso'), /switch <other> first/);
  fs.rmSync(t.m.CREDS_FILE);
  assert.equal(t.m.addBlockedBy('perso'), null, 'with an empty store there is no login to lose: that is the /logout recovery');
});

// ---------- the cheap ones ----------
test('pairFromRecord installs the scopes a refresh wrote, not the snapshot taken at park time', () => {
  const t = fresh();
  const rec = { name: 'work', ...pair('work'), scopes: ['user:inference', 'user:sessions:claude_code'], oauthExtra: { scopes: ['user:inference'], subscriptionType: 'max' } };
  assert.deepEqual(t.m.pairFromRecord(rec).scopes, ['user:inference', 'user:sessions:claude_code'], 'a refresh updates a.scopes only');
});

test('renderRows pads the live row like the parked ones so the ● column lines up', () => {
  const t = fresh();
  const lines = []; console.log = (m = '') => lines.push(String(m));
  const u = (five, week) => ({ session: { percent: five }, weekly: { percent: week }, scoped: [], extra: null });
  t.m.renderRows([{ name: 'work', checkedOut: true, email: 'w@x', plan: 'Max 20x', usage: u(12, 23) },
    { name: 'perso', email: 'p@x', plan: 'Max 20x', usage: u(5, 9) },
    { name: 'live', live: true, email: 's@x', plan: 'Max 20x', usage: u(1, 2) }]);
  console.log = () => {};
  assert.ok(lines.some((l) => l.startsWith('● work')), lines.join(' | '));
  assert.ok(lines.some((l) => l.startsWith('  perso')), lines.join(' | '));
  assert.ok(lines.some((l) => l.startsWith('  live')), 'the live row starts in the same column as a parked row');
});

test('after a failover the status line names the new account at once, with no extra network call', async () => {
  const t = fresh(); await machineOnPerso(t);
  const calls = stubNet(t.m, { usageByToken: { 'at-perso': usage(0, 96), 'at-work': usage(20, 12) }, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick();
  const before = calls.length;
  const meter = JSON.parse(fs.readFileSync(t.m.METER, 'utf8'));
  assert.equal(meter.accounts.work.checkedOut, true); assert.equal(meter.accounts.perso.checkedOut, false);
  assert.equal(meter.accounts.perso.home, true, 'a --failover switch leaves home where it was');
  assert.match(t.m.statuslineText().replace(/\x1b\[[0-9;]*m/g, ''), /^work /, 'the status line follows the switch');
  assert.equal(calls.length, before, 'the flags are rewritten in place: no metering round trip');
});

// ---------- doctor sees the end state of a lost race ----------
test('doctor fails when state.live names a record that is not checked out', async () => {
  const t = fresh(); await machineOnPerso(t);
  const a = t.m.loadAccount('perso'); delete a.checkedOut; t.m.saveAccount(a); // ZERO checked-out records: "at most one" passes on it
  const lines = []; console.log = (m = '') => lines.push(String(m));
  const prev = process.exitCode; await t.m.cmdDoctor(); const code = process.exitCode; process.exitCode = prev;
  console.log = () => {};
  assert.ok(code, 'a machine with no checked-out record must not report all checks passed');
  assert.ok(lines.some((l) => /^FAIL {2}state\.live \(perso\) names a checked-out record/.test(l)), lines.join(' | '));
});

// ---------- the meter's request budget: api.anthropic.com/api/oauth/usage rate-limits a steady 3 calls/min ----------
// Ageing helper: rewrite meter.json's per-row reading timestamps, as the clock would.
function ageMeter(t, byName) {
  const m = JSON.parse(fs.readFileSync(t.m.METER, 'utf8'));
  for (const [n, ms] of Object.entries(byName)) m.accounts[n].usage.at = Date.now() - ms;
  fs.writeFileSync(t.m.METER, JSON.stringify(m));
}

test('collect: a second run inside the TTL is served from the meter, without a request', async () => {
  const t = fresh(); await machineOnPerso(t);
  const calls = stubNet(t.m, { usageByToken: { 'at-perso': usage(40, 10), 'at-work': usage(5, 5) } });
  const first = await t.m.collect();
  assert.equal(calls.length, 2, 'cold: one request per account');
  assert.equal(first.find((r) => r.name === 'perso').usage.session.percent, 40);
  const second = await t.m.collect();
  assert.equal(calls.length, 2, 'warm: no request at all');
  assert.equal(second.find((r) => r.name === 'work').usage.session.percent, 5, 'the cached reading is still rendered');
  assert.ok(second.find((r) => r.name === 'work').cached, 'a served-from-meter row says so');
});

test('collect: past the TTL the checked-out account refreshes every tick, a parked one every fifth', async () => {
  const t = fresh(); await machineOnPerso(t);
  const calls = stubNet(t.m, { usageByToken: { 'at-perso': usage(40, 10), 'at-work': usage(5, 5) } });
  await t.m.collect();
  ageMeter(t, { perso: 60e3, work: 60e3 }); // one tick later
  await t.m.collect();
  assert.deepEqual(calls.slice(2).map((c) => c.tok), ['at-perso'], 'only the account that moves is re-read');
  ageMeter(t, { perso: 60e3, work: 6 * 60e3 }); // five ticks later
  await t.m.collect();
  assert.deepEqual(calls.slice(3).map((c) => c.tok).sort(), ['at-perso', 'at-work']);
});

test('collect: forced, every account is re-read whatever the meter says', async () => {
  const t = fresh(); await machineOnPerso(t);
  const calls = stubNet(t.m, { usageByToken: { 'at-perso': usage(40, 10), 'at-work': usage(5, 5) } });
  await t.m.collect();
  await t.m.collect({ force: true });
  assert.equal(calls.length, 4);
});

test('collect: a 429 keeps the last good reading and stops asking for a while', async () => {
  const t = fresh(); await machineOnPerso(t);
  const usageByToken = { 'at-perso': usage(40, 10), 'at-work': usage(5, 5) };
  const calls = stubNet(t.m, { usageByToken });
  await t.m.collect();
  t.m.net.http = async (method, url, { headers = {} } = {}) => { // the endpoint's bucket is empty
    calls.push({ method, url, tok: (headers.Authorization || '').replace('Bearer ', '') });
    return { status: 429, data: { error: { message: 'Rate limited. Please try again later.' } }, text: '' };
  };
  ageMeter(t, { perso: 60e3, work: 6 * 60e3 });
  const rows = await t.m.collect();
  const perso = rows.find((r) => r.name === 'perso');
  assert.equal(perso.error, undefined, 'a rate-limited read must not blank the row');
  assert.equal(perso.usage.session.percent, 40, 'the last good reading stands in');
  assert.ok(perso.cached && perso.rateLimited);
  const after = calls.length;
  ageMeter(t, { perso: 60e3, work: 6 * 60e3 });
  await t.m.collect();
  assert.equal(calls.length, after, 'inside the backoff the tool does not ask again');
});

test('collect: a row that fails keeps its last reading in the meter, so a later 429 has something to stand on', async () => {
  const t = fresh(); await machineOnPerso(t);
  const calls = stubNet(t.m, { usageByToken: { 'at-perso': usage(40, 10), 'at-work': usage(5, 5) } });
  await t.m.collect();
  t.m.net.http = async (method, url, { headers = {} } = {}) => {
    calls.push({ method, url, tok: (headers.Authorization || '').replace('Bearer ', '') });
    return { status: 500, data: null, text: 'boom' };
  };
  ageMeter(t, { perso: 60e3, work: 6 * 60e3 });
  const broken = await t.m.collect();
  assert.ok(broken.find((r) => r.name === 'perso').error, 'a 500 is still an error on the row');
  assert.equal(JSON.parse(fs.readFileSync(t.m.METER, 'utf8')).accounts.perso.usage.session.percent, 40,
    'the meter keeps the last reading behind the error');
  t.m.net.http = async () => ({ status: 429, data: { error: { message: 'Rate limited.' } }, text: '' });
  const limited = await t.m.collect();
  assert.equal(limited.find((r) => r.name === 'perso').usage.session.percent, 40, 'which is what the 429 falls back on');
});

test('collect: an account with no reading at all still respects its backoff', async () => {
  const t = fresh(); await machineOnPerso(t);
  const calls = [];
  t.m.net.http = async (method, url, { headers = {} } = {}) => { // never read successfully: nothing to fall back on
    calls.push({ method, url, tok: (headers.Authorization || '').replace('Bearer ', '') });
    return { status: 429, data: { error: { message: 'Rate limited.' } }, text: '' };
  };
  const first = await t.m.collect();
  assert.equal(calls.length, 2);
  assert.match(first.find((r) => r.name === 'perso').error, /asking again at|rate-limited/);
  const held = await t.m.collect();
  assert.equal(calls.length, 2, 'the account it has never read is the one it must stop asking');
  assert.match(held.find((r) => r.name === 'perso').error, /asking again at/);
  assert.equal(t.m.state().rateLimited.perso.tries, 1, 'a held tick does not count as another failure');
  await t.m.collect({ force: true });
  assert.equal(calls.length, 4, 'force still asks');
});

test('collect: the backoff lifts and a success clears it', async () => {
  const t = fresh(); await machineOnPerso(t);
  const usageByToken = { 'at-perso': usage(40, 10), 'at-work': usage(5, 5) };
  const calls = stubNet(t.m, { usageByToken });
  await t.m.collect();
  t.m.setState({ rateLimited: { perso: { until: Date.now() - 1, tries: 1 } } }); // the cooldown just expired
  ageMeter(t, { perso: 60e3, work: 60e3 });
  await t.m.collect();
  assert.deepEqual(calls.slice(2).map((c) => c.tok), ['at-perso'], 'once the cooldown lapses the account is asked again');
  assert.equal(t.m.state().rateLimited?.perso, undefined, 'a 200 clears the cooldown');
});

test('decide: a cached reading decides, one older than the cap does not', async () => {
  const t = fresh();
  const fresher = (r, ageMs) => ({ ...r, usage: { ...r.usage, at: Date.now() - ageMs } });
  const rows = [fresher(row('perso', 40, 96, { checkedOut: true }), 60e3), fresher(row('work', 30, 20), 4 * 60e3)];
  assert.equal(t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null }).action, 'failover');
  const stale = [fresher(row('perso', 40, 96, { checkedOut: true }), 30 * 60e3), rows[1]];
  assert.equal(t.m.decide({ rows: stale, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null }).action, 'none',
    'a half-hour-old live reading is not something to move the machine on');
  const staleTarget = [rows[0], fresher(row('work', 30, 20), 30 * 60e3)];
  assert.equal(t.m.decide({ rows: staleTarget, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null }).action, 'exhausted',
    'nor is a half-hour-old target');
});

test('tick: a failover re-reads every account first, so the machine moves on fresh numbers', async () => {
  const t = fresh(); await machineOnPerso(t);
  const usageByToken = { 'at-perso': usage(0, 96), 'at-work': usage(20, 12) };
  const calls = stubNet(t.m, { usageByToken, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'work');
  const usageCalls = calls.filter((c) => c.url.includes('/api/oauth/usage'));
  assert.ok(usageCalls.filter((c) => c.tok === 'at-work').length >= 2, 'the target is re-read before the switch');
});

test('renderRows: a stale row shows its age instead of pretending to be current', () => {
  const t = fresh();
  const lines = []; console.log = (m = '') => lines.push(String(m));
  const u = (five, week, ageMs) => ({ session: { percent: five }, weekly: { percent: week }, scoped: [], extra: null, at: Date.now() - ageMs });
  t.m.renderRows([{ name: 'work', checkedOut: true, email: 'w@x', plan: 'Max 20x', usage: u(12, 23, 1e3) },
    { name: 'perso', email: 'p@x', plan: 'Max 20x', usage: u(5, 9, 7 * 60e3), cached: true }]);
  console.log = () => {};
  assert.ok(lines.some((l) => l.startsWith('  perso') && /7m old/.test(l)), lines.join(' | '));
  assert.ok(lines.some((l) => l.startsWith('● work') && !/old/.test(l)), 'a fresh row carries no age marker');
});

test('renderRows: the edf column marks the next pick, the usable rows, and why the rest cannot take the machine', () => {
  const t = fresh();
  const lines = []; console.log = (m = '') => lines.push(String(m));
  const u = (five, week, resetMs) => ({ session: { percent: five }, weekly: { percent: week, resetsAt: iso(resetMs) }, scoped: [], extra: null, at: Date.now() });
  t.m.renderRows([
    { name: 'live', checkedOut: true, email: 'l@x', plan: 'Max 20x', usage: u(10, 30, 5 * 864e5) },
    { name: 'next', email: 'n@x', plan: 'Max 20x', usage: u(10, 40, 3600e3) },
    { name: 'ok', email: 'o@x', plan: 'Max 20x', usage: u(10, 40, 864e5) },
    { name: 'hot', email: 'h@x', plan: 'Max 20x', usage: u(98, 40, 60e3) },
    { name: 'gpt-a', provider: 'openai', email: 'a@x', plan: 'Pro Lite', usage: { session: null, weekly: { percent: 50 }, scoped: [], extra: null, at: Date.now() } },
  ]);
  console.log = () => {};
  const rowOf = (n) => lines.find((l) => l.startsWith(`  ${n}`) || l.startsWith(`● ${n}`));
  assert.ok(!lines.some((l) => /pressure/.test(l)), 'the pressure column is gone');
  assert.ok(lines.some((l) => /\bedf\b/.test(l)), 'the edf column has a header');
  assert.match(rowOf('next'), /▶/, 'the soonest usable reset a rotation may enter');
  assert.match(rowOf('ok'), /✓/);
  assert.match(rowOf('hot'), /5h 98%/, 'sooner still, but not usable — and it says why');
  assert.ok(!/[▶✓]/.test(rowOf('gpt-a')), 'a ChatGPT row is never a candidate');
  assert.ok(!/[▶✓]/.test(rowOf('live')), 'the live row has no edf verdict');
});

// ---------- when the meter goes blind: a refusal from any run, or N unreadable ticks in a row ----------
// On 2026-09-15 perso's usage endpoint answered 429 for nine ticks exactly while perso refused jobs, and work2's for
// eleven while it climbed from 65% to 100%. The meter is blind when an account is busiest.

test('decide: a live row that stays unreadable moves the machine on the third tick', () => {
  const t = fresh();
  const rows = [{ name: 'perso', error: 'usage endpoint rate-limited', checkedOut: true }, row('work', 10, 5)];
  const at = (blind) => t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null, blind });
  assert.equal(at(0).action, 'none', 'one unreadable tick is a hiccup');
  assert.equal(at(1).action, 'none');
  const d = at(2);
  assert.equal(d.action, 'failover'); assert.equal(d.to, 'work');
  assert.match(d.reason, /unreadable 3/);
});

test('decide: a blind live row with nowhere to go stays put', () => {
  const t = fresh();
  const rows = [{ name: 'perso', error: 'usage endpoint rate-limited', checkedOut: true }, row('work', 90, 88)];
  const d = t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null, blind: 2 });
  assert.equal(d.action, 'none', 'switching into an account over sessionHot buys nothing');
});

test('decide: a half-readable live row is a degraded reading — it joins the blind streak, never the hard rule', () => {
  const t = fresh();
  const half = { name: 'work7', checkedOut: true, usage: { session: null, weekly: { percent: 20 }, scoped: [] } };
  const spare = row('work6', 0, 10);
  const d0 = t.m.decide({ rows: [half, spare], live: 'work7', thresholds: T, exhaustedSince: null, blind: 0 });
  assert.equal(d0.action, 'none'); assert.equal(d0.liveUnreadable, true); assert.match(d0.reason, /5-hour window unreadable/);
  const d2 = t.m.decide({ rows: [half, spare], live: 'work7', thresholds: T, exhaustedSince: null, blind: 2 });
  assert.equal(d2.action, 'failover'); assert.equal(d2.to, 'work6', 'the third tick moves it, as for a fully blind row');
  const halfSpare = { name: 'work6', usage: { session: null, weekly: { percent: 10 }, scoped: [] } };
  const d = t.m.decide({ rows: [half, halfSpare], live: 'work7', thresholds: T, exhaustedSince: null, blind: 0 });
  assert.equal(d.action, 'none'); assert.notEqual(d.notify, true, 'pool-wide degradation on the first tick is not "no usable account"');
});

test('decide: a usage-limit refusal fails the machine over whatever the meter says', () => {
  const t = fresh();
  const rows = [row('perso', 10, 20, { checkedOut: true }), row('work', 30, 20)];
  const d = t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null, limited: { perso: new Date().toISOString() } });
  assert.equal(d.action, 'failover'); assert.equal(d.to, 'work');
  assert.match(d.reason, /refused/);
});

test('decide: an account that just refused on its limit is not a failover target', () => {
  const t = fresh();
  const rows = [row('perso', 40, 96, { checkedOut: true }), row('work', 5, 5)];
  const d = t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null, limited: { work: new Date().toISOString() } });
  assert.equal(d.action, 'exhausted', 'its meter lies until the reading catches up with the refusal');
});

test('decide: limit evidence older than the cooldown is history', () => {
  const t = fresh();
  const rows = [row('perso', 10, 20, { checkedOut: true }), row('work', 30, 20)];
  const old = new Date(Date.now() - 3 * 36e5).toISOString();
  assert.equal(t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T, exhaustedSince: null, limited: { perso: old } }).action, 'none');
});

// A transcript turn the client wrote itself: model "<synthetic>" is how a refusal looks from outside the session
// (memory daemon-model-pinned). Scanning them is what makes "any run" report a limit without touching every unit.
const limitTurn = (ts = new Date().toISOString()) => ({ type: 'assistant', timestamp: ts, message: { model: '<synthetic>', content: [{ type: 'text', text: "You've hit your weekly limit · resets Sep 21, 10am (America/Los_Angeles)" }] } });
function seedTranscript(t, name, rows, ageMs = 0) {
  const d = path.join(t.home, '.claude', 'projects', 'proj');
  fs.mkdirSync(d, { recursive: true });
  const f = path.join(d, name);
  fs.writeFileSync(f, rows.map((r) => JSON.stringify(r)).join('\n') + '\n');
  if (ageMs) { const at = (Date.now() - ageMs) / 1000; fs.utimesSync(f, at, at); }
  return f;
}

test('limitRefusals: a synthetic limit turn written since the last look is evidence', () => {
  const t = fresh();
  seedTranscript(t, 'a.jsonl', [limitTurn()]);
  const ev = t.m.limitRefusals({ since: Date.now() - 5 * 60e3 });
  assert.ok(ev, 'the refusal is found');
  assert.match(ev.text, /weekly limit/);
});

test('limitRefusals: a refusal whose file mtime lags the scan clock is still evidence', () => {
  const t = fresh();
  // The kernel stamps mtime from a COARSE clock that runs up to a millisecond behind the fine-grained one
  // Date.toISOString() records (measured on the main workstation: 85% of writes land behind the Date.now() taken just before
  // them). So a transcript written a hair AFTER a scan can carry an mtime a hair BEFORE it. Filtering files on
  // `mtimeMs < since` then drops the very refusal the scan exists to find — and because the next scan starts at
  // this one's timestamp, it is lost for good rather than deferred.
  const since = Date.now();
  const f = seedTranscript(t, 'run.jsonl', [limitTurn(new Date(since + 1).toISOString())]);
  const lagged = (since - 1) / 1000; // one millisecond of coarse-clock lag
  fs.utimesSync(f, lagged, lagged);

  const ev = t.m.limitRefusals({ since });
  assert.ok(ev, 'the turn timestamp decides what counts; the file mtime only decides what is worth opening');
  assert.equal(ev.file, f);
});

test('limitRefusals: the mtime window is a filter, not a second opinion on the turn', () => {
  const t = fresh();
  // Widening the file filter must not resurrect turns that are genuinely older than the scan point.
  const since = Date.now();
  const f = seedTranscript(t, 'run.jsonl', [limitTurn(new Date(since - 5 * 60e3).toISOString())]);
  const lagged = (since - 1) / 1000;
  fs.utimesSync(f, lagged, lagged);
  assert.equal(t.m.limitRefusals({ since }), null, 'an old refusal in a just-touched file is still old');
});

test('limitRefusals: a transcript untouched since the last look is not evidence', () => {
  const t = fresh();
  seedTranscript(t, 'old.jsonl', [limitTurn(new Date(Date.now() - 30 * 60e3).toISOString())], 30 * 60e3);
  assert.equal(t.m.limitRefusals({ since: Date.now() - 5 * 60e3 }), null);
});

test('limitRefusals: a fresh file whose limit turn is old is not evidence', () => {
  const t = fresh();
  seedTranscript(t, 'appended.jsonl', [limitTurn(new Date(Date.now() - 6 * 36e5).toISOString()), { type: 'user', message: { role: 'user', content: 'hi' } }]);
  assert.equal(t.m.limitRefusals({ since: Date.now() - 5 * 60e3 }), null, 'an old turn in a file touched for another reason is not a new refusal');
});

test('limitRefusals: the same words from a user turn are not evidence', () => {
  const t = fresh();
  seedTranscript(t, 'user.jsonl', [{ type: 'user', timestamp: new Date().toISOString(), message: { role: 'user', content: "You've hit your weekly limit, what do I do?" } }]);
  assert.equal(t.m.limitRefusals({ since: Date.now() - 5 * 60e3 }), null);
});

test('limitRefusals: a junk line never throws out of the scan', () => {
  const t = fresh();
  const f = seedTranscript(t, 'junk.jsonl', [limitTurn()]);
  fs.appendFileSync(f, '{not json at all\n');
  assert.ok(t.m.limitRefusals({ since: Date.now() - 5 * 60e3 }), 'the good line still counts');
});

test('tick: three blind ticks in a row fail the machine over', async () => {
  const t = fresh(); await machineOnPerso(t);
  stubNet(t.m, { usageByToken: { 'at-work': usage(12, 20) }, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick(); assert.equal(t.m.state().live, 'perso');
  await t.m.cmdTick(); assert.equal(t.m.state().live, 'perso');
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'work', 'the third blind tick moves the machine');
  assert.match(fs.readFileSync(t.m.AUTO_LOG, 'utf8'), /failover perso → work.*unreadable 3 ticks/, 'the alert says why it moved without numbers');
  assert.equal(t.m.state().blind, 0, 'the counter resets behind the switch');
});

test('tick: one readable tick clears the blind counter', async () => {
  const t = fresh(); await machineOnPerso(t);
  const usageByToken = { 'at-work': usage(12, 20) };
  stubNet(t.m, { usageByToken, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick(); await t.m.cmdTick();
  assert.equal(t.m.state().blind, 2);
  usageByToken['at-perso'] = usage(30, 20); // the endpoint answers again
  await t.m.cmdTick();
  assert.equal(t.m.state().blind, 0);
  assert.equal(t.m.state().live, 'perso', 'a readable live row decides on its own numbers');
});

test('tick: a limit refusal in a transcript fails the machine over on the next tick', async () => {
  const t = fresh(); await machineOnPerso(t);
  stubNet(t.m, { usageByToken: { 'at-perso': usage(10, 20), 'at-work': usage(12, 20) }, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'perso', 'nothing is wrong yet');
  seedTranscript(t, 'run.jsonl', [limitTurn()]);
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'work', 'a refusal outranks a meter that still reads 10%');
  assert.match(fs.readFileSync(t.m.AUTO_LOG, 'utf8'), /failover perso → work.*refused on its limit/, 'the alert says why it moved against the meter');
});

test('tick: a refusal written before the live account was checked out is not blamed on it', async () => {
  const t = fresh(); await machineOnPerso(t);
  stubNet(t.m, { usageByToken: { 'at-perso': usage(10, 20), 'at-work': usage(12, 20) }, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  await t.m.cmdTick();
  seedTranscript(t, 'run.jsonl', [limitTurn(new Date(Date.now() - 30e3).toISOString())]); // refused 30 s ago, on the account live then
  t.m.setState({ refusalScanAt: new Date(Date.now() - 60e3).toISOString() });
  const rec = t.m.loadAccount('perso'); rec.checkedOut = new Date(Date.now() - 10e3).toISOString(); t.m.saveAccount(rec); // perso came in by hand 10 s ago
  await t.m.cmdTick();
  assert.equal(t.m.state().live, 'perso', 'the refusal belongs to the account that was live before the checkout');
  assert.equal(t.m.state().limited?.perso, undefined);
});

// ---------- the ChatGPT (Codex) subscription row ----------
// Shapes taken from a live GET of chatgpt.com/backend-api/wham/usage on 2026-09-15.
const codexDoc = (over = {}) => ({
  user_id: 'user-x', account_id: 'acct-x', email: 'l@x', plan_type: 'prolite',
  rate_limit: { allowed: true, limit_reached: false, primary_window: { used_percent: 12, limit_window_seconds: 604800, reset_after_seconds: 600000, reset_at: Math.floor(Date.now() / 1000) + 600000 }, secondary_window: null },
  additional_rate_limits: [], credits: { has_credits: false, unlimited: false, balance: '0' },
  spend_control: { reached: false, individual_limit: null }, rate_limit_reached_type: null, ...over,
});

test('parseCodexUsage: windows are classified by their length, never by primary/secondary position', () => {
  const t = fresh();
  // The real prolite document: the ONLY top-level window is the weekly one. Reading it positionally would file a
  // 7-day number under "5-hour" and make the row lie.
  const a = t.m.parseCodexUsage(codexDoc());
  assert.equal(a.session, null, 'no 5-hour window on this plan');
  assert.equal(a.weekly.percent, 12);
  assert.ok(Date.parse(a.weekly.resetsAt) > Date.now(), 'reset_at (epoch seconds) became an ISO string until() can read');

  // Same two windows, swapped: the 5-hour one arrives as secondary.
  const b = t.m.parseCodexUsage(codexDoc({ rate_limit: { primary_window: { used_percent: 30, limit_window_seconds: 604800, reset_at: Math.floor(Date.now() / 1000) + 1000 }, secondary_window: { used_percent: 80, limit_window_seconds: 18000, reset_at: Math.floor(Date.now() / 1000) + 100 } } }));
  assert.equal(b.session.percent, 80, '18000s is the 5-hour window wherever it sits');
  assert.equal(b.weekly.percent, 30);
  assert.equal(b.session.severity, 'warning');
});

test('parseCodexUsage: per-model limits become scoped rows, credits become the extra cell', () => {
  const t = fresh();
  const u = t.m.parseCodexUsage(codexDoc({
    additional_rate_limits: [{ limit_name: 'GPT-5.3-Codex-Spark', metered_feature: 'codex_bengalfox', rate_limit: { primary_window: { used_percent: 4, limit_window_seconds: 18000, reset_at: Math.floor(Date.now() / 1000) + 60 }, secondary_window: { used_percent: 41, limit_window_seconds: 604800, reset_at: Math.floor(Date.now() / 1000) + 600 } } }],
    credits: { has_credits: true, unlimited: false, balance: '12' },
  }));
  assert.equal(u.scoped.length, 1, 'one entry per named limit, at its worst window');
  assert.equal(u.scoped[0].label, 'GPT-5.3-Codex-Spark wk');
  assert.equal(u.scoped[0].percent, 41);
  assert.equal(u.extra.enabled, true);
  assert.equal(u.extra.balance, '12');
});

test('collect: a ChatGPT row appears only for a stored account, and is tagged openai', async () => {
  const t = fresh();
  stubCodexNet(t.m, {});
  assert.equal((await t.m.collect({ includeLive: false })).length, 0, 'no stored account, no row');

  seedCodexAccount(t, 'gpt-a', 'a'); linkCodex(t, 'gpt-a');
  stubCodexNet(t.m, { whamByToken: { 'gat-a': whamDoc(12, { email: 'a@x' }) } });
  const rows = await t.m.collect({ includeLive: false });
  assert.equal(rows.length, 1);
  const r = rows[0];
  assert.equal(r.name, 'gpt-a');
  assert.equal(r.provider, 'openai');
  assert.equal(r.plan, 'Pro Lite');
  assert.equal(r.email, 'a@x');
  assert.equal(r.usage.weekly.percent, 12);
  assert.equal(r.checkedOut, true, 'the linked account is the active one');
});

test('collect: every Claude row still says anthropic, and a Codex failure never blanks them', async () => {
  const t = fresh();
  await machineOnPerso(t);
  seedCodexAccount(t, 'gpt-a', 'a'); linkCodex(t, 'gpt-a');
  stubCodexNet(t.m, { usageByToken: { 'at-perso': usage(10, 20), 'at-work': usage(30, 40) }, profileByToken: { 'at-perso': profile('uuid-p', 'p@x'), 'at-work': profile('uuid-w', 'w@x') } });
  const rows = await t.m.collect({ includeLive: false });
  // gpt-a is the ACTIVE account and its meter read fails: we may not refresh it, so the row says who will.
  assert.match(rows.find((r) => r.name === 'gpt-a').error, /the proxy refreshes it/, 'an unreadable active account names its owner, it does not offer a re-login');
  assert.equal(rows.find((r) => r.name === 'perso').usage.session.percent, 10, 'the Claude rows are untouched by the Codex failure');
  assert.ok(rows.filter((r) => r.provider === 'anthropic').length >= 2);
});

test('decide: a ChatGPT row is never a failover target, however empty it is', () => {
  const t = fresh();
  // perso is over the line, work is refused, and the ChatGPT row is at 0%: the tempting wrong answer.
  const rows = [
    row('perso', 99, 99, { checkedOut: true }),
    { name: 'chatgpt', provider: 'openai', usage: { session: null, weekly: { percent: 0 }, scoped: [] } },
  ];
  const d = t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T });
  assert.equal(d.action, 'exhausted', 'switching the machine to a ChatGPT subscription is not a thing that exists');
  assert.notEqual(d.to, 'chatgpt');

  // And it must not be counted as the emptiest account for a failback either.
  const d2 = t.m.decide({ rows: [row('work', 10, 10, { checkedOut: true }), { name: 'chatgpt', provider: 'openai', usage: { session: null, weekly: { percent: 0 }, scoped: [] } }], live: 'work', home: 'chatgpt', thresholds: T });
  assert.equal(d2.action, 'none');
});

// ---------- more than one ChatGPT account ----------
// The proxy re-reads its credential on every upstream call and writes a rotated one back as <path>.tmp-<uuid> renamed
// over auth.json, so the switch is a DIRECTORY symlink: a file-level link would be replaced by the first refresh.
const codexPair = (tag, ms = 8 * 864e5) => ({ access: `gat-${tag}`, refresh: `grt-${tag}`, expires: Date.now() + ms, accountId: `acct-${tag}` });
function seedCodexAccount(t, name, tag, { ms, meta } = {}) {
  const dir = path.join(t.m.CODEX_STORE, name);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(dir, 'auth.json'), JSON.stringify(codexPair(tag, ms)), { mode: 0o600 });
  if (meta !== null) fs.writeFileSync(path.join(dir, 'meta.json'), JSON.stringify(meta || { email: `${name}@x`, plan: 'Pro Lite' }), { mode: 0o600 });
  return dir;
}
const linkCodex = (t, name) => { fs.mkdirSync(path.dirname(t.m.CODEX_LIVE), { recursive: true }); try { fs.unlinkSync(t.m.CODEX_LIVE); } catch { /* none */ } fs.symlinkSync(path.join(t.m.CODEX_STORE, name), t.m.CODEX_LIVE); };
// The real document, trimmed to what the parser reads.
const whamDoc = (pct, { email = 'l@x', plan = 'prolite', reached = false } = {}) => ({
  email, plan_type: plan,
  rate_limit: { allowed: !reached, limit_reached: reached, primary_window: { used_percent: pct, limit_window_seconds: 604800, reset_at: Math.floor(Date.now() / 1000) + 600000 }, secondary_window: null },
  additional_rate_limits: [], credits: { has_credits: false, unlimited: false, balance: '0' }, spend_control: { reached: false },
});
// Route by bearer so each account answers with its own numbers, and count refresh POSTs per refresh token.
function stubCodexNet(m, { whamByToken = {}, refreshByToken = {}, usageByToken = {}, profileByToken = {} } = {}) {
  const calls = [];
  m.net.http = async (method, url, { headers = {}, body } = {}) => {
    const tok = (headers.Authorization || '').replace('Bearer ', '');
    calls.push({ method, url, tok });
    if (url.includes('/wham/usage')) { const d = whamByToken[tok]; return d ? { status: 200, data: d, text: '' } : { status: 401, data: null, text: '' }; }
    if (url.includes('auth.openai.com/oauth/token')) { const rt = new URLSearchParams(body).get('refresh_token') || (() => { try { return JSON.parse(body).refresh_token; } catch { return null; } })(); const r = refreshByToken[rt]; return r ? { status: 200, data: r, text: '' } : { status: 400, data: { error: 'invalid_grant' }, text: '' }; }
    if (url.endsWith('/api/oauth/profile')) { const p = profileByToken[tok]; return p ? { status: 200, data: p, text: '' } : { status: 401, data: null, text: '' }; }
    if (url.includes('/api/oauth/usage')) { const u = usageByToken[tok]; return u ? { status: 200, data: u, text: '' } : { status: 401, data: null, text: '' }; }
    throw new Error(`unexpected ${method} ${url}`);
  };
  return calls;
}

test('codex store: accounts are enumerated from their own directories, the symlink says which is active', () => {
  const t = fresh();
  assert.deepEqual(t.m.codexNames(), [], 'no store, no accounts');
  seedCodexAccount(t, 'gpt-a', 'a'); seedCodexAccount(t, 'gpt-b', 'b');
  assert.deepEqual(t.m.codexNames(), ['gpt-a', 'gpt-b']);
  assert.equal(t.m.codexActive(), null, 'nothing linked yet');
  linkCodex(t, 'gpt-b');
  assert.equal(t.m.codexActive(), 'gpt-b');
});

test('codex switch: retargets the link and touches no Claude credential', async () => {
  const t = fresh();
  await machineOnPerso(t);
  seedCodexAccount(t, 'gpt-a', 'a'); seedCodexAccount(t, 'gpt-b', 'b'); linkCodex(t, 'gpt-a');
  const credsBefore = fs.readFileSync(t.m.CREDS_FILE, 'utf8');
  const liveBefore = t.m.state().live;

  await t.m.switchCodex('gpt-b');
  assert.equal(t.m.codexActive(), 'gpt-b');
  assert.equal(fs.lstatSync(t.m.CODEX_LIVE).isSymbolicLink(), true, 'still a symlink, not a copied directory');
  assert.equal(fs.readFileSync(t.m.CREDS_FILE, 'utf8'), credsBefore, 'the Claude live store is untouched');
  assert.equal(t.m.state().live, liveBefore, 'and so is the live Claude account');
  // The credential must still be reachable THROUGH the link, which is what the proxy opens every request.
  assert.equal(JSON.parse(fs.readFileSync(path.join(t.m.CODEX_LIVE, 'auth.json'), 'utf8')).accountId, 'acct-b');
});

test('codex switch: refuses a name it does not hold, and one with no credential', async () => {
  const t = fresh();
  seedCodexAccount(t, 'gpt-a', 'a'); linkCodex(t, 'gpt-a');
  await assert.rejects(() => t.m.switchCodex('nope'), /nope/);
  fs.mkdirSync(path.join(t.m.CODEX_STORE, 'gpt-empty'), { recursive: true });
  await assert.rejects(() => t.m.switchCodex('gpt-empty'), /no credential|add-gpt/);
  assert.equal(t.m.codexActive(), 'gpt-a', 'a refused switch leaves the link where it was');
});

test('codex refresh: only a parked account, rotated pair written back to its own directory', async () => {
  const t = fresh();
  seedCodexAccount(t, 'gpt-a', 'a', { ms: 60e3 }); // expiring inside the refresh-ahead window
  seedCodexAccount(t, 'gpt-b', 'b', { ms: 60e3 });
  linkCodex(t, 'gpt-a');
  const calls = stubCodexNet(t.m, { refreshByToken: { 'grt-b': { access_token: 'gat-b2', refresh_token: 'grt-b2', expires_in: 8 * 86400 } } });

  // The active one belongs to the proxy: we must not POST its refresh token at all.
  await assert.rejects(() => t.m.refreshCodex(t.m.loadCodexAccount('gpt-a')), /active|proxy/);
  assert.equal(calls.filter((c) => c.url.includes('oauth/token')).length, 0, 'not one refresh call for the active account');

  const b = await t.m.refreshCodex(t.m.loadCodexAccount('gpt-b'));
  assert.equal(b.access, 'gat-b2');
  const onDisk = JSON.parse(fs.readFileSync(path.join(t.m.CODEX_STORE, 'gpt-b', 'auth.json'), 'utf8'));
  assert.equal(onDisk.refresh, 'grt-b2', 'the rotated refresh token is persisted, or the next refresh spends a dead one');
  assert.equal(fs.statSync(path.join(t.m.CODEX_STORE, 'gpt-b', 'auth.json')).mode & 0o777, 0o600);
  assert.equal(JSON.parse(fs.readFileSync(path.join(t.m.CODEX_STORE, 'gpt-a', 'auth.json'), 'utf8')).access, 'gat-a', 'the active account is not rewritten');
});

test('codex refresh: a rejected refresh token marks the record dead and deletes NOTHING', async () => {
  const t = fresh();
  seedCodexAccount(t, 'gpt-b', 'b', { ms: 60e3 });
  stubCodexNet(t.m, {}); // no refresh answer → 400 invalid_grant
  await assert.rejects(() => t.m.refreshCodex(t.m.loadCodexAccount('gpt-b')), /add-gpt/);
  assert.ok(fs.existsSync(path.join(t.m.CODEX_STORE, 'gpt-b', 'auth.json')), 'deleting the credential is the proxy\'s destructive path, never ours');
});

test('collect: every stored ChatGPT account gets a row, the active one marked', async () => {
  const t = fresh();
  seedCodexAccount(t, 'gpt-a', 'a'); seedCodexAccount(t, 'gpt-b', 'b'); linkCodex(t, 'gpt-a');
  stubCodexNet(t.m, { whamByToken: { 'gat-a': whamDoc(12, { email: 'a@x' }), 'gat-b': whamDoc(3, { email: 'b@x', plan: 'plus' }) } });
  const rows = await t.m.collect({ includeLive: false });
  const a = rows.find((r) => r.name === 'gpt-a'), b = rows.find((r) => r.name === 'gpt-b');
  assert.equal(a.provider, 'openai'); assert.equal(a.checkedOut, true, 'the linked account is the active one');
  assert.equal(a.usage.weekly.percent, 12); assert.equal(a.email, 'a@x');
  assert.equal(b.checkedOut, false); assert.equal(b.usage.weekly.percent, 3); assert.equal(b.plan, 'Plus');
  assert.equal(rows.filter((r) => r.name === 'chatgpt').length, 0, 'the single hardcoded row is gone');
});

test('decideCodex: an exhausted account moves to one with room, and never to a Claude account', () => {
  const t = fresh();
  const gpt = (name, pct, active = false) => ({ name, provider: 'openai', checkedOut: active, usage: { session: null, weekly: { percent: pct }, scoped: [] } });
  const rows = [row('perso', 2, 2), gpt('gpt-a', 99, true), gpt('gpt-b', 4)];

  const d = t.m.decideCodex({ rows, active: 'gpt-a', thresholds: T });
  assert.equal(d.action, 'failover');
  assert.equal(d.to, 'gpt-b');

  // Nowhere to go: say so once, and do not reach across families however empty perso is.
  const d2 = t.m.decideCodex({ rows: [row('perso', 2, 2), gpt('gpt-a', 99, true), gpt('gpt-b', 97)], active: 'gpt-a', thresholds: T });
  assert.equal(d2.action, 'exhausted');
  assert.notEqual(d2.to, 'perso');
  assert.equal(d2.suggest, 'claude-fable-5-1', 'the alert names the Claude model at the same tier; nothing reroutes for us');
  assert.match(d2.reason, /\/model claude-fable-5-1/);
});

test('decideCodex: a 429 from the gateway outranks a low meter; a 401 is a dead login, not a spent one', () => {
  const t = fresh();
  const gpt = (name, pct, active = false) => ({ name, provider: 'openai', checkedOut: active, usage: { session: null, weekly: { percent: pct }, scoped: [] } });
  const rows = [gpt('gpt-a', 10, true), gpt('gpt-b', 4)];
  const at = new Date().toISOString();

  const d = t.m.decideCodex({ rows, active: 'gpt-a', thresholds: T, blocked: { state: 'upstream-blocked', statusCode: 429, observedAt: at } });
  assert.equal(d.action, 'failover', 'the meter goes blind exactly when an account is busiest');
  assert.equal(d.to, 'gpt-b');

  const d2 = t.m.decideCodex({ rows, active: 'gpt-a', thresholds: T, blocked: { state: 'upstream-blocked', statusCode: 401, observedAt: at } });
  assert.equal(d2.action, 'none', 'a broken login is not a spent window; switching accounts hides it');

  const old = new Date(Date.now() - 3 * 60 * 60e3).toISOString();
  const d3 = t.m.decideCodex({ rows, active: 'gpt-a', thresholds: T, blocked: { state: 'upstream-blocked', statusCode: 429, observedAt: old } });
  assert.equal(d3.action, 'none', 'the file is sticky, so old evidence must expire like a Claude refusal');
});

test('the two domains do not interact: one tick can move both, neither reads the other rows', () => {
  const t = fresh();
  const gpt = (name, pct, active = false) => ({ name, provider: 'openai', checkedOut: active, usage: { session: null, weekly: { percent: pct }, scoped: [] } });
  const rows = [row('perso', 99, 99, { checkedOut: true }), row('work', 5, 5), gpt('gpt-a', 99, true), gpt('gpt-b', 4)];

  const claude = t.m.decide({ rows, live: 'perso', home: 'perso', thresholds: T });
  assert.equal(claude.action, 'failover'); assert.equal(claude.to, 'work', 'the Claude decision ignores the ChatGPT rows');

  const codex = t.m.decideCodex({ rows, active: 'gpt-a', thresholds: T });
  assert.equal(codex.action, 'failover'); assert.equal(codex.to, 'gpt-b', 'and the ChatGPT decision ignores the Claude rows');
});

// ---------- a deprioritised account is a last resort ----------
test('decide: a preferred account with room always beats an emptier deprioritised one', () => {
  const t = fresh();
  // perso is Louis's personal account and marked last resort; work2 has room, so emptiness must NOT decide.
  const rows = [row('work', 99, 99, { checkedOut: true }), row('perso', 0, 0, { priority: 1 }), row('work2', 60, 60)];
  const d = t.m.decide({ rows, live: 'work', home: 'work', thresholds: T });
  assert.equal(d.action, 'failover');
  assert.equal(d.to, 'work2', 'priority tiers first, emptiness only within a tier');
  assert.equal(d.warn, null, 'nothing to warn about: a preferred account took it');
});

test('decide: with nothing preferred left it takes the deprioritised one and says so', () => {
  const t = fresh();
  const rows = [row('work', 99, 99, { checkedOut: true }), row('perso', 0, 0, { priority: 1 }), row('work2', 97, 97)];
  const d = t.m.decide({ rows, live: 'work', home: 'work', thresholds: T });
  assert.equal(d.action, 'failover');
  assert.equal(d.to, 'perso', 'a dead machine is worse than being on the personal account');
  assert.match(d.warn, /perso is deprioritised/);
  assert.match(d.warn, /work2 5h 97%/, 'the warning names the window that had no room, so the choice is auditable');
});

test('decide: two deprioritised accounts still sort by emptiness between themselves', () => {
  const t = fresh();
  const rows = [row('work', 99, 99, { checkedOut: true }), row('perso', 40, 40, { priority: 1 }), row('perso2', 10, 10, { priority: 1 })];
  const d = t.m.decide({ rows, live: 'work', home: 'work', thresholds: T });
  assert.equal(d.to, 'perso2');
  assert.equal(d.warn, null, 'no preferred account existed to have had no room, so there is nothing to report');
});

test('decideCodex: the same tiering applies to ChatGPT accounts', () => {
  const t = fresh();
  const gpt = (name, pct, extra = {}) => ({ name, provider: 'openai', usage: { session: null, weekly: { percent: pct }, scoped: [] }, ...extra });
  const rows = [gpt('gpt-work', 99, { checkedOut: true }), gpt('gpt-perso', 0, { priority: 1 }), gpt('gpt-team', 50)];
  const d = t.m.decideCodex({ rows, active: 'gpt-work', thresholds: T });
  assert.equal(d.to, 'gpt-team');
  assert.equal(d.warn, null);

  const d2 = t.m.decideCodex({ rows: [gpt('gpt-work', 99, { checkedOut: true }), gpt('gpt-perso', 0, { priority: 1 })], active: 'gpt-work', thresholds: T });
  assert.equal(d2.to, 'gpt-perso');
  assert.equal(d2.warn, null, 'the only possible move is not news; the warning is for "a preferred account had no room"');
});

// ---------- which pane hit the wall ----------
test('paneForPid: walks a process up to the tmux pane that owns it', () => {
  const t = fresh();
  const panes = [{ pid: 100, addr: 'alakazam:3.0', cwd: '/w/a', win: 'claude' }, { pid: 200, addr: 'social:1.0', cwd: '/w/s', win: 'zsh' }];
  // 501 -> 500 -> 200 : a claude process nested two deep under the social pane.
  const tree = { 501: 500, 500: 200, 200: 1, 100: 1 };
  assert.equal(t.m.paneForPid(501, panes, (pid) => tree[pid] || 0).addr, 'social:1.0');
  assert.equal(t.m.paneForPid(100, panes, (pid) => tree[pid] || 0).addr, 'alakazam:3.0');
  assert.equal(t.m.paneForPid(999, panes, () => 0), null, 'a process under no pane is not attributed to a random one');
  // A broken parent chain must terminate rather than spin.
  assert.equal(t.m.paneForPid(7, panes, (pid) => pid), null);
});

test('codexCulprit: the last Codex request at or before the rejection is the session that spent it', () => {
  const t = fresh();
  const log = path.join(t.home, '.claude', 'model-gateway', 'logs', 'request-routes.jsonl');
  fs.mkdirSync(path.dirname(log), { recursive: true });
  const at = (ms) => new Date(Date.now() - ms).toISOString();
  fs.writeFileSync(log, [
    { at: at(60e3), backend: 'codex', model: 'gpt-6-astra', sessionId: 'old-gpt' },
    { at: at(30e3), backend: 'anthropic', model: 'claude-opus-5', sessionId: 'a-claude-one' },
    { at: at(10e3), backend: 'codex', model: 'gpt-6-astra', sessionId: 'the-culprit' },
    { at: at(-30e3), backend: 'codex', model: 'gpt-6-astra', sessionId: 'after-the-fact' },
  ].map((r) => JSON.stringify(r)).join('\n') + '\n');

  assert.equal(t.m.codexCulprit({ observedAt: new Date().toISOString() }), 'the-culprit');
  assert.equal(t.m.codexCulprit({ observedAt: at(20e3) }), 'old-gpt', 'evidence after the rejection is not the cause of it');
});

test('attribute: argv carries a resumed session id, which beats guessing from the directory', () => {
  const t = fresh();
  const panes = [{ pid: 100, addr: 'alakazam:1.0', cwd: '/w/onnx', win: 'claude' }, { pid: 200, addr: 'alakazam:2.0', cwd: '/w/onnx', win: 'claude' }];
  const tree = { 101: 100, 201: 200, 100: 1, 200: 1 };
  const ppid = (pid) => tree[pid] || 0;
  const procs = [
    { pid: 101, cmd: 'claude --enable-auto-mode --rc', cwd: '/w/onnx' },
    { pid: 201, cmd: 'claude --resume sess-abc', cwd: '/w/onnx' },
  ];
  assert.equal(t.m.attribute({ sessionId: 'sess-abc', cwd: '/w/onnx', panes, procs, ppid }), 'tmux alakazam:2.0 [claude] /w/onnx');
  // Without that id, two panes sit in the same repo: say both rather than pick one and be wrong half the time.
  assert.equal(t.m.attribute({ sessionId: 'sess-zzz', cwd: '/w/onnx', panes, procs, ppid }), 'one of tmux alakazam:1.0, alakazam:2.0 (/w/onnx)');
  // One pane in that repo is unambiguous.
  assert.equal(t.m.attribute({ cwd: '/w/onnx', panes, procs: [procs[0]], ppid }), 'tmux alakazam:1.0 [claude] /w/onnx');
  assert.equal(t.m.attribute({ cwd: '/elsewhere', panes, procs, ppid }), null, 'no match beats a wrong match');
});

test('attribution never throws when tmux, /proc or the log has nothing to say', () => {
  const t = fresh();
  assert.equal(t.m.attribute({ sessionId: 'x', cwd: '/nope', panes: [], procs: [] }), null, 'no tmux server at all');
  assert.equal(t.m.attribute({}), null);
  // These two read the real tmux server and /proc, so the only thing worth asserting is the contract — that they
  // cannot throw into the notify path. Asserting a RETURN value here would make the suite depend on which panes
  // happen to be open, which is how a machine-dependent flake gets in.
  assert.doesNotThrow(() => t.m.whereFromSession('no-such-session'));
  assert.doesNotThrow(() => t.m.claudeProcs());
  assert.equal(t.m.codexCulprit(null), null, 'no gateway log on this machine is normal, not an error');
  assert.equal(t.m.readCodexBlocked(), null);
  assert.equal(t.m.transcriptFor(null), null);
});

// ---------- the ChatGPT half of a tick ----------
test('actOnCodex: a failover moves the link, alerts, and touches no Claude state', async () => {
  const t = fresh();
  await machineOnPerso(t);
  seedCodexAccount(t, 'gpt-a', 'a'); seedCodexAccount(t, 'gpt-b', 'b'); linkCodex(t, 'gpt-a');
  const liveBefore = t.m.state().live, credsBefore = fs.readFileSync(t.m.CREDS_FILE, 'utf8');

  await t.m.actOnCodex({ cd: { action: 'failover', to: 'gpt-b', reason: 'gpt-a 99% >= 95%, gpt-b 4%' }, at: new Date().toISOString(), s: {}, t: T, blocked: null });
  assert.equal(t.m.codexActive(), 'gpt-b');
  assert.equal(t.m.state().live, liveBefore, 'the live Claude account is untouched');
  assert.equal(fs.readFileSync(t.m.CREDS_FILE, 'utf8'), credsBefore);
  assert.match(fs.readFileSync(t.m.AUTO_LOG, 'utf8'), /gateway ChatGPT gpt-a → gpt-b/);
});

test('actOnCodex: exhaustion names the same-tier Claude model once, not every tick', async () => {
  const t = fresh();
  seedCodexAccount(t, 'gpt-a', 'a'); linkCodex(t, 'gpt-a');
  const cd = { action: 'exhausted', notify: true, suggest: 'claude-fable-5-1', reason: 'gpt-a 100% >= 95%, and no other ChatGPT account is under 85%' };
  await t.m.actOnCodex({ cd, at: new Date().toISOString(), s: {}, t: T, blocked: null });
  const log1 = fs.readFileSync(t.m.AUTO_LOG, 'utf8');
  assert.match(log1, /every ChatGPT account is spent/);
  assert.match(log1, /\/model claude-fable-5-1/, 'the alert carries the switch Louis has to make by hand');
  assert.ok(t.m.state().codexExhaustedSince, 'the episode is recorded so the next tick stays quiet');

  await t.m.actOnCodex({ cd: { ...cd, notify: false }, at: new Date().toISOString(), s: { codexExhaustedSince: t.m.state().codexExhaustedSince }, t: T, blocked: null });
  assert.equal(fs.readFileSync(t.m.AUTO_LOG, 'utf8'), log1, 'a still-exhausted tick says nothing new');
});

test('actOnCodex: a switch that fails is reported and leaves the link where it was', async () => {
  const t = fresh();
  seedCodexAccount(t, 'gpt-a', 'a'); linkCodex(t, 'gpt-a');
  await t.m.actOnCodex({ cd: { action: 'failover', to: 'gpt-missing', reason: 'x' }, at: new Date().toISOString(), s: {}, t: T, blocked: null });
  assert.equal(t.m.codexActive(), 'gpt-a');
  assert.match(fs.readFileSync(t.m.AUTO_LOG, 'utf8'), /FAILED/);
});

// ---------- `cost --json`: the machine-readable contract a consumer builds on ----------
// Everything here is a synthetic transcript under the temp HOME; no real transcript, no price refresh, no network.
let costSeq = 0;
// Deliberately NOT called seedTranscript: the refusal scanner above already declares one, and a second function
// declaration of that name hoists over it and silently re-points every earlier test at this one.
function seedCostTranscript(t, rel, lines) {
  const f = path.join(t.m.CFG, 'projects', rel);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  fs.writeFileSync(f, lines.map((l) => JSON.stringify(l)).join('\n') + '\n');
  return f;
}
const costTurn = (i, { cwd = '/w/repo', ts = '2026-09-17T10:00:00.000Z' } = {}) => ({ type: 'assistant', timestamp: ts, cwd,
  message: { id: `cu-${++costSeq}`, model: 'claude-opus-5', usage: { input_tokens: i, output_tokens: 1 } } });
// cost.js resolves HOME once, at require time, and cmdCost pulls it in lazily — so it has to be dropped from the
// module cache alongside claude-usage.js, or a later test reads the temp HOME of an earlier one.
function costJson(t, opts) {
  return JSON.parse(costText(t, { ...opts, json: true }));
}
function costText(t, opts) {
  delete require.cache[path.join(__dirname, 'cost.js')];
  let out = '';
  console.log = (m = '') => { out += `${m}\n`; };
  try { t.m.cmdCost({ ...opts }); } finally { console.log = () => {}; }
  return out;
}
// A turn whose flat cache-write count and per-TTL split contradict each other: neither quantity can be believed.
const conflictTurn = (flat, h1, { cwd = '/w/repo', ts = '2026-09-17T10:00:00.000Z' } = {}) => ({ type: 'assistant', timestamp: ts, cwd,
  message: { id: `cx-${++costSeq}`, model: 'claude-opus-5', usage: { cache_creation_input_tokens: flat, cache_creation: { ephemeral_1h_input_tokens: h1 } } } });

test('cost --json: session metadata is scoped to what the rows actually cover', () => {
  const t = fresh();
  // Emitting every session on the machine under --roots made the scope line and the metadata disagree, and a
  // consumer that trusted the metadata described sessions the report had deliberately excluded.
  seedCostTranscript(t, 'proj/ROOT.jsonl', [costTurn(10)]);
  seedCostTranscript(t, 'proj/ROOT/subagents/agent-1.jsonl', [costTurn(20)]);
  seedCostTranscript(t, 'proj/OTHER.jsonl', [costTurn(40)]);
  const all = costJson(t, {});
  assert.deepEqual(Object.keys(all.sessions).sort(), ['OTHER', 'ROOT', 'agent-1']);

  const scoped = costJson(t, { roots: 'ROOT' });
  assert.deepEqual(Object.keys(scoped.sessions).sort(), ['ROOT', 'agent-1'], 'OTHER is outside the requested roots');
  assert.deepEqual(scoped.scope.resolvedRoots, ['ROOT']);

  const byRepo = costJson(t, { repo: '/w/only' });
  assert.deepEqual(Object.keys(byRepo.sessions), [], 'no session touched that repo, so none is described');
});

test('cost --json: two files sharing a session id merge their metadata instead of overwriting', () => {
  const t = fresh();
  // A repo move leaves the same session id under two project directories. Keyed by id, whichever was written last
  // erased the other's existence — the tokens stayed in the totals, but the identity was gone.
  seedCostTranscript(t, 'old-path/twin.jsonl', [costTurn(1000, { cwd: '/w/old', ts: '2026-09-15T10:00:00.000Z' })]);
  seedCostTranscript(t, 'new-path/twin.jsonl', [costTurn(3000, { cwd: '/w/new', ts: '2026-09-17T10:00:00.000Z' })]);
  const out = costJson(t, {});
  const meta = out.sessions.twin;
  assert.equal(meta.files.length, 2, 'both transcripts are named');
  assert.deepEqual([...meta.cwds].sort(), ['/w/new', '/w/old'], 'every cwd the id was seen in');
  assert.equal(meta.first, '2026-09-15T10:00:00.000Z', 'the earliest of the two');
  assert.equal(meta.last, '2026-09-17T10:00:00.000Z', 'and the latest');
  assert.equal(out.rows.reduce((a, r) => a + r.in, 0), 4000, 'and no token was lost either way');
});

test('cost: the human report blames contradictory counts, not a missing rate', () => {
  const t = fresh();
  // The old legend said "a model or a cache rate is missing from the price table, so this is a FLOOR". When the
  // cause is two counts disagreeing, that sentence is simply untrue — and it told the reader the number was an
  // under-estimate when the engine had in fact been over-charging.
  seedCostTranscript(t, 'proj/a.jsonl', [conflictTurn(1e6, 2e6), costTurn(1e6)]);
  const out = costText(t, {});
  assert.match(out, /counts? (that )?contradict|contradictory counts/i, 'the cause is named');
  assert.doesNotMatch(out, /a model or a cache rate is missing from the price table/,
    'that legend must not be printed when no rate is missing');
  assert.match(out, /1 turn\(s\)/, 'and it says how many turns were left out of the money');
});

test('cost: a genuinely missing rate still says so', () => {
  const t = fresh();
  // The rate-missing legend has to survive: it is still the right answer when it is the right answer.
  seedCostTranscript(t, 'proj/a.jsonl', [{ type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/repo',
    message: { id: 'unknown-1', model: 'no-such-model-anywhere', usage: { input_tokens: 10, output_tokens: 1 } } }]);
  const out = costText(t, {});
  assert.match(out, /missing from the price table/);
  assert.match(out, /unpriced models: no-such-model-anywhere/);
});

test('cost --json: a consumer can tell a withheld turn from a missing rate', () => {
  const t = fresh();
  seedCostTranscript(t, 'proj/a.jsonl', [conflictTurn(1e6, 2e6), costTurn(1e6)]);
  const out = costJson(t, {});
  const row = out.rows[0];
  assert.equal(row.turns, 2, 'both turns are observed');
  assert.equal(row.withheldTurns, 1, 'one contributes no money');
  assert.deepEqual(row.partialReasons, ['count-conflict']);
  assert.equal(row.cwConflict, 1e6, 'the diagnostic quantity travels with the row');
  assert.equal(row.partial, true);
  // The figure is a known priced subtotal, not a certified total — and the JSON says which turns it excludes.
  assert.ok(out.rows.every((r) => Number.isFinite(r.usd)));
  assert.equal(out.unpriced.length, 0, 'no model here lacks a rate, so the rate list stays empty');
});

// A shared message belongs to exactly one source corpus-wide. When a selection holds a copy but the owner sits
// outside it, the selection's subtotal is allocation-limited — and saying nothing let `partial: false` assert a
// completeness the scope could not see.
function seedSharedOutsideOwner(t) {
  const shared = { type: 'assistant', timestamp: '2026-09-17T10:00:00.000Z', cwd: '/w/repo',
    message: { id: 'SHARED-ID', model: 'claude-opus-5', usage: { input_tokens: 2000000, output_tokens: 1 } } };
  seedCostTranscript(t, 'proj/aaa-outside.jsonl', [shared]);          // sorts first, so it owns the id
  seedCostTranscript(t, 'proj/zzz-root.jsonl', [shared, costTurn(10)]);
}

test('cost --json: a scope whose shared message is allocated outside says its coverage is incomplete', () => {
  const t = fresh();
  seedSharedOutsideOwner(t);
  const scoped = costJson(t, { roots: 'zzz-root' });
  const cov = scoped.scope.allocationCoverage;
  assert.equal(cov.complete, false);
  assert.equal(cov.allocatedElsewhereIds, 1);
  assert.match(cov.meaning, /allocation/i, 'the field documents itself');
  assert.doesNotMatch(JSON.stringify(scoped.scope), /campaign total/i, 'coverage is not a second total');
  assert.ok(!JSON.stringify(cov).includes('SHARED-ID'), 'no raw message id is emitted');
  assert.ok(!JSON.stringify(cov).includes('.jsonl'), 'and no file path either');
  // Pricing completeness is a separate question and must not have moved.
  assert.equal(scoped.rows.every((r) => r.partial === false), true);

  const whole = costJson(t, {});
  assert.equal(whole.scope.allocationCoverage.complete, true, 'the full corpus is always fully represented');
  assert.equal(whole.scope.allocationCoverage.allocatedElsewhereIds, 0);
});

test('cost: the human report flags a scope that is missing work allocated elsewhere', () => {
  const t = fresh();
  seedSharedOutsideOwner(t);
  const out = costText(t, { roots: 'zzz-root' });
  assert.match(out, /outside the selected scope/i, 'the reader is told why');
  assert.match(out, /not complete/i);
  assert.doesNotMatch(costText(t, {}), /outside the selected scope/i, 'a whole scope says nothing about coverage');
});

// No module.exports here on purpose: requiring this file runs the whole suite and silences console.log — a trap, not a seam.
