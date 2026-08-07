---
name: open-local-port
description: Use when Louis asks to open an external port on his home network — e.g. "open port 9000", "expose service X to the internet", "add a NAT rule", "forward port", "let people reach my dev server". Wraps the SFR Box NAT manager and (when HTTP-shaped) the TinyButMighty nginx reverse proxy.
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
| CRITICAL | `open-port add` fails (router unreachable, auth, etc.) | `open-local-port: nat add failed for <name>: <reason>` |
| CRITICAL | `nginx -t` fails after writing new config | `open-local-port: nginx config invalid for <name>: <stderr-tail>` |
| WARNING | `SFR_BOX_PASSWORD` missing from env at start | `open-local-port: SFR_BOX_PASSWORD not in env; asked Louis to source ~/.secrets.sh` |
| WARNING | Requested ext_port or name already in NAT rules | `open-local-port: collision: <port-or-name> already taken by rule <id>` |
| WARNING | Requested reserved port 80/443 without explicit confirmation | `open-local-port: reserved port <port> requested; confirmed with Louis` |
| WARNING | Asked to re-enable disabled rule 1 (ssh ext 22) | `open-local-port: re-enable of disabled ssh ext 22 requested; confirmed with Louis` |
| WARNING | Proxied DNS handoff requested for non-CF-friendly port | `open-local-port: handoff to link-develle-domain with proxied=true on port <port>; recommended grey-cloud` |
| INFO | NAT rule added | `open-local-port: rule added: <name> ext=<port> dst=<dst>:<dst_port> proto=<proto>` |
| INFO | nginx server block added and reloaded | `open-local-port: nginx server block added for <name>; reload OK` |
| INFO | Reachability check from outside LAN succeeded | `open-local-port: reachability OK for <public-ip>:<ext_port>` |

Concrete invocation examples:

```
claude-log open-local-port INFO "open-local-port: starting; name=<name> ext=<port>"
claude-log open-local-port WARNING "open-local-port: collision: 9000 already taken by rule 7"
claude-log open-local-port CRITICAL "open-local-port: nat add failed for comfyui: 401 Unauthorized"
```

# triggers I might have missed: <none>

# open-local-port

Opens an external port on Louis's SFR Box and (when relevant) wires up the nginx reverse proxy on TinyButMighty so Internet traffic reaches a service on the LAN.

## Network architecture (memorize)

```
Internet ──► SFR Box (<gateway>) ──► TinyButMighty (<proxy-host>, nginx) ──► TheBeast (<workstation>, services)
                                  └──► TheBeast (<workstation>) directly  (e.g. SSH on ext <ext-ssh-port>)
```

Public IP: redacted (see `~/42/TheStables/network/routers/nat_manager/README.md`). TinyButMighty runs nginx on port 80 already (slides). Most HTTP services follow the proxied path; SSH-to-TheBeast goes direct on port <ext-ssh-port>.

## Inputs to gather

Use AskUserQuestion if any are missing:

| Field | Notes |
|---|---|
| `name` | rule name, **≤20 chars**, e.g. `comfyui`, `share_https` |
| `ext_port` | external port (1-65535) |
| `dst` | `74` (TinyButMighty) or `96` (TheBeast). Last octet only also accepted by `open-port`. |
| `dst_port` | usually same as `ext_port`; on `74` it's the nginx listen port |
| `proto` | `tcp` (default), `udp`, or `both` |
| `service_kind` | `tcp-stream` (raw passthrough) or `http` (so we add nginx) |

## Workflow

### 1. List current rules first (collision check)

```bash
open-port list
```

If the chosen `ext_port` or `name` is already taken, surface that to Louis before doing anything else.

### 2. Add the NAT rule

```bash
open-port add <name> <ext_port> <dst> <dst_port> --proto <proto>
```

Requires `SFR_BOX_PASSWORD` (and optionally `SFR_BOX_LOGIN`) in env — sourced from `~/.secrets.sh` by `.zshrc`. If unset, ask Louis to source it; do not prompt for the password directly.

Re-run `open-port list` to confirm.

### 3. (HTTP only) Add nginx server block on TinyButMighty

If `dst=74` and `service_kind=http`, the Pi's nginx routes by port (and/or `server_name`). Create a config file:

```bash
ssh TinyButMighty "sudo tee /etc/nginx/conf.d/<name>.conf > /dev/null" <<'EOF'
server {
    listen <ext_port>;
    location / {
        proxy_pass http://<workstation>:<backend_port>;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
ssh TinyButMighty "sudo nginx -t && sudo systemctl reload nginx"
```

If `nginx -t` fails, surface the error verbatim and stop — do not reload a broken config.

For `tcp-stream` (e.g. SSH), nginx config goes in the `stream {}` block of `/etc/nginx/nginx.conf`, not `/etc/nginx/conf.d/`. Edit the file directly via ssh in that case.

### 4. Verify reachability

From outside the LAN:

```bash
# from a non-home network
nc -zv <public-ip> <ext_port>            # plain TCP
curl -I http://<public-ip>:<ext_port>/   # HTTP
```

Or from inside the LAN, the public IP usually loops back through the router; test via a phone on cellular if uncertain.

If the user wants a `develle.fr` subdomain pointing at this port, hand off to the `link-develle-domain` skill afterwards.

## Reminders / gotchas

- Rule `1` (`ssh`, ext 22) is **intentionally disabled** — don't enable it without asking.
- `open-port` actions hit the live router. There is no dry-run mode. Always `list` before `add`/`delete`.
- Don't pick `ext_port` 80 or 443 unless Louis explicitly says so — those are reserved for the slides/share stack.
- New ports may be blocked by Cloudflare if the destination is a `develle.fr` subdomain that's *proxied*: Cloudflare only proxies a fixed set of HTTP/HTTPS ports. Use a non-proxied (grey-cloud) DNS record for arbitrary ports.
