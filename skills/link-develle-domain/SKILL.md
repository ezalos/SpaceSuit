---
name: link-develle-domain
description: Use when Louis asks to add or change a DNS record on `develle.fr` — e.g. "make X.develle.fr point at Y", "add a subdomain", "set up a CNAME", "Cloudflare-proxy this", "what DNS records do we have". Wraps the idempotent dns.sh sync tool.
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
| CRITICAL | `develle-dns sync` exits non-zero | `link-develle-domain: dns sync failed: <stderr-tail>` |
| CRITICAL | Cloudflare API returns 401/403 (token invalid or insufficient scope) | `link-develle-domain: cloudflare auth failed: <status>` |
| WARNING | `CLOUDFLARE_API_TOKEN` missing from env | `link-develle-domain: CLOUDFLARE_API_TOKEN not in env; asked Louis to direnv allow ~/42/GroundControl/network/domains` |
| WARNING | User proposed apex as `develle.fr` instead of `@` | `link-develle-domain: apex name confusion; corrected 'develle.fr' to '@'` |
| WARNING | `proxied: true` requested for a port Cloudflare doesn't proxy | `link-develle-domain: proxied=true on non-proxyable port <port>; recommended grey-cloud` |
| WARNING | `proxied: true` with `ttl != 1` (Cloudflare will reject) | `link-develle-domain: proxied record with ttl=<ttl>; corrected to ttl=1` |
| WARNING | Public IP appears rotated vs. existing A records | `link-develle-domain: public IP rotated: <old> -> <new>; flagged stale records` |
| WARNING | `develle-dns delete` requested (always confirm) | `link-develle-domain: delete requested for <name> <type>; confirmed with Louis` |
| INFO | `develle-dns status` diff displayed | `link-develle-domain: status diff: <N> create, <M> update, <K> skip` |
| INFO | `develle-dns sync` applied successfully | `link-develle-domain: sync OK; applied <N> changes` |

Concrete invocation examples:

```
claude-log link-develle-domain INFO "link-develle-domain: starting; record type=<type> name=<name>"
claude-log link-develle-domain WARNING "link-develle-domain: apex name confusion; corrected 'develle.fr' to '@'"
claude-log link-develle-domain CRITICAL "link-develle-domain: dns sync failed: 403 Forbidden on /zones/.../dns_records"
```

# triggers I might have missed: <none>

# link-develle-domain

Manages DNS records for the `develle.fr` zone via the Cloudflare API. The single source of truth is `~/42/GroundControl/network/domains/cloudflare-dns/dns.json`; the `develle-dns sync` command does an idempotent diff-and-apply.

## Inputs to gather

| Field | Required | Default | Notes |
|---|---|---|---|
| `type` | yes | `A` | `A`, `AAAA`, `CNAME`, `TXT`, `MX`, `SRV`, etc. |
| `name` | yes | — | **short** name (`app`, not `app.develle.fr`). Use `@` for apex. |
| `content` | yes | — | IP, hostname, or text value |
| `proxied` | no | `true` (for A/AAAA/CNAME) | Cloudflare orange cloud on/off |
| `ttl` | no | `1` | seconds; `1` = auto. Required when `proxied=true`. |
| `comment` | no | — | annotation on Cloudflare side |

## Required env

`CLOUDFLARE_API_TOKEN` (Zone:DNS:Edit + Zone:Zone:Read). It's in `~/42/GroundControl/network/domains/.envrc` (also still in `~/42/Markdowns2Teach/.envrc`).

**In an agent shell it will be empty, and neither direnv nor `secrets run` fixes it.** direnv doesn't run non-interactively, and `secrets run` only resolves `pass://` refs — this token is still a plaintext literal, so it is invisible to it. Asking Louis to `direnv allow` does nothing for your shell. Load just that one line, which never prints the value:

```bash
cd ~/42/GroundControl/network/domains && eval "$(grep -E '^export CLOUDFLARE_API_TOKEN=' .envrc)" && develle-dns status
```

Same shape for `link-develle-access` (`CLOUDFLARE_ACCESS_TOKEN_GCLOUD_AUTH`, `CLOUDFLARE_ACCOUNT_ID`). Never accept a token typed inline. Once the token is vaulted, `secrets run -- develle-dns status` becomes the right form and this workaround should be deleted.

## Workflow

### 1. Read current state

```bash
cat ~/42/GroundControl/network/domains/cloudflare-dns/dns.json | jq .   # local desired state
develle-dns list                                             # what Cloudflare actually has
```

Show Louis both, so it's clear whether the new record duplicates an existing one.

### 2. Edit `dns.json`

Append (or update) the record in `~/42/GroundControl/network/domains/cloudflare-dns/dns.json` using the `Edit` tool, preserving JSON formatting and existing entries. Schema:

```json
{
  "type": "A",
  "name": "<short-name>",
  "content": "<ip-or-host>",
  "proxied": true,
  "comment": "<short purpose>"
}
```

### 3. Dry-run

```bash
develle-dns status
```

Show Louis the diff (records to create/update/skip). Stop here for review.

### 4. Apply (gated)

```bash
develle-dns sync
```

Then `develle-dns list` to confirm the record landed.

## Combined flows

- **Need an external port too?** Run `open-local-port` first; if the port is non-standard (not 80/443), set `proxied: false` because Cloudflare's proxy only forwards a fixed list of HTTP/HTTPS ports.
- **For proxied A records**, `content` is the **current public IP** of the home connection. NEVER hardcode it here — it rotates, and a committed home IP is a deanonymization leak. Fetch it at use time: `curl -s https://api.ipify.org`. Cloudflare hides it from outside DNS lookups while the record is proxied.
- **Apex (`develle.fr` itself)** uses `name: "@"`. Do not write `develle.fr` as the name.

## Reminders

- `dns.json` is **gitignored** (it leaks origin IPs). Don't ever propose committing it.
- `dns.example.json` is the safe-to-commit template.
- Proxied records with `ttl != 1` will be rejected by Cloudflare.
- `develle-dns delete <name> <type>` exists but requires `--yes` to actually run; never call it without explicit user confirmation.
- Public IP of the home network can change (residential ISP). If a previously working A record stops resolving correctly, the IP may have rotated — check with `curl -s https://api.ipify.org` from TheBeast and update accordingly.
