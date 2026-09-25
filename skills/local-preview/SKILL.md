---
name: local-preview
description: Use when a choice needs Louis's EYES while he is remote — mockups, frames, visual comparisons, "which layout looks better". Publishes a throwaway page at https://local.develle.fr/<slug>/ (OAuth-gated at the Cloudflare edge) and hands him the URL. On a machine with ~/Publish/.relay, publishes by writing into ~/Publish/ instead (pages and dev servers). Do NOT rely on panel attachments for visual decisions; he works over SSH and may only have a terminal.
---

# local-preview

Louis is remote-first: when a decision needs his eyes (mockups, frames,
comparisons), publish a throwaway page and give him the URL. The subdomain is
Access-gated (Google SSO, ezalos@gmail.com only) and serves `/srv/local/` on
TinyButMighty via Caddy (`file_server browse`; drop-in
`/etc/caddy/local-develle.caddy`).

## First: does a relay carry this machine's pages?

`test -e ~/Publish/.relay`. If it exists, this machine cannot reach the web
host itself (by design: never try ssh, a tailnet name or an ACL change). A
broker elsewhere publishes whatever lands in `~/Publish/` within seconds:

1. Write the self-contained page into `~/Publish/<slug>/` (same slug rule as
   below).
2. Wait for `~/Publish/.status/<slug>` (a few seconds) and give Louis the URL
   it holds, never a guessed one. A line starting `error:` is the reason it
   was refused.
3. Tear down by deleting `~/Publish/<slug>/`; the broker moves it to the trash.

A dev server is published the same way: `echo <port> > ~/Publish/.links/<name>`,
URL in `~/Publish/.status/<name>`, delete the file to unpublish.

Otherwise, follow the steps below.

## Per use

1. Pick a unique slug (`<topic>-<4 rand chars>`); build a tiny self-contained
   `index.html` plus the images (relative src, no external assets).
2. `ssh TinyButMighty_ts 'mkdir -p /srv/local/<slug>'` then `scp` the files
   (ezalos is in the `caddy` group; the dir is group-writable, no sudo).
3. Verify `curl -sI https://local.develle.fr/<slug>/` returns a 302 to
   `develle-one.cloudflareaccess.com` (the gate working), then give Louis the
   URL.
4. **Tear down as soon as Louis has answered** — move the slug out of the web
   root: `ssh TinyButMighty_ts 'mkdir -p /srv/local-trash && mv /srv/local/<slug> /srv/local-trash/'`
   (trash sits outside the served tree; empty it occasionally). Slugs are
   per-question, not per-project — concurrent sessions just use different
   slugs.

## If the subdomain is ever gone

Serving path: proxied A record (home origin, IP hidden by the orange cloud) in
`~/42/GroundControl/network/domains/cloudflare-dns/dns.json` -> home NAT :80 ->
Pi nginx (`/etc/nginx/conf.d/local.conf`, root `/srv/local`, autoindex) — the
same pattern as diagrams.develle.fr. The Access app is `develle-access protect
local` in `~/42/GroundControl/network/domains`. Restore the dns.json record +
`develle-dns sync`, and the nginx conf on the Pi.

## Observability

- WARNING when the 302 gate check fails (page may be publicly reachable —
  stop and fix the Access app before sharing the URL).
- INFO on publish and on teardown: `local-preview: <slug> up|torn down`.
