# deep-research-web

Runs a research charter as a Research conversation on claude.ai, in Louis's own account,
and collects the report back with graded citations. Design:
`docs/plans/2026-09-16-deep-research-claude-web-engine-design.md`. This automates a
logged-in claude.ai session, which the Consumer Terms prohibit; Louis accepted that on
2026-09-16 (see the design doc). Keep the volume human: one run at a time, five-minute polls.

## Install (as the user, once per host)

    deep-research-web/install.sh

Then, in a normal tmux window (it prompts once, for whatever the mail carries: the code,
or the sign-in link pasted whole):

    deep-research-web login <name>      # <name> = the claude-usage account, e.g. work5
    deep-research-web switch <name>     # make it the live profile

Root side, paste in a normal tmux window (keeps the profile out of the backup):

    sudo cp backup/restic-excludes /etc/restic/excludes

Chrome runs headed on the machine's X session (found through `/tmp/.X11-unix`, so the
display number may change across boots) and nobody is looking at that screen. Headless
Chrome never clears claude.ai's Cloudflare challenge on this host, probed 2026-09-16, so
`--headless` exists only for experiments. If a command reports a challenge that did not
clear, the X session is the thing to check (`ls /tmp/.X11-unix`, the unlock script).

The engine imports two modules (`deep_research.charter`, `deep_research.verify`) from the
repo root one level up (this project's parent directory by default), overridable with
`DEEP_RESEARCH_WEB_SPACESUIT`.

## Use

    deep-research-web launch --charter charter.md     # prints the chat URL
    deep-research-web status                          # running / done / needs-reply / failed / stale
    deep-research-web collect <run-id>                # report.md, sources.md, run-result.json, grades
    deep-research-web report <run-id> [--out PATH]    # the collected report.md, stdout by default
    deep-research-web archive <run-id> [--name X]     # copy (or rename) a collected run into the library
    deep-research-web check-claims <run-id>           # opt-in: an independent headless verifier, primary pages only
    deep-research-web list
    deep-research-web stop <run-id>

Any <run-id> may be a unique PREFIX; an ambiguous one is refused with its candidates.
`report` reads only the disk, so `report <id> > file.md` is the whole report and costs
nothing. `needs-reply` means the assistant spoke and never launched research: nothing in
the conversation JSON separates a clarifying question from a refusal or a plain answer, so
one state covers all three. `status` persists that state but never `done`, which is the
watcher's to write.

Library: with `DEEP_RESEARCH_WEB_ARCHIVE_ROOT` set, every `collect` (the CLI's and the
watcher's) also copies the finished run to `<root>/<start date>-<name>/`: `charter.md`,
`report.md`, `sources.md`, `run-result.json`, `verification.*` when present, `archive.json`
(sanitised metadata: no org id, no paths), a `README.md` written once and then left to
humans, and a regenerated `<root>/README.md` index. `--name` at launch chooses the name
(lowercase kebab-case, at most 60 characters); without it the question is slugged.
`conversation.json` and `run.json` never leave the runs root. The engine never touches git:
whoever runs the collect commits the directory in the repo that hosts the root.

`check-claims` is never automatic. It runs `claude -p` with WebFetch and WebSearch only, a
JSON schema for the answer, the report inlined, and a prompt that asks an independent
reader for CONFIRMED / PARTIALLY / REFUTED / UNREACHABLE per load-bearing claim with the
verbatim supporting text; the answer is validated and written as `verification.json` and
`verification.md`, then the run is re-archived. It spends the account's usage and takes
minutes; `--timeout` is in minutes (default 60).

Exit codes: 0 ok, 1 problem, 2 logged out, busy, or profile unusable, 3 preflight refused
(account flag, model, project, or a run already in flight), 4 usage window exhausted,
5 no research started and the assistant is waiting on a reply, 6 still running.

Grades: QUOTED (quoted string found on the live page), LIVE (page resolves, nothing
verbatim to match), MISQUOTED (page live, quoted string absent), DEAD, UNVERIFIABLE,
UNCHECKED (`--no-verify`).

## Accounts

Each claude.ai account has its own saved profile under
`~/.local/state/deep-research-web/profiles/<name>/`, named as in claude-usage.
`chrome-profile` is a symlink to the live one; a launch always runs there.

    deep-research-web profiles          # saved profiles, the live one, each account's meters
    deep-research-web switch [<name>]   # no name: the saved account with the most weekly room

A launch refused on usage walks the saved accounts that have room, most room first,
switching and relaunching until one takes it. An account that refused is never asked
again in that launch. When none has room, the launch exits 4 and names the earliest
window that reopens. An account flag found while polling halts every running run; a flag
at launch refuses the launch, never switches. Each run records its account, and status, collect, stop and the
watcher read it through that account's own profile. A new account needs a claude.ai
Project named "Deep research" before its first launch.

## Logout

    rip ~/.local/state/deep-research-web/profiles/<name>

Then `switch` to another profile if this one was live.

## Migration from one profile

Move the old single profile directory to `profiles/<name>` with `mv -T`. Then run
`deep-research-web switch <name>` to make it live. Records without `account` still
read through the live link.

The watcher timer (`deep-research-web-watch.timer`) is registered in
`monitoring/services.yaml`; its Telegram tag `[deep-research]` in
`monitoring/telegram-senders.yaml`.
