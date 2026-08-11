# upload_file

WebDAV inbox at `https://upload.develle.fr/inbox/` for phone → server
file drops. Files land on TinyButMighty; `pull-uploads` (in `~/42/SpaceSuit/bin/`)
rsyncs them to `~/Inbox/` on the local machine.

## Architecture

```
Phone (iOS Files / Android WebDAV client)
   └─ HTTPS PUT /inbox/<filename>   (basic auth: ezalos / <password>)
   → SFR Box NAT (ext 443 → <proxy-host>:443)
   → Caddy on TinyButMighty (cert via Cloudflare DNS-01)
   → /srv/upload/inbox/<filename>

Local machine: pull-uploads
   └─ rsync --remove-source-files TinyButMighty:/srv/upload/inbox/ ~/Inbox/
   └─ ssh cleanup of empty subdirs
```

## Resumable / chunked uploads (tus)

The browser UI at `/` uploads via the **tus** resumable protocol (tusd on
`127.0.0.1:1080`, proxied by Caddy at `/files/*`), in ~48 MB chunks. This is
what lets `upload.develle.fr` sit behind a Cloudflare Tunnel: each chunk stays
under Cloudflare's 100 MB proxied-request cap, so any file size works and the
home IP is never exposed. A `tusd` post-finish hook (`/etc/tusd/hooks/post-finish`,
source of truth: `upload_file/tusd/post-finish` in this repo) moves the completed
file into `/srv/upload/inbox/` under a **datetime-prefixed name**
(`YYYY-MM-DD_HHMMSS_<name>`, `_N` on a same-second repeat) — so uploading
`selection.json` five times keeps five distinct files; nothing is ever shadowed
or lost at reception. `pull-uploads` preserves the server's prefix (it only
stamps files that arrive without one). tus is also resumable — an interrupted
upload continues instead of restarting.

The WebDAV `PUT /inbox/*` path still exists for native clients (iOS Files), but
behind the tunnel it is capped at 100 MB (it can't chunk) — use the browser UI
for large files. Caveat: WebDAV PUTs land under their raw name (no prefix; the
hook only sees tus uploads), so a same-name PUT between two pulls overwrites on
the Pi. `pull-uploads` stamps these at fetch time, but the browser UI is the
collision-proof path.

## Daily use

- **Phone**: open Files (iOS) or any WebDAV-capable file manager (Android:
  Material Files, Solid Explorer), upload to the saved `upload.develle.fr`
  server.
- **Laptop**: `pull-uploads` whenever you want the files locally.

## Phone setup (one-time)

### iOS Files

1. Files app → top-right `…` menu → **Connect to Server**.
2. Server: `https://upload.develle.fr/inbox/`
3. Connect As: **Registered User**.
4. Name: `ezalos`, Password: paste from password manager.
5. Tap **Next**. Server appears under "Shared" in the sidebar.

### Android

Recommended: **Material Files** (open-source) or **Solid Explorer**. Both
use the same connect-to-server flow — pick WebDAV / WebDAVs, type the
URL, user, password.

## Rotating the password

1. Generate new plaintext on TheBeast:

   ```bash
   python3 -c 'import secrets; print(secrets.token_urlsafe(24))'
   ```

2. Hash with Caddy on the Pi:

   ```bash
   ssh TinyButMighty 'caddy hash-password --plaintext "<NEW_PLAINTEXT>"'
   ```

3. Edit `Caddyfile.snippet` here, replace the `$2a$14$...` line.
4. Edit `/etc/caddy/Caddyfile` on the Pi (find the same line and replace).
5. Reload: `ssh TinyButMighty 'sudo systemctl reload caddy'`.
6. Update the saved credential on every device (phone, any laptop that
   mounted it).
7. Update the password-manager entry.

## Bootstrap (one-time)

Driven by `~/42/SpaceSuit/plans/2026_05_14-plan_upload_develle_fr.md`. Depends
on the share-file stack already being up (see `~/42/SpaceSuit/share_file/README.md`)
— this reuses the same Caddy install, NAT rule, and Cloudflare token.

## Gotchas

- **Browser to `/inbox/`** previously returned `405 Method Not Allowed`
  because `/inbox/` is the WebDAV endpoint and `file_server` doesn't
  speak PROPFIND/GET-as-listing. The Caddyfile now redirects GET/HEAD
  on `/inbox` and `/inbox/` to `/` so phones that guess the URL still
  land on the upload page. Real WebDAV verbs (PUT, PROPFIND, DELETE,
  MKCOL, etc.) still reach the webdav handler.

- **`apt upgrade` clobbers the custom Caddy binary.** Both the
  cloudflare DNS provider and the WebDAV handler are non-standard
  modules added via `caddy add-package`. When the cloudsmith Caddy
  package is upgraded, the binary at `/usr/bin/caddy` is replaced with
  the stock build and Caddy fails to start (the journal will show
  `module not registered: dns.providers.cloudflare`). `caddy` is now
  `apt-mark hold`-ed on the Pi to prevent this; when you intentionally
  want to upgrade Caddy, unhold, upgrade, then immediately re-run:

  ```bash
  ssh TinyButMighty 'sudo caddy add-package \
    github.com/caddy-dns/cloudflare \
    github.com/mholt/caddy-webdav && \
    sudo systemctl restart caddy'
  ```

  …and only then `apt-mark hold caddy` again.

- **Native phone WebDAV clients are flaky.** Material Files (Android)
  in particular failed to negotiate the connection during initial
  setup. The browser UI at `/` is the recommended phone flow because
  it relies only on standard HTTPS + basic auth + a PUT — no app
  quirks to debug.

## Caveats

- **Single user.** No per-recipient auth. The bcrypt-hashed password
  protects the inbox; anyone with the password can upload AND list AND
  delete everything in there.
- **No quotas.** A misbehaving phone client could fill the Pi disk.
  Check `df -h /srv` on the Pi periodically.
- **Bcrypt hash is no longer committed.** `Caddyfile.snippet` is
  gitignored because the repo is public; a weak/short password would be
  trivially crackable from the hash. The live `/etc/caddy/Caddyfile` on
  the Pi is the source of truth. To bootstrap a fresh Pi, regenerate the
  snippet from the live config (or from `caddy hash-password` plus the
  template in this README).
- **No auto-pull.** `pull-uploads` is manual on purpose — local machine
  decides when files come down. If this becomes annoying, wrap it in a
  systemd timer (e.g. 10-min interval).
- **Public IP exposed.** `upload` is a grey-cloud A record because Caddy
  needs direct 443 access for TLS — same model as `share.develle.fr`.

## Files in this directory

- `Caddyfile.snippet` — the `upload.develle.fr` block. **Gitignored**
  (contains bcrypt hash; repo is public). Local-only; the live
  `/etc/caddy/Caddyfile` on the Pi is the source of truth.
- `nginx-upload-redirect.conf` — port-80 nginx block that 301-redirects
  `upload.develle.fr` to HTTPS, so it doesn't fall through to pruna's
  server block (the implicit default on :80).
- `README.md` — this file.

The actual CLI lives at `~/42/SpaceSuit/bin/pull-uploads` (picked up via the
existing `$PATH_SETUP_DIR/bin` path-prepend in `.zshrc`).
