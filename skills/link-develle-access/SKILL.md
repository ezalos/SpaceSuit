---
name: link-develle-access
description: Use when Louis wants to put a `develle.fr` subdomain behind Google login or open it back up — e.g. "gate upload.develle.fr", "make X.develle.fr private", "make Y public", "require login on Z", "what's behind auth", "who can reach W". Wraps cloudflare-access/access.sh (Cloudflare Access + Google SSO, one-click, 30-day session).
allowed-tools: Read, Edit, Write, Bash, Grep, AskUserQuestion
---

## Observability

This skill follows the universal observability baseline (see `docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction (Claude was about to be wrong), fallback, retry, precondition-fail.
- **INFO (systematic) on any user feedback, suggestion, or caveat during the run.** Every distinct user message that conveys preference, redirection, refinement, or commentary MUST be logged. Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>` (or `no change — already on track`).
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| CRITICAL | `develle-access` exits non-zero | `link-develle-access: develle-access <cmd> failed: <stderr-tail>` |
| CRITICAL | Cloudflare API returns 401/403 (token invalid or insufficient scope) | `link-develle-access: cloudflare auth failed: <status>` |
| WARNING | `CLOUDFLARE_ACCESS_TOKEN_GCLOUD_AUTH` missing from env | `link-develle-access: access token not in env; not vaulted yet, needs to be in env or ~/42/TheHarness/network/domains/.envrc as plaintext (secrets check there)` |
| WARNING | `protect` requested on a grey-cloud / DNS-only host (Access can't enforce at the edge) | `link-develle-access: <host> is DNS-only; Access won't intercept — needs proxy/tunnel first` |
| WARNING | `protect` on a host with non-browser clients (API/WebDAV) | `link-develle-access: <host> has non-browser clients; flagged need for a service token` |
| WARNING | `public` requested (removes a gate — always confirm) | `link-develle-access: public <host> requested; confirmed with Louis before removing the gate` |
| WARNING | adding the Google IdP changes login UX for apps with `allowed_idps: []` (e.g. n8n) | `link-develle-access: empty-idp app <host> will also show Google now; flagged` |
| INFO | `develle-access list` / `status` displayed | `link-develle-access: state shown; <N> gated apps` |
| INFO | `protect` / `public` applied | `link-develle-access: <host> -> <gated|public>` |

Concrete invocation examples:

```
claude-log link-develle-access INFO "link-develle-access: starting; action=protect host=upload.develle.fr"
claude-log link-develle-access WARNING "link-develle-access: upload has non-browser clients; flagged need for a service token"
claude-log link-develle-access CRITICAL "link-develle-access: cloudflare auth failed: 403 on /access/apps"
```

# triggers I might have missed: <none>

# link-develle-access

Gates `develle.fr` subdomains behind **Google SSO** via Cloudflare Access — or removes the gate to make them public. One-click login (the browser is already signed into Google), 30-day session cookie, no email/PIN. The Cloudflare side IS the source of truth (there's no local config file); `develle-access list` reflects live state. Wraps `~/42/TheHarness/network/domains/cloudflare-access/access.sh`.

## Required env

Sourced from `~/42/TheHarness/network/domains/.envrc` via direnv (private `ezalos` GitHub repo):

| Var | Purpose |
|---|---|
| `CLOUDFLARE_ACCESS_TOKEN_GCLOUD_AUTH` | Account token — Access Apps+Policies Edit, Access Orgs/IdPs Edit |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account id (non-secret) |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Only needed the first time, to create the Google IdP |

If `develle-access` complains the token is missing: it isn't vaulted yet, so **in an agent shell it will always be missing** — direnv doesn't run non-interactively and `secrets run` only resolves `pass://` refs, not plaintext literals. Load just the lines you need, which never prints their values:

```bash
cd ~/42/TheHarness/network/domains && eval "$(grep -E '^export (CLOUDFLARE_ACCESS_TOKEN_GCLOUD_AUTH|CLOUDFLARE_ACCOUNT_ID)=' .envrc)" && develle-access list
```

Never accept a token typed inline. Once vaulted, `secrets run --` is the right form and this workaround should be deleted.

**Order matters when a host is about to go live: gate first, create the DNS record second.** `develle-dns sync` has no per-record filter, so it publishes everything in `dns.json` — including any half-finished hostname a previous session left there. Check `develle-access list` against the pending `develle-dns status` diff and protect every name that is about to be created, or it is briefly (or permanently) open.

## Commands

```bash
develle-access list                 # identity providers + every Access app and its gate
develle-access status <sub>         # one host: config, policy, and a live 302/200 probe
develle-access protect <sub> [email]  # gate <sub>.develle.fr behind Google (default email: ezalos@gmail.com)
develle-access public  <sub>        # remove the Access app -> <sub>.develle.fr is public again
develle-access idp                  # ensure the Google IdP exists; print its id
```

## Workflow

1. **Read state** — `develle-access list` (and `status <sub>` for the host in question). Show Louis what's gated vs public so it's clear whether the change is a no-op.
2. **Apply** — `protect` to gate, `public` to open. `protect` is idempotent (says EXISTS if already gated); `public` is idempotent (says already-public if no app).
3. **Verify** — `develle-access status <sub>`. Gated = `HTTP 302 -> develle-one.cloudflareaccess.com`. Public = `HTTP 200` (or the app's own response). Confirm before declaring done.

## Reminders

- **Edge-only enforcement.** Access intercepts only on hostnames that pass through Cloudflare — proxied A records or tunnel CNAMEs (share/deck/upload/diary/n8n). A grey-cloud DNS-only host bypasses the edge, so `protect` won't actually gate it; it must be proxied/tunneled first (see `link-develle-domain`).
- **Tight policy.** `protect` writes a per-email allow policy (`include: [{email: ...}]`), NOT `everyone`. To add a friend later: `develle-access protect <sub> friend@example.com` recreates with that email, or edit the policy directly. (n8n's pre-existing policy is `everyone`+OTP — weak; tighten if it matters.)
- **Google-only, no chooser.** Apps are created with `allowed_idps: [google]` + `auto_redirect_to_identity: true`, so login goes straight to Google. The first time the Google IdP is added, apps with `allowed_idps: []` (n8n) will ALSO start offering Google — not a security change, but flag it.
- **Non-browser clients break when gated.** Anything hitting the host with curl/WebDAV/an API call gets bounced to login. For those, mint a Cloudflare Access **service token** instead of an email policy.
- **`upload` has a Caddy `basic_auth` backstop** at the origin (double-prompt after Google). Removing it needs the origin confirmed reachable *only* via the tunnel (external bypass test) first.
- Changing the team name in Cloudflare breaks the Google redirect URI (`https://develle-one.cloudflareaccess.com/cdn-cgi/access/callback`) — keep them in sync.
