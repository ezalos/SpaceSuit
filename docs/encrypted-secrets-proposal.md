# Encrypted Secrets in a Public Dotfiles Repo — Design Proposal

**Author:** Louis (with Claude)
**Date:** 2026-02-16
**Status:** Draft / Proposal
**Scope:** Security tiers for managing sensitive files in the `Setup` dotfiles repository

---

## Problem Statement

This repository is a public dotfiles manager. Sensitive files — API tokens, credentials, shell secrets — currently rely on `.gitignore` to stay out of version control. This means:

- No backup of secrets (if the disk dies, they're gone)
- No synchronization of secrets across machines
- No audit trail of changes to secrets
- Constant risk of accidental `git add -A` exposing them

We need a layered approach to secret management that lets us store encrypted secrets in the public repo while keeping the workflow simple for day-to-day use.

---

## Security Tiers Overview

| Tier | Approach | Backup | Sync | Complexity | Best For |
|------|----------|--------|------|------------|----------|
| 1 | `.gitignore` | None | None | Zero | Non-critical local-only config |
| 2 | Symmetric encryption (`git-crypt`) | Yes | Yes | Low | API tokens, `.secrets.sh` |
| 3 | GPG asymmetric encryption | Yes | Yes | Medium | SSH passphrases, important tokens |
| 4 | External secret manager | Yes | Yes | Medium | All secrets (if already using one) |
| 5 | Hardware-backed (YubiKey) | Yes | Yes | High | Production creds, critical SSH keys |

---

## Tier 1 — `.gitignore` (Current Approach)

### Description

Files containing secrets are listed in `.gitignore` and never enter version control. This is what we do today: `.secrets.sh` and `.envrc` are gitignored.

### How It Works

```gitignore
# In .gitignore
.secrets.sh
.envrc
*.credentials.json
```

Secrets live only on the local filesystem. Each machine has its own copy, manually created and maintained.

### Pros

- Zero tooling required — nothing to install, configure, or learn
- No cryptographic complexity
- Impossible to leak via git (as long as `.gitignore` is correct)

### Cons

- **No backup whatsoever** — disk failure means total loss
- **No sync across machines** — must manually recreate secrets on each device
- **No change history** — no way to see what a secret was last week
- **Fragile** — a careless `git add -A` or a misconfigured `.gitignore` can expose everything
- **No discoverability** — new machine setup requires remembering which secrets exist

### Risk Profile

Low attack surface (secrets never touch git), but high operational risk (no recovery, no sync).

### Best Suited For

- Truly ephemeral local config that you can regenerate trivially
- Files you explicitly don't want backed up anywhere

---

## Tier 2 — Symmetric Encryption (`git-crypt`)

### Description

Use [`git-crypt`](https://github.com/AGWA/git-crypt) to transparently encrypt specific files in the repository. Files are encrypted at rest in git (so they appear as binary blobs on GitHub) but are automatically decrypted on checkout when the machine has the symmetric key.

### Tool Recommendation

**`git-crypt`** — mature, widely used, integrates directly with git's smudge/clean filter system.

### How It Works

1. Initialize git-crypt in the repo:

```bash
git-crypt init
```

This generates a symmetric key stored at `.git/git-crypt/keys/default`.

2. Define which files to encrypt via `.gitattributes`:

```gitattributes
.secrets.sh filter=git-crypt diff=git-crypt
dotfiles/credentials/** filter=git-crypt diff=git-crypt
*.secret filter=git-crypt diff=git-crypt
```

3. On a new machine, unlock the repo with the exported key:

```bash
# Export the key from an existing machine (do this once, store safely):
git-crypt export-key /tmp/dotfiles-git-crypt-key

# On the new machine:
git-crypt unlock /path/to/dotfiles-git-crypt-key
```

4. From this point on, `git add`, `git commit`, `git pull` all work normally. Encryption/decryption is transparent.

### Example Workflow

```bash
# Edit secrets normally — they're decrypted in your working tree
vim .secrets.sh

# Commit as usual — git-crypt encrypts before storing
git add .secrets.sh
git commit -m "update API tokens"

# On GitHub, .secrets.sh appears as encrypted binary
# On your machine, it's plaintext
```

### Pros

- **Secrets are backed up and synced** via normal git push/pull
- **Transparent workflow** — no special commands for daily use
- **Change history preserved** (though diffs of encrypted files are opaque on GitHub)
- **Battle-tested tool** with wide adoption

### Cons

- **Single shared symmetric key** — anyone with the key can decrypt everything
- **Key distribution problem** — you need a secure way to get the key to each new machine
- **All-or-nothing per keyring** — can't give one machine access to some files but not others (without multiple keyrings)
- **Binary diffs on GitHub** — you lose the ability to review secret changes in PRs (not relevant for solo use)

### Risk Profile

Moderate. The security is exactly as strong as the protection of the symmetric key file. If someone gains access to the key, they can decrypt all Tier 2 secrets from the public repo.

### Best Suited For

- `.secrets.sh` (shell environment variables, API tokens)
- Non-critical credentials that you need synced across machines
- Config files with embedded tokens (e.g., `.npmrc` with registry tokens)

---

## Tier 3 — GPG Asymmetric Encryption (`git-crypt` with GPG Keys)

### Description

Same transparent encryption as Tier 2, but instead of a single shared symmetric key, each device has its own GPG key pair. The repo is configured to trust specific GPG keys, and `git-crypt` encrypts to all trusted keys. This gives per-device access control and key revocation.

### Tool Recommendation

**`git-crypt`** (same tool as Tier 2, different key mode) + **`gpg`** (GnuPG).

### How It Works

1. Generate a GPG key on each device (if you don't have one):

```bash
gpg --full-generate-key
# Choose: RSA and RSA, 4096 bits, no expiry (or set expiry as desired)
# Use a consistent email across devices for identification
```

2. Initialize git-crypt with GPG mode:

```bash
git-crypt init
```

3. Authorize a GPG key to decrypt the repo:

```bash
# Add a collaborator (yourself on another device) by GPG key ID
git-crypt add-gpg-user --trusted USER_GPG_KEY_ID
```

This creates an encrypted copy of the repo key for that GPG identity and commits it to `.git-crypt/keys/`.

4. On the new device, just unlock — GPG handles the rest:

```bash
# No key file needed — git-crypt uses your local GPG key
git-crypt unlock
```

### Example: Adding a New Device

```bash
# On the new device, generate and export the public key:
gpg --full-generate-key
gpg --armor --export louis@newdevice > /tmp/newdevice.pub

# Transfer the public key to an already-authorized device (scp, email, etc.)
# On the authorized device:
gpg --import /tmp/newdevice.pub
git-crypt add-gpg-user --trusted NEW_KEY_ID
git add .git-crypt/
git commit -m "authorize new device: newdevice"
git push
```

### Revoking Access

To revoke a device's access, you need to:

1. Remove the device's key entry from `.git-crypt/keys/`
2. Rotate the underlying symmetric key (git-crypt doesn't natively support this — you'd need to re-init and re-encrypt)

This is the main weakness: revocation is manual and disruptive.

### Pros

- **No shared secret to distribute** — each device uses its own GPG key
- **Per-device access control** — you choose which devices can decrypt
- **Key revocation possible** (though cumbersome)
- **Same transparent workflow** as Tier 2 once unlocked
- **GPG key can be password-protected** for additional security

### Cons

- **GPG key management is genuinely complex** — key generation, trust models, keyservers, expiry
- **Must authorize each new device** from an already-authorized device (chicken-and-egg on first setup)
- **Revocation is painful** — requires re-keying if you truly want to lock out a compromised device
- **GPG tooling has poor UX** — confusing commands, opaque error messages

### Risk Profile

Good. Compromise of one device's GPG key exposes secrets, but you have per-device granularity. The main risk is GPG's complexity leading to misconfiguration.

### Best Suited For

- SSH key passphrases
- Important API tokens (cloud provider keys, CI/CD tokens)
- Credentials you want to be able to revoke per-device

---

## Tier 4 — External Secret Manager (`pass`, 1Password CLI, Bitwarden CLI)

### Description

Secrets are not stored in the dotfiles repo at all. Instead, they live in a dedicated password manager, and your dotfiles scripts reference them at runtime. The `.secrets.sh` file becomes a thin shim that calls the secret manager to fetch values.

### Tool Recommendations

| Tool | Notes |
|------|-------|
| [`pass`](https://www.passwordstore.org/) | Unix philosophy, GPG-encrypted flat files in a git repo. Free, open source. |
| [`1password-cli` (`op`)](https://developer.1password.com/docs/cli/) | Commercial. Excellent UX, biometric unlock, team sharing. |
| [`bitwarden-cli` (`bw`)](https://bitwarden.com/help/cli/) | Open source server, good CLI. Free tier available. |
| [`gopass`](https://github.com/gopasspw/gopass) | Enhanced `pass` with better team/multi-store support. |

**Recommendation for solo use:** `pass` (simplest, git-native, GPG-based) or `1password-cli` (if you already use 1Password).

### How It Works with `pass`

1. Initialize the password store:

```bash
# Install pass
sudo apt install pass  # or brew install pass

# Initialize with your GPG key
pass init YOUR_GPG_KEY_ID

# The store lives at ~/.password-store/ (a git repo)
pass git init
```

2. Store secrets:

```bash
pass insert dotfiles/openai-api-key
# Enter the key at the prompt

pass insert dotfiles/gateway-token
pass insert dotfiles/github-token
```

3. Rewrite `.secrets.sh` to fetch from pass:

```bash
# .secrets.sh — fetches secrets from pass at shell startup
export OPENAI_API_KEY="$(pass show dotfiles/openai-api-key)"
export GATEWAY_TOKEN="$(pass show dotfiles/gateway-token)"
export GITHUB_TOKEN="$(pass show dotfiles/github-token)"
```

4. Sync across machines:

```bash
# pass uses git under the hood
pass git push
pass git pull
```

### How It Works with 1Password CLI

```bash
# Sign in
eval $(op signin)

# Store a secret
op item create --category=login --title="OpenAI API Key" \
  --vault="Dotfiles" password="sk-..."

# Reference in .secrets.sh
export OPENAI_API_KEY="$(op read 'op://Dotfiles/OpenAI API Key/password')"
```

### Pros

- **Industry-standard security** — password managers are purpose-built for this
- **Audit trail** — who accessed what and when
- **Works across all machines** — no repo-specific setup needed
- **Secrets never touch the dotfiles repo** — zero leak risk from git
- **Can share secrets with team members** (1Password, Bitwarden)
- **Biometric unlock available** (1Password, Bitwarden)

### Cons

- **External dependency** — need the password manager installed and configured on every device
- **Startup latency** — each `pass show` or `op read` call adds time to shell init
- **Offline access can be limited** (1Password, Bitwarden cloud-based)
- **`pass` still requires GPG** — so you inherit GPG complexity anyway
- **Shell startup can fail** if the secret manager is locked or unavailable

### Mitigating Startup Latency

Cache secrets in a session-local file or use lazy loading:

```bash
# Lazy-load secrets on first use instead of shell startup
openai_api_key() {
  if [ -z "$OPENAI_API_KEY" ]; then
    export OPENAI_API_KEY="$(pass show dotfiles/openai-api-key)"
  fi
  echo "$OPENAI_API_KEY"
}
```

### Risk Profile

Strong. Secrets are protected by the password manager's encryption, access controls, and (optionally) 2FA. The risk shifts to the security of the password manager itself, which is generally well-audited.

### Best Suited For

- All secrets, especially if you already use a password manager
- Secrets shared across contexts (not just dotfiles — also scripts, CI, etc.)
- Environments where you want a single source of truth for credentials

---

## Tier 5 — Hardware-Backed (YubiKey + GPG or `age` + Hardware Token)

### Description

The encryption private key is stored on a hardware security module (HSM) such as a YubiKey. The key never leaves the hardware device — all cryptographic operations happen on-chip. This protects against key extraction even if the host machine is fully compromised.

### Tool Recommendations

| Approach | Tools |
|----------|-------|
| YubiKey + GPG | `gpg` + `ykman` (YubiKey Manager) |
| YubiKey + `age` | [`age`](https://github.com/FiloSottile/age) + [`age-plugin-yubikey`](https://github.com/str4d/age-plugin-yubikey) |
| FIDO2/PIV | YubiKey PIV mode for SSH keys, certificate-based auth |

**Recommendation:** YubiKey 5 series + GPG for maximum ecosystem compatibility. The `age` route is simpler if you don't need GPG for anything else.

### How It Works (YubiKey + GPG)

1. Generate GPG keys directly on the YubiKey (or move existing keys to it):

```bash
# Generate on-card (keys never exist on disk)
gpg --card-edit
> admin
> generate

# Or move existing subkeys to the card
gpg --edit-key YOUR_KEY_ID
> keytocard
```

2. Use with git-crypt (Tier 3 workflow, but now the GPG key is on hardware):

```bash
# Unlock requires physical YubiKey + PIN
git-crypt unlock
# GPG prompts for YubiKey PIN, performs decryption on-chip
```

3. Use with `pass` (Tier 4 workflow, hardware-backed):

```bash
# pass uses GPG, which uses the YubiKey transparently
pass show dotfiles/openai-api-key
# Requires YubiKey inserted + PIN
```

### How It Works (`age` + YubiKey)

```bash
# Install age and the YubiKey plugin
brew install age
brew install age-plugin-yubikey  # or cargo install age-plugin-yubikey

# Generate an identity tied to the YubiKey
age-plugin-yubikey --generate

# Encrypt a file
age -r age1yubikey1... -o secrets.age secrets.txt

# Decrypt (requires YubiKey touch)
age -d -i age-yubikey-identity.txt secrets.age > secrets.txt
```

### Pros

- **Highest security tier** — private keys physically cannot be extracted from hardware
- **Tamper-resistant** — YubiKey detects and resists physical attacks
- **Touch confirmation** — optional requirement for physical touch on each crypto operation
- **PIN protection** — brute-force lockout after failed attempts
- **Works with existing Tier 3 and Tier 4 workflows** — drop-in upgrade

### Cons

- **Requires physical hardware** (~$50-$75 per YubiKey)
- **Need a backup YubiKey** — if you lose the only key, you lose access (buy two)
- **Complex initial setup** — GPG + smartcard configuration has many pitfalls
- **Physical presence required** — can't decrypt on a remote server without the key plugged in
- **Touch requirement adds friction** to batch operations

### Risk Profile

Excellent. Even full compromise of the host machine cannot extract the private key. The main risks are physical loss of the YubiKey (mitigated by backup keys) and supply-chain attacks on the hardware itself (extremely unlikely for YubiKey).

### Best Suited For

- Production infrastructure credentials (cloud provider root keys, Kubernetes admin certs)
- SSH keys to critical servers
- GPG signing keys for software releases
- Anything where key compromise would be catastrophic

---

## Comparison Matrix

| Concern | Tier 1 | Tier 2 | Tier 3 | Tier 4 | Tier 5 |
|---------|--------|--------|--------|--------|--------|
| Secrets backed up | No | Yes | Yes | Yes | Yes |
| Secrets synced across machines | No | Yes | Yes | Yes | Yes |
| Survives disk failure | No | Yes | Yes | Yes | Yes |
| Transparent git workflow | N/A | Yes | Yes | No | Partial |
| Per-device access control | N/A | No | Yes | Yes | Yes |
| Key revocation | N/A | No | Partial | Yes | Yes |
| Offline access | Yes | Yes | Yes | Partial | Yes |
| Setup complexity | None | Low | Medium | Medium | High |
| Ongoing friction | None | None | Low | Low | Low-Medium |
| Resists host compromise | No | No | No | Partial | Yes |

---

## Recommended Migration Path

The tiers are not mutually exclusive. You can (and should) use different tiers for different classes of secrets. Here is the recommended migration path:

### Phase 1: Start with Tier 2 (`git-crypt` symmetric)

This is the highest-impact, lowest-effort change. It immediately solves the two biggest problems: no backup and no sync.

```bash
# One-time setup (5 minutes)
sudo apt install git-crypt  # or brew install git-crypt
cd ~/Setup
git-crypt init
```

Add a `.gitattributes` file to encrypt sensitive files:

```gitattributes
.secrets.sh filter=git-crypt diff=git-crypt
*.secret filter=git-crypt diff=git-crypt
```

Export the key and store it somewhere safe (another machine, USB drive, password manager):

```bash
git-crypt export-key ~/dotfiles-key.bin
```

Remove `.secrets.sh` from `.gitignore`, add and commit it. It's now encrypted in git, decrypted locally.

### Phase 2: Upgrade to Tier 3 (GPG keys) when you have multiple devices

When the symmetric key distribution becomes annoying, switch to GPG-based `git-crypt`. Each device gets its own key, and you authorize them individually.

### Phase 3: Adopt Tier 4 (secret manager) for critical credentials

If you start using `pass` or 1Password for personal password management (or already do), migrate high-value secrets there. Keep Tier 2/3 for convenience secrets that benefit from the transparent git workflow.

### Phase 4: Add Tier 5 (hardware) if your threat model demands it

If you manage production infrastructure or sign software releases, invest in a YubiKey pair and move those keys to hardware. This is an upgrade to your GPG setup, not a replacement — it slots in underneath Tier 3 and Tier 4.

### What Stays at Tier 1

Some files should remain gitignored and never enter the repo, even encrypted:

- `.envrc` files generated by `direnv` (ephemeral, project-specific)
- Temporary session tokens
- Anything you can regenerate in under a minute

---

## Open Questions

- **Which secrets currently exist across Louis's machines?** An inventory would help decide which tier each secret belongs to.
- **Is Louis already using a password manager?** If so, Tier 4 might be the natural first step instead of Tier 2.
- **How many machines need access?** If it's just 2-3, Tier 2 is fine. At 5+, Tier 3 or 4 starts to pay off.
- **Are there any secrets that warrant Tier 5 today?** If not, defer hardware investment.
