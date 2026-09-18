# hf-pull — rate-capped Hugging Face downloads

`hf download` opens eight connections and pulls at whatever the line gives. On a
shared residential link that pins the NIC at line rate and the gateway stops
answering for everyone in the house within seconds. This tool pulls one file at
a time through `curl --limit-rate`, verifies every blob, and writes the official
hub cache layout, so `from_pretrained("org/repo")` works afterwards unchanged.

```
hf-pull Qwen/Qwen-Image-Edit-2511                   # 50 MB/s, sequential
hf-pull black-forest-labs/FLUX.1-dev --exclude 'flux1-dev.safetensors' --exclude 'ae.safetensors'
hf-pull some/dataset --type dataset --rate 10M --dry-run
hf-pull --window                                    # is the line asleep right now?
```

- Default cap `50M` (curl syntax), override with `--rate` or `HF_PULL_RATE`.
- Resumable: partial blobs are kept as `<etag>.hfpull-part` and `curl -C -` continues them.
- Verified: sha256 for LFS files, git blob sha1 for the rest, compared to the Hub's etag before the blob is accepted.
- Token: `$HF_TOKEN`, else `~/.cache/huggingface/token`; passed to curl through a 0600 config file, never argv, and dropped on the redirect to the CDN.
- Stdlib + curl (`zoneinfo` included). Tests: `python3 hf_pull/test_hf_pull.py`.

## The night window

A pull over `HF_PULL_NIGHT_GB` (default 40) waits until the people who share the
line are asleep — **01:00–07:00 in the LINE's timezone, which is deliberately not
this machine's**. Reading `date` to decide gives the opposite answer whenever the
two differ, so the tool answers instead:

```
$ hf-pull --window
  at the line (Europe/Paris):  Fri 2026-09-18 02:35 CEST
  this machine:                Thu 2026-09-17 17:35 PDT
  window 01:00-07:00 is OPEN, closes in 4 h 24 m
```

Outside the window an over-threshold pull **refuses** (exit 3) and prints the
`systemd-run` line that schedules it, with the zone written out explicitly.
`--now` overrides when a human said now.

Configure the zone in `~/.config/hf-pull/env` (`KEY=VALUE`, same keys as the
environment, which wins). It is a file and not just an env var because the
scheduled systemd unit this tool hands you never sees your shell profile:

```
HF_PULL_LINE_TZ=Europe/Paris
```

No zone is hardcoded — this repo is public, and a timezone names a place. With
none configured the tool warns loudly and pulls anyway rather than blocking.
