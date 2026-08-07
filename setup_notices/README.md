<!-- ABOUTME: Authoring guide for the setup_notices system. -->
<!-- ABOUTME: Explains the notice file format, lifecycle, and when to use this mechanism. -->

# setup_notices

Per-machine first-time-setup reminders. When you pull ~/Setup on a machine,
the zshrc shows a one-line reminder for any pending notice until you run
its ack command.

## When to create a notice

Create one when a Setup change requires a **manual step on each machine
that pulls it** — installing a package, editing a config file outside the
repo, running a one-time migration. Do **not** create one for changes that
the dotfiles deploy or a zshrc function handles automatically.

## File format

One file per notice, in this directory:

    setup_notices/YYYY_MM_DD_<snake_case_id>.md

Frontmatter:

    ---
    id: <stable_snake_case_id>
    summary: <one-line, shown in listings>
    ack_cmd: <absolute path to an idempotent ack command, or omit>
    ---

Body: markdown. Include what the notice is for, a copy-paste install
block, and a link back to the motivating plan/spec in `plans/`.

## Lifecycle

- Commit the notice file to the repo.
- Every machine that pulls sees the reminder on next shell start.
- User runs `setup_notices show <id>`, follows the instructions.
- `setup_notices ack <id>` or `setup_notices run <id>` clears it locally
  (writes `~/.cache/setup_notices/<id>.acked`).
- When the manual step is no longer relevant on any active machine, delete
  the file from the repo. (The per-machine ack file is harmless but can be
  cleaned up with `setup_notices unack <id>` then removing the ack file
  manually.)

## CLI

    setup_notices              # list pending
    setup_notices all          # list all (pending + acked)
    setup_notices show <id>    # cat full body
    setup_notices run <id>     # execute ack_cmd, ack on success
    setup_notices ack <id>     # mark acked manually
    setup_notices unack <id>   # undo ack (for testing)

## Ack state location

`~/.cache/setup_notices/<id>.acked` — empty file, presence means acked.
Per-machine, never committed.
