# share_file

Token-gated file sharing on `https://share.develle.fr`.

## Architecture

```
TheBeast: share-file CLI
   └─ secrets.token_urlsafe(24) → 32-char token (192 bits entropy)
   └─ scp <file> tinybutmighty:/srv/share/<token>/
   └─ ssh tinybutmighty 'echo $expires_at > /srv/share/<token>/.expires'
   └─ prints https://share.develle.fr/<token>/<filename>

Internet viewer
   → SFR Box NAT (ext 443 → <proxy-host>:443)
   → Caddy on TinyButMighty (cert via Cloudflare DNS-01, real Let's Encrypt)
   → file_server on /srv/share with strict /<token>/<filename> regex

TinyButMighty: share-cleanup.timer (every 5 min)
   → /opt/share_file/cleanup.py removes /srv/share/<token>/ where now > .expires
```

## One-time bootstrap

These steps are manual — they touch the router, the Pi, and Cloudflare DNS.
Run from TheBeast unless noted. Steps 4 and 5 are best driven by the
`open-local-port` and `link-develle-domain` Claude skills.

### 1. Install Caddy on the Pi (Debian Bookworm aarch64)

Use the official Cloudsmith-hosted apt repo — Bookworm's distro caddy is
behind v2.7 (which is needed for `caddy add-package`).

```bash
ssh TinyButMighty 'sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt" | sudo tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
sudo apt-get update -qq
sudo apt-get install -y caddy
caddy version'
```

The package creates the `caddy` user/group.

### 2. Add the Cloudflare DNS module

Caddy v2.7+ supports adding modules to the running binary without xcaddy:

```bash
ssh TinyButMighty 'sudo caddy add-package github.com/caddy-dns/cloudflare && caddy list-modules | grep cloudflare'
```

Should print `dns.providers.cloudflare`.

### 3. Storage on the Pi

```bash
ssh TinyButMighty 'sudo mkdir -p /srv/share && \
  sudo chown caddy:caddy /srv/share && \
  sudo chmod 2775 /srv/share && \
  sudo usermod -aG caddy ezalos'
```

`2775` = group-writable + setgid (so files dropped by `share.py` running as
`ezalos` end up in the `caddy` group automatically). `usermod -aG caddy ezalos`
lets `ezalos` write into `/srv/share`. Re-login after this for the supplementary
group to take effect in interactive shells (ssh sessions get fresh group lookup
each connection, so share.py works immediately from TheBeast without re-login).

### 4. Deploy Caddyfile + token

```bash
scp ~/Setup/share_file/Caddyfile TinyButMighty:/tmp/Caddyfile
ssh TinyButMighty 'sudo install -m 0644 -o root -g root /tmp/Caddyfile /etc/caddy/Caddyfile && rm /tmp/Caddyfile'

# Token — pipe via stdin so it never appears in argv or shell history.
( source ~/42/Markdowns2Teach/.envrc >/dev/null 2>&1
  printf "CLOUDFLARE_API_TOKEN=%s\n" "$CLOUDFLARE_API_TOKEN" | \
    ssh TinyButMighty "sudo install -m 0640 -o root -g caddy /dev/stdin /etc/default/caddy" )
```

### 5. systemd drop-in (load EnvironmentFile, strip --environ, fix log dir)

The packaged Caddy unit ships with `ExecStart=/usr/bin/caddy run --environ ...`
and **no** EnvironmentFile. The `--environ` flag dumps every loaded env var into
the journal — including `CLOUDFLARE_API_TOKEN`. Override both:

```bash
ssh TinyButMighty 'sudo mkdir -p /etc/systemd/system/caddy.service.d
sudo tee /etc/systemd/system/caddy.service.d/override.conf > /dev/null <<EOF
[Service]
EnvironmentFile=-/etc/default/caddy
LogsDirectory=caddy
LogsDirectoryMode=0750
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
EOF
sudo systemctl daemon-reload'
```

`LogsDirectory=caddy` makes systemd own `/var/log/caddy` with the right perms.
The empty `ExecStart=` line resets the upstream value, then the second
`ExecStart=` provides the replacement (without `--environ`).

### 6. Start Caddy

```bash
ssh TinyButMighty 'sudo systemctl enable --now caddy && sleep 8 && sudo journalctl -u caddy -n 30 --no-pager'
```

Look for `certificate obtained successfully` for `share.develle.fr`. The DNS-01
challenge happens automatically — no port-80 collision because the Caddyfile
sets `auto_https disable_redirects` (port 80 is owned by nginx for slides).

If a previous failed run left `/var/log/caddy/share.access.log` owned by root
(possible if you ran `sudo caddy validate ...` before configuring the LogsDirectory),
chown it: `ssh TinyButMighty 'sudo chown caddy:caddy /var/log/caddy/share.access.log'`.

### 7. NAT rule for 443

Use the `open-local-port` skill. Equivalent CLI:

```bash
cd ~/42/GroundControl/network/routers/nat_manager
uv run python nat.py add share_https 443 74 443 --proto tcp
```

### 8. DNS record

Use the `link-develle-domain` skill. The `share` A record is already in
`~/Setup/cloudflare-dns/dns.json`. Sync:

```bash
( source ~/42/Markdowns2Teach/.envrc >/dev/null 2>&1
  cd ~/Setup/cloudflare-dns && ./dns.sh status && ./dns.sh sync )
```

`proxied: false` is required because Caddy issues a real cert end-to-end and
visitors connect direct (no Cloudflare edge). Could flip to proxied + per-hostname
"Full" SSL via a Cloudflare Configuration Rule later.

### 9. Janitor

`cleanup.py` lives at `/opt/share_file/cleanup.py` on the Pi (Setup is not
cloned there).

```bash
scp ~/Setup/share_file/cleanup.py ~/Setup/share_file/share-cleanup.{service,timer} TinyButMighty:/tmp/
ssh TinyButMighty '
  sudo install -d -m 0755 /opt/share_file
  sudo install -m 0755 /tmp/cleanup.py /opt/share_file/cleanup.py
  sudo install -m 0644 /tmp/share-cleanup.service /etc/systemd/system/share-cleanup.service
  sudo install -m 0644 /tmp/share-cleanup.timer /etc/systemd/system/share-cleanup.timer
  rm /tmp/cleanup.py /tmp/share-cleanup.service /tmp/share-cleanup.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now share-cleanup.timer
  systemctl list-timers share-cleanup.timer --no-pager
'
```

### 10. SSH key

`share.py` calls `ssh TinyButMighty` and `scp TinyButMighty:...`. Make sure
`~/.ssh/config` on TheBeast has a `Host TinyButMighty` block with key auth (no
password prompts). Test: `ssh TinyButMighty 'echo ok'`.

### 11. Smoke test

```bash
echo hello > /tmp/x.txt
share-file /tmp/x.txt --duration 5m
# → https://share.develle.fr/<token>/x.txt
curl -sI <url>             # 200 OK
# wait 6 minutes, retry; should 404 after the timer's next pass
```

## CLI reference

```
share-file <path> [--duration 1h] [--host TinyButMighty] [--remote-root /srv/share] [--base-url https://share.develle.fr]
```

Duration syntax: `30s`, `15m`, `1h` (default), `2d`. No combined units.

## Security notes

- 32-char URL-safe base64 token = 192 bits. A trillion guesses/sec would take ~10^36 years to find one valid link.
- No directory listing (`browse off`) and a strict regex — root `/`, `/<token>/`, partial paths, traversal, dotfiles all return 404.
- The `share` DNS record is **not** Cloudflare-proxied, so the home public IP is exposed for this hostname. Acceptable; can flip to proxied + "Full" SSL Configuration Rule later.
- Files live unencrypted on the Pi until expiry. Don't share secrets with this — use `age` or similar first if you need to.
- Caddy logs every fetch to `/var/log/caddy/share.access.log` — useful for forensics, also a privacy footprint to be aware of.
- The systemd drop-in deliberately strips the upstream `--environ` flag to keep the API token out of `journalctl`.
