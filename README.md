# SpaceSuit

Put it on, and any machine becomes habitable.

Dotfiles, agent skills, install scripts and small generic tools — everything I
carry from machine to machine, packaged so a fresh OS becomes a familiar one in
minutes. Public on purpose: clone it, take what fits, leave what doesn't.

## What's inside

| dir | what it is |
|---|---|
| `dotfiles/` | shell, editor, terminal, git config — deployed as symlinks by the registry below |
| `src_dotfiles/` | the deployer: a per-device registry (`dotfiles/dotfiles.json`) that fans files out as symlinks, tracks variants per machine, and never needs hand-editing (`python -m src_dotfiles --help`) |
| `skills/` | Claude Code skills — self-contained agent workflows (`SKILL.md` + tooling per dir) |
| `Installs/` | install helpers (mostly vestigial; see Installs/) |
| `bin/` | small CLI wrappers added to PATH |
| `netwatch/` | a home-network black-box recorder: link/gateway/WAN probes, outage forensics, publishable dashboard |
| `scripts/`, `tests/` | shared helpers and the test suite (`uv run pytest`) |

## Quickstart on a fresh machine

```bash
git clone https://github.com/ezalos/SpaceSuit ~/42/SpaceSuit
cd ~/42/SpaceSuit && uv sync
uv run python -m src_dotfiles deploy        # symlinks the dotfiles for this device
```

Running `deploy` unmodified on a machine that isn't mine will overwrite your
shell/editor/claude configs with mine — fork the repo and edit
`dotfiles/dotfiles.json` first.

Machine-specific shell bits load from `~/.zshrc.local` (not in this repo — create
yours). Tool targets (dashboards, upload endpoints) read from `~/.config/<tool>/env`.

## What is deliberately NOT here

Anything naming a specific machine, network, address or service of mine lives in
a private infra repo. The rule that keeps this one shareable: **if it names a
machine, a place, or an address — it doesn't belong here.** A commit-time scrub
hook enforces it.

## History note

This repo's history starts fresh in 2026. Six years of prior evolution live on
privately — the public tree carries only what a stranger can safely see and
usefully reuse.
