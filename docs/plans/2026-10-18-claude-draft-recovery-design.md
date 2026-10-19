# Claude draft recovery: keep the prompt a crash would eat

Date: 2026-10-18
Status: design, awaiting Louis's review. No code yet.

## Problem

A long prompt is often 30 minutes of typing plus masked pastes (`[Pasted text #1 +60
lines]`). If Claude Code, tmux or the machine dies before Enter, all of it is lost.
Goal: get that draft back, by session, at any point in time ("the biggest thing I had
before the crash"), with no action at use time.

## What is proven (throwaway sessions on an isolated tmux socket, v2.1.283 to v2.1.303)

| Fact | Evidence |
|---|---|
| Claude never writes an unsent draft to disk, not while typing, not on SIGHUP | random markers typed, never submitted, then grep of `~/.claude` |
| `paste-cache/<hash>.txt` is written at submit, not at paste | mtime equals the `history.jsonl` timestamp to the second, 5 of 5 |
| The input box grows to about the terminal height, then scrolls inside itself; the top of a long draft is in neither `capture-pane -p` nor `-S -` | START marker count 0 in both |
| Claude's own `.prompt-draft` writer exists only in the background-daemon socket server, writes only after the daemon sends `shutdown` | bundle read, 2.1.303 |
| A daemon cannot attach to running sessions later | `kernel.yama.ptrace_scope = 1` |
| A stdin tap at launch catches every byte before Claude sees it, and survives `kill -9` of anything | spike: the marker typed 50 ms before each kill is in the log |
| util-linux `script -I` blocks SIGHUP; when tmux dies it stays alive and keeps Claude running as an orphan (without it Claude exits in 2 s) | spike + `lib/pty-session.c:234-251` |

So the only full capture is a terminal proxy at launch that logs what is typed, and
`script` cannot be that proxy.

## Rejected

- Scraping the pane (`capture-pane`): clips exactly the long drafts.
- Backing up `paste-cache`: written at submit, protects nothing.
- `CLAUDE_JOB_DIR`: means faking the background daemon (changes the system prompt,
  permission rules, `/restart`, Remote Control state) and still writes only at shutdown.
- `script -I`: orphans on tmux death, needs a second implementation on macOS (BSD
  `script -r`, binary records).
- A SessionStart hook to learn session ids: the settings file is managed elsewhere and
  rewritten in place; the ids can be recovered from `history.jsonl` instead (below).

## Design

### 1. `claude-draft-tap` (`dotfiles/bin/`, Python 3 stdlib only)

A terminal proxy, about 150 lines: `pty.fork()`, the outer terminal in raw mode, a
`select` loop copying stdin to the pty and the pty to stdout.

- Logs **stdin only**, never output. Each read chunk becomes one JSONL line
  `{"t": <epoch ms>, "b": "<base64>"}`, appended to the launch's `keys.jsonl`. The
  line is written before the bytes go to Claude. A torn last line is ignored when read.
- `fsync` at most once per second while there are unsynced bytes, so a power loss
  costs at most about 1 s of typing.
- Behaves like a terminal disappearing: on stdin EOF/EIO, or SIGHUP/SIGTERM to the
  proxy, it sends SIGHUP to Claude, waits up to 3 s, then SIGKILL, and exits.
- Forwards SIGWINCH (copies the window size to the pty), exits with Claude's status,
  restores the terminal on every exit path.
- **Fail-open for logging**: any error writing the log disables logging for that
  launch (one line to stderr after Claude exits), the session goes on. The proxy itself
  is still a single point of failure for the session, so it stays small and is tested
  against every crash below.
- Sets `CLAUDE_DRAFT_TAP=<launch dir>` in Claude's environment, so a nested launch
  does not tap twice.

### 2. Store: `~/.claude/draft-history/<launch-id>/` (dir 0700, files 0600)

- `launch-id` = `<UTC yyyymmddThhmmss>-<proxy pid>`.
- `meta.json`: start time, cwd, argv, `$TMUX_PANE`, terminal size, hostname, Claude
  version.
- `keys.jsonl`: the log above.
- Retention: every new launch prunes launch dirs older than 30 days. No timer, no
  service.
- Not in backups: added to each machine's restic exclude list (in the private infra
  repo, not here). The drafts are personal text and whatever was pasted.

### 3. The rebuild (`claude_draft/rebuild.py`)

A line-editor emulator that follows Claude Code's own key map (2.1.303):

- **Inserting**: printable text; a bracketed paste (`ESC[200~ … ESC[201~`) inserted as
  one block, with its FULL text, not Claude's placeholder.
- **New line without submit**: `LF` (Ctrl-J), `ESC CR` (Alt-Enter), `ESC[13;2u` and
  `ESC[27;2;13~` (Shift-Enter, the CSI u and xterm forms), and `\` + CR.
- **Submit**: CR with no `\` before the cursor. Each submit closes a segment.
- **Editing**: Left/Right, Ctrl/Alt word moves, Home/End (`ESC[H`, `ESC[F`, `ESC[1~`,
  `ESC[4~`), Ctrl-A/E/B/F, Backspace `0x7f`/`0x08`, Delete/Ctrl-D, Ctrl-K/U/W, Alt-D,
  Alt-Backspace, and Ctrl-Y with its own kill ring.
- **Clearing**: Ctrl-L, Esc Esc, Ctrl-C with text present.
- **Ignored**: the terminal's replies to Claude's queries, which also arrive on stdin:
  DA/XTVERSION (`ESC P >|… ESC \`, `ESC[?…c`), focus `ESC[I`/`ESC[O`, theme
  `ESC[?997;…n`, size and colour reports.
- **Unknowable**: text that comes from inside Claude (Up/Down history recall, Tab
  completion, Ctrl-R, Ctrl-G external editor, undo, vim mode). From that point the
  segment is marked `uncertain` and the typed text after it is still kept, so nothing
  typed is ever dropped. The raw log stays available (`show --raw`).

### 4. Session ids, without a hook

Claude records every submit in `history.jsonl` with `sessionId`, `project`, `timestamp`
and the text. A rebuilt submit segment from the same cwd, within a few seconds of its
CR, with matching text, gives that segment's session id. The unsent tail takes the id
of the last matched submit of the launch (`/clear` and `--resume` are handled because
each submit is matched on its own). A launch with no submit shows its `$TMUX_PANE` and
start time instead.

### 5. `claude-draft` CLI (`dotfiles/bin/`)

- `claude-draft list`: recent launches; session id prefix, cwd, last keystroke, size of
  the unsent tail, `uncertain` flag.
- `claude-draft largest [PREFIX]`: the biggest unsent tail. The default answer to "what
  did I lose".
- `claude-draft show PREFIX [--at TIME] [--raw]`: the draft as it stood at a time
  (replay up to that time); `--raw` prints the decoded bytes.
- `claude-draft check [--since 7d]`: for every submit in `history.jsonl` that a
  launch saw, compares the rebuilt text with what Claude recorded (pastes over 1024
  chars are read through `contentHash` from `paste-cache`). Prints the exact-match rate
  and each miss. This is the acceptance measure.
- A prefix resolves like `claude-session` (3+ hex chars of a session id, or of a
  launch id).

### 6. The zsh hook (`dotfiles/.zshrc`, the existing `claude()` function)

The last line becomes: if stdin and stdout are terminals, `CLAUDE_DRAFT_TAP` is unset,
the tap is installed and the switch allows it, run
`claude-draft-tap -- command claude …`; otherwise `command claude …` as today.
The switch is `CLAUDE_DRAFT=on` during the trial (opt-in) and `CLAUDE_DRAFT != off`
after it (opt-out); see Rollout. Headless `claude -p` from scripts never reaches this function (non-interactive shells
do not load `.zshrc`), and pipes fail the terminal check anyway.

## Failure model

| Event | Draft kept up to | What else happens |
|---|---|---|
| Claude crashes / OOM-killed / `kill -9` | the last keystroke | proxy exits with 137 |
| tmux server dies, pane killed, SSH-attached client gone | the last keystroke | proxy SIGHUPs Claude, like a terminal; no orphan |
| proxy killed `-9` | the last keystroke | Claude loses its terminal and exits |
| reboot (clean) | the last keystroke | |
| power loss / kernel panic | about 1 s before | fsync cadence |
| SSH drop with tmux alive | nothing lost | tmux keeps the session |

## Tests (`tests/test_claude_draft*.py`, real files and real ptys, no mocks)

1. **Rebuild units** from byte streams: every key in section 3, plus the replies to
   filter. A property test with seeded stdlib `random` (no new dependency): 1000 random
   edit scripts over the supported keys, the rebuild equals a reference model.
2. **Differential against Claude** (integration, marked slow): a real `claude` under the
   tap on a private tmux socket, generated edit scripts on drafts that fit the box, the
   rebuild must equal the text read from the box with `capture-pane`. Zero mismatches
   across 200 scripts. Nothing is ever submitted.
3. **Crash suite** (integration): a 60-row draft with mid-line edits and a real masked
   paste (raw bracketed bytes, the `[Pasted text` placeholder checked on screen), then
   each event in the failure model that can be produced (`kill -9` Claude,
   `kill-server`, kill the pane, `kill -HUP` / `-9` the proxy). Each time `claude-draft
   largest` must equal the known draft byte for byte, and no Claude process may remain
   after 5 s.
4. **Terminal behaviour**: resize while running (Claude repaints at the new size), exit
   status passed through, terminal mode restored after every exit path.
5. **Latency**: time from send-keys to the char on screen, with and without the tap,
   20 samples; the medians must stay within 2 ms.
6. **Ground truth in use**: `claude-draft check` after a week.

## Rollout

1. Build and pass tests 1-5 on Linux, then on the Mac (same code).
2. One tmux window with `CLAUDE_DRAFT=on` (opt-in during the trial) for a week.
3. `claude-draft check --since 7d`: every miss explained; latency and orphans clean.
4. Default on, `CLAUDE_DRAFT=off` as the escape hatch.

## Open points for Louis

- Retention: 30 days is a guess.
- Ctrl-G (external editor) is the one composing path the tap cannot see; a long
  prompt written there is already protected by the editor's own swap file.
