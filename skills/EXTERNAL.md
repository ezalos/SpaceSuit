# External (third-party) skills

These skills live as untracked real directories in `~/.claude/skills/`, NOT in this
repo. They are recorded here so a fresh machine can reinstall them. See
`docs/plans/2026-07-07-skills-fanout-deploy-design.md` (sub-project 2) for the planned
installer that will read this manifest.

Owned skills, by contrast, live in this `skills/` directory and deploy automatically
via the single `skills` fan-out entry in `dotfiles/dotfiles.json` (drop a folder in,
run `python -m src_dotfiles deploy skills`).

| Skill(s) | Source | License / pin | Notes |
|---|---|---|---|
| `research`, `research-add-fields`, `research-add-items`, `research-deep`, `research-report` | https://github.com/Weizhena/Deep-Research-skills | MIT | Copied in; still contains the upstream author's hardcoded `/home/weizhena/…` path |
| `hegelian-dialectic-skill` | https://github.com/KyleAMathews/hegelian-dialectic-skill | commit `77d69b4` (2026-04-08) | Has its own `.git` |
| `concept-to-video` | https://www.claudepluginhub.com/skills/mathews-tom-armory/concept-to-video | — | From Claude Plugin Hub (author `mathews-tom-armory`) |
