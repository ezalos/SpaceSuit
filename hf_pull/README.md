# hf-pull — rate-capped Hugging Face downloads

`hf download` opens eight connections and pulls at whatever the line gives. On a
shared residential link that pins the NIC at line rate and the gateway stops
answering for everyone in the house within seconds. This tool pulls one file at
a time through `curl --limit-rate`, verifies every blob, and writes the official
hub cache layout, so `from_pretrained("org/repo")` works afterwards unchanged.

```
hf-pull Qwen/Qwen-Image-Edit-2511                   # 25 MB/s, sequential
hf-pull black-forest-labs/FLUX.1-dev --exclude 'flux1-dev.safetensors' --exclude 'ae.safetensors'
hf-pull some/dataset --type dataset --rate 10M --dry-run
```

- Default cap `25M` (curl syntax), override with `--rate` or `HF_PULL_RATE`.
- Resumable: partial blobs are kept as `<etag>.hfpull-part` and `curl -C -` continues them.
- Verified: sha256 for LFS files, git blob sha1 for the rest, compared to the Hub's etag before the blob is accepted.
- Token: `$HF_TOKEN`, else `~/.cache/huggingface/token`; passed to curl through a 0600 config file, never argv, and dropped on the redirect to the CDN.
- Stdlib + curl. Tests: `python3 hf_pull/test_hf_pull.py`.
