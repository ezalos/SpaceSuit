# Lessons

Infra-specific lessons (naming a host, domain, or repo of Louis's own) live in
the private `~/42/thestables/docs/lessons.md` instead — split out 2026-08-07
during the Setup/TheStables consolidation. This file keeps only the generic,
reusable-anywhere lessons.

## Allowlisting Cloudflare's IPs at the origin is NOT origin authentication

**2026-07-03** — After firewalling an origin to allow only Cloudflare's published
IP ranges on 80/443, I called it "safe to remove the origin's basic_auth." Louis
pushed back: what stops an attacker who has the origin IP? The gap I missed —
Cloudflare's IP ranges are SHARED across all customers, so an attacker can put
your origin IP behind THEIR own Cloudflare zone/Worker (with a spoofed Host) and
reach it from a Cloudflare IP your firewall trusts, bypassing YOUR Access.

**Rule:** IP-allowlisting all of Cloudflare ≠ origin auth. To actually seal a
Cloudflare-fronted origin: (a) use a Cloudflare Tunnel and make the origin port
reachable only via loopback/LAN (no inbound at all — best when tunneled), (b) use
Authenticated Origin Pulls (mTLS cert only your zone presents), or (c) a secret
header your zone injects and the origin verifies. Never drop an origin-level auth
control on the strength of an all-Cloudflare IP allowlist alone.

