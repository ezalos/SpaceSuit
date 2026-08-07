# Proton Pass secret manager setup

Plan: ../plans/2026_06_29-proton_secret_manager.md
Architecture: Proton-first, nothing committed. **Per-context** read-only agent tokens
(one per vault, isolated) + direnv. Secrets stored as Proton "API Credential" items
(`Secret` field = value, `API Key` field = env var name).

## Done + proven

- [x] pass-cli 2.2.1 installed (SHA256-verified); per-context `proton-agent` wrapper
      (isolated session + auto-login) in ~/Setup/bin
- [x] `use proton <context>` direnv helper (~/.config/direnv/direnvrc); isolation gate
      verified (un-minted context refuses cleanly)
- [x] Learned Louis's API-credential format; `/Secret` injection proven end-to-end
- [x] `proton-envrc <context>` generator (~/Setup/bin): reads each item's `API Key`
      (var name) -> emits `export NAME='pass://share/id/Secret'` + `use proton`
- [x] Full flow proven: proton-envrc general > .envrc -> direnv load -> proton-agent run
      resolves all secrets (masked)

## Remaining

- [ ] Louis: mint per-context tokens for money/alakazam/anon (the `mk` script) so
      those vaults are usable; then `proton-envrc <ctx>` works identically
- [ ] Louis: delete the old broad "TheBeast Agent" token after scoped ones exist
- [ ] Register `bin/proton-agent`, `bin/proton-envrc`, the direnvrc in dotfiles (add-dotfile)
- [ ] Write the secrets section in user-level ~/.claude/CLAUDE.md
- [ ] Roll out: `proton-envrc <ctx> >> .envrc` in real projects; drop duplicated plaintext keys
- [ ] Commit the whole thing (still uncommitted, pending review)

## Notes / deferred

- Flag 2 (per-item power): `Railway Account Token`, DNS/Tunnel-edit Cloudflare token are
  account-level; decide if an agent should hold those vs project-scoped creds
- Data hygiene: `GOOGLE_OAUTH_CLIENT_SECRET` item has a trailing space in its `API Key`
  field (generator trims it, but worth cleaning in the app)
- SSH keys -> human-only vault; TinyButMighty -> own per-host tokens; Public IP -> not a vault problem
