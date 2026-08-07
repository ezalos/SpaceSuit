# Claude resume table for trestore

Date: 2026-08-03
Status: shipped 2026-08-03 (commits 8e02177, ecd14d1; test-isolation prerequisite
in 243096c). 76 tests. Implemented as the stdlib-only `claude_resume/` package
rather than the `scripts/claude-resume.sh` this doc names, because the assignment
algorithm needs associative arrays and macOS ships bash 3.2. See the plan doc's
"Deviation" section.

Two things were added during implementation that this design did not anticipate:

- **Lineage vs stranger tiers.** The first cut ranked candidates by mtime alone.
  Prototyped against the live save it displaced five real panes with 27-to-38
  line `/security-review` sessions. See "Candidate set" and "Assignment
  invariant" below, both since rewritten.
- **Busy-pane guard.** `cresume` can run at any time, not only after a restore.
  Sending into a pane that already has Claude up types the resume command in as
  a chat message. Such panes are now detected via their tty and skipped, with
  `--force` to override.

The bug this design predicts was observed live on the day it was written: a tmux
crash restore (using the old code) scrambled 17 of 25 panes and let a 49-line
`/security-review` session take a pane while a 1.4MB real conversation was left
unattached. Nothing was lost; the mapping was repaired by hand.

## Problem

After `trestore` recreates the tmux topology, it walks every pane that had Claude
Code running and asks a question per pane (`tmux-restore.sh:272-378`). On the
current live save that is **26 sequential prompts**. Each one identifies the
candidate conversations by an 8-character hex prefix and nothing else, so there is
no way to tell what you are choosing without leaving the prompt and going to read
the session files.

Two things make it worse than tedious.

### The "latest" option is usually wrong

For each pane the prompt offers `saved` (the id recorded in the snapshot) against
`latest` (the newest `*.jsonl` in that pane's project dir). `find_recent_sessions`
walks panes in `state.tsv` order and greedily hands out conversations by mtime,
excluding ids already assigned. It never checks whether a conversation actually
belonged to the pane it is being offered to.

Measured on the live save (26 Claude panes, 11 project dirs):

```
alfred  w0   saved=c1da1164  latest=0a49c821
alfred  w1   saved=0a49c821  latest=c1da1164   <- swapped
social  w0   saved=709a8ea6  latest=1309b680
social  w1   saved=1309b680  latest=709a8ea6   <- swapped
```

15 of 26 panes report `saved != latest`. 14 of those are pure permutation inside
one directory: the conversation set is identical, only the pane assignment is
scrambled. The remaining one is offered a conversation that is older than several
of the saved ids and simply fell through the greedy walk.

Checked per directory, there are **zero** conversations on disk newer than every
saved id of that directory's panes, and all 26 saved ids exist on disk. So `saved`
is the correct answer for 26 of 26 panes today, and the UI asks 26 questions of
which 15 dangle a misleading alternative.

### Panes are stable, so most of the questions have no content

Distinct conversation ids per pane across 101 snapshots (roughly one month of
tiered history):

- 15 of 26 panes: exactly 1 conversation for the whole month
- 11 of 26 panes: 2 to 4 conversations

Most panes have never had a decision to make.

## What exists to fix it

Claude session files carry human-readable labels. Record types present in
`~/.claude/projects/<encoded-cwd>/<id>.jsonl`:

- `ai-title` gives a generated title, e.g. `Investigate network losses and ethernet connectivity issues`
- `last-prompt` gives the most recent user prompt, truncated to about 200 characters
- the first `user` record gives the opening message

All 26 live panes have an `ai-title`. Across all 1840 session files on disk only
145 do, because most are short or predate the feature, so a fallback chain is
required for the history drill-down.

Lookup cost is negligible: `tac FILE | grep -m1 '"ai-title"'` returns in about 3ms
on an 84MB file, since `tac` reads from the end.

## Design

### Placement

New script `scripts/claude-resume.sh`, aliased `cresume` in `.zshrc` alongside
`tsave` / `trestore` / `tsnaps`. `trestore` calls it after the topology restore
completes, passing the snapshot dir it restored from.

The picker is already 107 lines inline in `tmux-restore.sh` and this design takes
it to roughly 350. Splitting follows the precedent of `tmux-snapshots.sh` /
`tsnaps`. Standalone invocation matters independently: re-running the picker
should not require re-restoring topology.

### Pane identity

A pane is keyed by `session name + window index + pane index + cwd`. That key is
what joins a pane to itself across snapshots, and it is what makes the assignment
invariant below expressible.

### Candidate set

For each Claude pane `P` in the restored snapshot `S0`, in descending priority:

1. **lineage**: the `claude_session_id` recorded for `P` in `S0`
2. **lineage**: every id `P` held in an older snapshot, matched on the pane key
3. **stranger**: any conversation in `P`'s project dir that no pane ever held

The lineage/stranger distinction is load-bearing, and was added after this design
was prototyped against live data. A project directory accumulates conversations
that never belonged to any pane: `/security-review` runs and similar tool-spawned
sessions (four appeared in `~/Setup` inside one hour, 27 to 38 lines each), plus
orphans left behind by panes that have since closed. Ranking candidates purely by
mtime let those displace five live panes. A stranger is therefore a last resort,
never a competitor to a conversation a pane actually held.

Each candidate carries the `.jsonl` mtime and a title resolved through the chain
`ai-title` -> `last-prompt` -> first `user` message -> `(untitled)`. Titles are
cached by id and resolved lazily, only for ids that reach a rendered cell. That
bounds the work at panes x columns, roughly 78 lookups, about 0.25s.

A candidate whose `.jsonl` no longer exists renders as `(gone)` and cannot be
selected.

### Assignment invariant

**A conversation is assigned to at most one pane.** Two rules enforce it.

*Reservation*: a conversation another pane held in `S0` is never offered to this
pane, at any tier. This alone makes the swap structurally impossible.

*Three tiers*, each walked in deterministic `(session, window, pane)` order:

1. **Identity.** Every pane whose `S0` conversation still exists on disk keeps it.
2. **Own past.** Remaining panes take the newest surviving conversation from their
   own lineage.
3. **Last resort.** Panes whose entire lineage is gone take the newest available
   stranger from their cwd.

Tier 1 is the fix for the swap bug: the old `latest` applied something like tier 3
to every pane unconditionally. Tiers 2 and 3 only ever run for a pane whose own
conversation has been deleted, which is currently zero of 25 panes.

Verified against the live save: 25 panes, 0 displaced, so the computed default is
exactly the saved assignment. Any non-zero displacement count means a tier rule
has regressed.

### Columns

| Col | Content |
| --- | --- |
| `[1]` | **most recent**, the default. Per pane, the newest surviving conversation that pane actually held, via the three-tier assignment above. |
| `[2]` | **change-driven**. Walking snapshots newest to oldest from `S0`, the first whose per-pane assignment differs from column 1 for at least one pane. |
| `[3]` | **last manual save**. The newest snapshot marked `origin=manual`. |

Column 1 is computed per pane and never takes a conversation from a neighbouring
pane. On current data it resolves to the saved id for every pane, which is the
expected result and confirms the default is safe.

`h` continues the change-driven walk, appending `[4]`, `[5]` and so on. Each new
column must differ from every column already shown, so `h` never adds a duplicate.

Column 3 is omitted entirely when no marked manual snapshot exists, which is the
case for all 101 existing snapshots. Any column whose cells would all render `=`
is not shown.

### Screen

```
Claude resume - 26 panes across 12 sessions

  [1] most recent (default)   [2] 08-03 13:00   [3] 08-02 18:45

  22 panes agree across all columns -> resume most recent    [v] list them

  4 panes differ:

  setup@2026-06-03-14h06  w5   ~/Setup
    [1] fae7432a  Set up Proton Drive CLI access          active 08-03 14:22
    [2]    =
    [3] 51c7915d  Debug direnv secret resolution         active 08-02 17:40

  web_wm_onnx@2026-07-01-21h46  w2   ~/Work/web_wm_onnx
    [1] b2bfcae4  Delete PR and create report PR          active 08-03 11:05
    [2] 34fc2407  Probe evaluation for mini-MIRA models   active 08-03 12:50
    [3]    =

  1/2/3 take column   d pane-by-pane   h more history   Enter default   q none
```

The mockup shows a populated `[3]`. In practice that column is absent on first
run and appears only after the first marked manual save, as described under the
origin marker below.

Cell rendering rules:

- identical to column 1: `=`
- pane absent from that snapshot: `-`
- candidate file missing on disk: `(gone)`
- otherwise: short id, title, last-active time

Panes that agree across every shown column collapse into the one-line count. `v`
expands them. Since 22 of 26 agree today, this keeps every real decision on one
screen.

### Keys

| Key | Action |
| --- | --- |
| `1`, `2`, `3`, ... | Take that column for every pane and resume |
| `Enter` | Same as `1` |
| `d` | Pane-by-pane detail. fzf with title and last-active in the line when available, numbered menu otherwise |
| `h` | Append the next change-driven column |
| `v` | Expand the agreed panes |
| `q` | Restore nothing, leave panes at a shell |

### Execution

Unchanged from today: `tmux send-keys -t "$target" "claude --resume '$id'" Enter`
per pane. A selected id that is missing from disk degrades to a bare
`claude --resume` for that pane, which opens Claude's own picker.

`TMUX_RESTORE_NO_LAUNCH=1` keeps its current meaning, pre-typing without Enter.

### trestore integration

`trestore -b` (batch) takes column 1 with no prompt, which preserves today's
"resume everything" semantics with the per-pane rule corrected. Interactive
`trestore` shows the table. `trestore` gains no new flags.

### tsave change: origin marker

`tmux-save.sh` writes an `origin` file into the staging dir before the swap,
containing `manual`, `cron`, or `shutdown`. Because history snapshots are made
with `cp -a` of the completed save dir, the marker propagates automatically.

Detection:

1. explicit `--origin VALUE` wins
2. otherwise `manual` when stdin is a tty
3. otherwise `cron`

Wiring at the three call sites:

- systemd `ExecStop` in `tmux-save-on-shutdown.service` gains `--origin shutdown`
- the crontab `*/15` line gains `--origin cron`
- a hand-typed `tsave` falls through to the tty check and records `manual`

Snapshots without an `origin` file read as unknown. Column 3 therefore stays
hidden until the first marked manual save exists. `tsnaps --list` gains an origin
column, which is useful on its own.

Call-site tracking status:

- the shutdown unit lives in the repo at `scripts/tmux-save-on-shutdown.service`
  and `~/.config/systemd/user/tmux-save-on-shutdown.service` is a symlink to it,
  so editing the repo file is the whole change. A `systemctl --user daemon-reload`
  applies it.
- the crontab is not tracked (`scripts/crontabs/` is empty), so `--origin cron` is
  a manual, machine-local edit. The tty fallback means an unmarked cron entry
  still records `cron` correctly, so this edit is belt-and-braces rather than
  load-bearing.

## Testing

The harness already exists: `tests/test_tmux_save_restore.py` drives the real
scripts from pytest against a private tmux server (`TMUX_TMPDIR` plus a short
socket dir) with `TMUX_SAVE_DIR` and `TMUX_SAVE_LOG` pointed at `tmp_path`. New
tests follow that pattern in `tests/test_claude_resume.py`. Run with
`uv run pytest`.

Candidate construction, the assignment invariant, and column selection are pure
functions of a snapshot tree plus a project-dir tree. They are tested against a
fixture of synthetic snapshots and synthetic `.jsonl` files, with the table
rendered to stdout and asserted. No tmux server is involved for those.

This requires one new env override: `CLAUDE_PROJECTS_DIR`, defaulting to
`$HOME/.claude/projects`. Without it the project dir is hardcoded and the table
logic cannot be tested against a fixture. `TMUX_SAVE_DIR` and
`TMUX_SAVE_HISTORY_DIR` already exist and are reused.

Cases that must be covered:

- two panes in one cwd do not swap conversations (the regression this fixes)
- a live pane keeps its own conversation when a newer stranger exists in the cwd
- a stranger is used only when the pane's whole lineage is gone from disk
- a pane whose saved id is gone from disk falls back and renders `(gone)`
- a pane absent from an older snapshot renders `-` in that column
- a column that would be all `=` is not shown
- `h` never appends a duplicate column
- the title fallback chain hits each of its four levels
- origin detection returns `manual` / `cron` / `shutdown` for the three call paths

Only `send-keys` execution needs a live server. That runs on an isolated `-L`
socket, per the rule that tmux save/restore work never touches the default server.

`tmux-save.sh` is cron-critical, so the origin change must leave its default path
behavior-compatible: a save with no flags and no tty still produces a complete
snapshot, with the marker as the only addition.

## Rejected alternatives

**Keep the per-pane loop as the default.** This is the 26 prompts the redesign
exists to remove. Kept, with titles added, behind `d`.

**fzf multi-select over all panes as the primary screen.** Hides the column
concept, which is the part that makes a bulk decision possible, and depends on fzf
being installed. Appropriate for the `d` drill-down, wrong for the main screen.

**Full grid, one row per pane.** Closest to the original sketch, but 26 rows plus
session headers runs about 40 lines and squeezes titles to roughly 28 characters.
The difference-focused layout shows the same information with the no-decision rows
compressed.

**Grid collapsed to one row per tmux session.** Fits in 15 lines but hides which
pane is in conflict until you drill in, which reintroduces the exploration cost
the redesign is meant to remove.

**Columns at fixed time offsets (3h ago, yesterday).** Measured all `=` on real
data, because 15 of 26 panes held one conversation for the entire month. Dead
space in the common case.

**cwd-newest as "latest".** Today's behavior and the source of the swap bug.

## Files touched

- `scripts/claude-resume.sh` (new)
- `scripts/tmux-restore.sh` (drop lines 272-378, call the new script)
- `scripts/tmux-save.sh` (origin marker)
- `scripts/tmux-snapshots.sh` (origin column in `--list`)
- `dotfiles/.zshrc` (`cresume` alias)
- `scripts/tmux-save-on-shutdown.service` (`--origin shutdown`, symlinked into
  `~/.config/systemd/user/`, so the repo edit is the whole change)
- `tests/test_claude_resume.py` (new)
- crontab (`--origin cron`, untracked, machine-local)
