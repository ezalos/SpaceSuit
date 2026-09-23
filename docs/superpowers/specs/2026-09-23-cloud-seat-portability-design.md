# Cloud seat portability: a SpaceSuit-only bootstrap for a headless remote seat

Date: 2026-09-23. Status: design, awaiting Louis's review. Companion specs: the seat
itself lives in the work tree's own docs; the modular ssh config lives in the private
infra repo's `docs/plans/`.

## 1. Goal

A brand-new headless Linux host ("the seat") reaches Louis's full working setup from
three sources only: this public repo, one private work-tree repo, and the vault. It never
clones the private infra repo (GroundControl). Along the way, drift between tracked and
live config on the existing devices is folded back in.

What "full working setup" means for the seat, in Louis's words: oh-my-zsh, tmux, p10k, the
Claude Code layer (skills, CLAUDE.md, settings, statusline, hooks), the `claude-usage`
family-account tool with its failover watcher, both deep-research engines, the vault
tooling, and a service registry that is not GroundControl's.

## 2. Facts this design rests on (measured 2026-09-23)

- `src_dotfiles deploy` is symlink-only. `only_devices: null` deploys everywhere; a list
  makes `database.load_all()` skip the entry on any device not listed. `variants` gives a
  per-device source. `skills` is a fanout entry: every child dir of `skills/` becomes
  `~/.claude/skills/<name>`. `deploy` raises `RuntimeError` on any dangling symlink under
  `~/.claude/skills/`.
- Registry entries whose `main` lives in GroundControl today: `claude_md` (+2 variants),
  `claude_settings` (+2 variants), `claude_usage`, `claude_usage_command`,
  `claude_usage_bin`, `nginx.conf`, `serve-slides.service`, `agents-dashboard.service`,
  `lock-session-on-login.service`, the ssh config entry.
- `claude-usage.js` requires `cost.js` and `prices.default.json` through `__dirname`, and
  Node resolves the deployed symlink to its realpath, so the tool runs off the checkout it
  is symlinked from. Only the checkout location changes in this design, not that property.
- The `deep-research-web` engine (GroundControl) imports `deep_research.charter` and
  `deep_research.verify` from this repo (`DEEP_RESEARCH_WEB_SPACESUIT`, default
  `~/42/SpaceSuit`). Its skill file `skills/deep-research-claude-web/SKILL.md` and its
  design spec are already public in this repo.
- A missing `@`-import in a CLAUDE.md is silently skipped, and a present one is loaded.
  Tested 2026-09-23 with `claude -p` in a throwaway project: run 1 with the import target
  absent answered from the base only; run 2 with the target present answered with the
  imported codeword. The official memory doc is silent on the missing case, so this is an
  observed behaviour, not a documented contract; the plan re-checks it after every Claude
  Code upgrade that touches memory loading.
- `.setup_env` is committed with a hardcoded `WHICH_COMPUTER` naming one device, sourced by
  `.zshrc` before hostname detection. `scripts/dotfiles-check.sh` still uses
  `SETUP_DIR="${HOME}/Setup"`, so the per-prompt dotfiles sync indicator is dead on every
  device that uses `~/42/SpaceSuit`.
- On the main workstation the live `~/.claude/settings.json` is a real file that differs
  from the tracked base in five keys (`autoMode`, `switchModelsOnFlag`,
  `extraKnownMarketplaces`, `model`, `statusLine`), and the live `~/.ssh/config` is a real
  file, not the tracked symlink. Both are drift; the live files are the truth.
- GroundControl's service registry tool (`monitoring/bin/service` 153 lines,
  `monitoring/lib/registry.py` 72 lines, `monitoring/tools/service-checks` 107 lines,
  tests in `monitoring/tests/test_registry.py`) depends only on PyYAML and hard-codes the
  two allowed host names in argparse `choices`.
- `Installs/bootstrap.sh` installs oh-my-zsh, p10k, three zsh plugins, rustup, rip2, nvim,
  direnv and gh without sudo. It does not install tmux (needs apt, and Ubuntu 22.04's 3.2a
  breaks the OSC-52 clipboard chain; `scripts/install-tmux.sh` builds 3.5a) and nothing in
  this repo installs `pass-cli` on Linux; the runbook in `plans/2026_06_29-proton_secret_manager.md`
  hardcodes the `linux.x86_64` selector.

## 3. Design

### 3.1 Three generic tools move from GroundControl into this repo

GroundControl's own rule for netwatch applies: generic, secret-free code belongs in the
public repo. All three qualify. Each move is `git mv` in one repo and `git rm` in the other,
never a copy, so nothing can go stale.

| Tool | From (GroundControl) | To (SpaceSuit) | Scrub before the first public commit |
|---|---|---|---|
| claude-usage | `dotfiles/claude-usage/` (js, cost.js, prices.default.json, tests, `commands/claude-usage.md`, `bin/claude-usage`) | `claude_usage/` | 2 device-name mentions in the js, the device list in the command doc |
| deep-research-web engine | `deep-research-web/` (package, `bin/`, `systemd/`, `install.sh`, `env.example`, tests) | `deep_research_web/` beside `deep_research/` | 3 device-name mentions in `tests/test_notify.py`; the screen-geometry comment becomes a config value |
| service registry | `monitoring/bin/service`, `monitoring/lib/registry.py`, `monitoring/tools/service-checks`, `monitoring/tests/test_registry.py` | `service_registry/` with `bin/service` and `bin/service-checks` | the argparse `choices` for `--host` |

Registry changes for the moves: the three `claude_usage*` entries keep their deploy paths
and get a repo-relative `main`; new entries `deep_research_web_bin` (`~/.local/bin/deep-research-web`)
and `service_registry_bin` (`~/.local/bin/service`, `~/.local/bin/service-checks`). The
deep-research-web systemd user units stay installed by its own `install.sh`, which now
lives here; `DEEP_RESEARCH_WEB_SPACESUIT` keeps its default and becomes redundant.

The service registry tool takes the registry file from `--registry <path>` or
`$SERVICE_REGISTRY`, refuses to run with neither, and accepts any lowercase-slug `--host`.
GroundControl keeps a two-line `monitoring/bin/service` wrapper that execs the SpaceSuit
tool with `--registry monitoring/services.yaml`, so its weekly sweep and charter change
nothing. The seat points `$SERVICE_REGISTRY` at a registry file in its work tree. One tool,
several registries. `service-checks` keeps selecting entries by `HOST_ID` or
`platform.node()`.

Louis's memory note that claude-usage "lives off the checkout" stays true and moves with
the file; the plan updates the note's path.

### 3.2 CLAUDE.md: a public base plus a private local file

`~/.claude/CLAUDE.md` becomes the tracked public base, `dotfiles/claude_md` in this repo,
and its last line is:

    @~/.claude/CLAUDE.local.md

`~/.claude/CLAUDE.local.md` is each device's private context. It is never in this repo. On
the existing devices it is the file that is their `claude_md` variant today, reached
through a new registry alias `claude_md_local` with per-device `variants` pointing into
GroundControl and `only_devices` listing those devices. The seat's local file is placed by
its own runbook from its work tree, outside this registry.

The base is today's private base with three lines moved to the local side or generalized:
the household-line measurement and its incident path, the note naming a machine whose
pulls depend on pushes landing, and the machine named in the headless-Chrome CDP example
(the rule stays, the example is generalized). The 2026-08-11 incident line under the email
guardrail stays in the base: its recipient detail was removed from the live file on
2026-09-23, and what remains is the rule's evidence, not personal information. The `claude_md` entry becomes global
(`only_devices: null`, no variants), so any new device gets the base by deploying.

Gate: Louis reads the base once before its first public commit. It carries his working
preferences and paths into private repos, which the registry already exposes, but the
decision to publish the prose is his.

### 3.3 settings.json: re-sync, and find out why it drifts

The tracked base `claude_settings` in GroundControl is re-synced from the live workstation
file (live is truth). It stays private: its permission lists name machines. The seat's
settings come from its work tree.

The live file being a real file rather than the symlink is probably Claude Code rewriting
`settings.json` atomically on `/model` or auto-mode toggles, which replaces a symlink with
a file. The plan verifies this with one toggle on a symlinked test file. If confirmed, a
symlinked settings entry cannot stay a symlink, and the drift check
(`scripts/dotfiles-check.sh`) grows a "settings diverged from tracked" line instead of the
deployer pretending. If not confirmed, the entry is re-deployed as the symlink it was.

### 3.4 Pre-registering a device from another machine

`src_dotfiles` infers only the current device. Pre-registering the seat from the
workstation, so the seat never has to commit to this public repo and holds no personal git
identity, needs a new CLI subcommand, per the add-dotfile rule that a missing operation is
built in `src_dotfiles/__main__.py` first:

    .venv/bin/python -m src_dotfiles add_device <identifier> <home_path> [--dotfiles-dir=<path>]

The identifier is `<hostname>.<user>` as `config.identifier` computes it. Then `extend_to`
for every gated entry the seat needs: `skills`, the three `claude_usage*`,
`deep_research_web_bin`, `service_registry_bin`, `claude-badge`, `gh_identity_router`,
`gh_contexts_bootstrap`, `git_pre_commit_guard`, `git_identity_scrub`. Not extended, on
purpose: every X-session, nginx, streaming, dashboard, netwatch and lock-screen entry, and
the ssh config entry (the seat's ssh config is three lines written by its runbook).

Because `deploy` calls `save_all()`, running it on the seat must produce an empty registry
diff. The runbook checks `git -C ~/42/SpaceSuit status --porcelain` is empty after deploy;
a non-empty result is a registry bug to fix here, never a commit from the seat.

### 3.5 Small repo fixes the seat trips over

- `.setup_env` stops naming a device: `WHICH_COMPUTER` comes from `~/.zshrc.local` when
  set, else from the existing hostname detector, which grows a generic `cloud` branch for
  hostnames it does not know.
- `scripts/dotfiles-check.sh`: `SETUP_DIR` becomes `~/42/SpaceSuit`, restoring the sync
  indicator on every device.
- `Installs/install-pass-cli.sh`: arch-aware selector (`linux.x86_64`, `linux.aarch64`,
  `macos.aarch64`) over the vendor `versions.json`, sha256-verified, into `~/.local/bin`.
  `bootstrap.sh` calls it. The runbook's hardcoded selector note is retired.
- `Installs/bootstrap.sh` prints the apt line for tmux, zsh, jq, socat and the build deps
  as one copy-paste-safe line, so the sudo step on a fresh box is one paste.
- `skills/EXTERNAL.md` stays a manifest. The seat installs the listed externals by hand
  from it; building the installer is out of scope.

### 3.6 What each device ends up with

| Entry | Workstation | Macs | Pi | Seat |
|---|---|---|---|---|
| shell, tmux, editors, statusline, hooks, skills | as today | as today | as today | yes |
| `claude_md` (public base) | yes | yes | yes | yes |
| `claude_md_local` (private, per device) | GroundControl variant | GroundControl variant | GroundControl variant | placed by runbook |
| `claude_settings` | GroundControl (re-synced) | GroundControl variant | GroundControl variant | placed by runbook |
| `claude_usage*` | yes | yes (todo says never deployed; the plan does it) | yes | yes |
| `deep_research_web_bin` | yes | no | no | yes |
| `service_registry_bin` | yes (GroundControl wrapper) | no | yes | yes (work-tree registry) |
| gh router, guards | yes | yes | no | yes |
| X, nginx, netwatch, streaming, dashboards | as today | no | as today | no |

## 4. Error handling

- A move that leaves a dangling `~/.claude/skills/` link makes `deploy` raise; the plan
  runs `deploy skills` on the workstation right after each move and fixes the link before
  committing.
- `claude-usage` reads and writes the live credential store. Every test of the moved tool
  runs with `CLAUDE_CONFIG_DIR` and `HOME` pointed at a temp dir, never against
  `~/.claude`, and no `switch` is exercised outside its existing test suite.
- The registry tool's wrapper in GroundControl fails loudly if the SpaceSuit checkout or
  the registry file is missing, with the path it looked for.

## 5. Testing

- `tests/test_dotfiles.py`: `add_device` round-trip, `extend_to` onto a pre-registered
  device, `deploy` on a device with no `deploy` entry for a global alias (translation
  path), and "deploy leaves the registry unchanged when the device is pre-registered".
- claude-usage's own `node --test` suite passes from `claude_usage/`.
- deep-research-web's pytest suite passes from `deep_research_web/`; the notify tests use
  a generic hostname.
- `service_registry` tests pass with `--registry` pointing at a temp file; a test asserts
  the tool refuses without a registry path and accepts a new host slug.
- On the workstation after everything: `deploy` reports every entry "already correct",
  `claude -p` in a throwaway project sees both the base rule and the local file's rule,
  `claude-usage doctor` passes, `deep-research-web status` runs, `service list` works
  through the GroundControl wrapper, `git status` clean in both repos.

## 6. Out of scope

The seat itself (its VM, disk, access, runbook, registry file): the work tree's spec. The
modular ssh config: GroundControl's spec. The agents-dashboard push, which is not
multi-host safe. The EXTERNAL.md installer. Any change to how grants or browser profiles
are obtained: both stay one-per-device by design.

## 7. Sequence

1. `add_device` subcommand and its tests.
2. Service registry move plus the GroundControl wrapper; its tests.
3. claude-usage move; scrub; tests; registry `main` paths; workstation re-deploy.
4. deep-research-web move; scrub; tests; new registry entry; workstation re-deploy.
5. CLAUDE.md split: base file, `claude_md_local` alias, Louis's read of the base, deploy
   on the workstation, `claude -p` check.
6. settings re-sync and the symlink-survival probe.
7. `.setup_env`, `dotfiles-check.sh`, `install-pass-cli.sh`, bootstrap apt line.
8. Pre-register the seat and `extend_to` its entries. Commit, push.
