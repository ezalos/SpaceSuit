# ABOUTME: CLI for the Claude resume table: renders it, reads one key, sends resume commands.
# ABOUTME: Invoked by trestore and standalone as `cresume`.
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .candidates import assign_most_recent, build_candidates
from .claude_paths import transcript_path
from .columns import build_columns
from .render import render
from .snapshots import list_snapshots, read_panes
from .titles import TitleResolver


def resume_commands(assignment: dict, projects_dir: Path) -> list:
    """The exact command each pane should be sent, skipping panes with no choice.

    A chosen conversation whose file has since vanished degrades to Claude's own
    picker rather than failing: `claude --resume <gone-id>` would just error in
    the pane and leave nothing useful on screen.
    """
    out = []
    for pane in sorted(assignment, key=lambda p: p.sort_key):
        sid = assignment[pane]
        if not sid:
            continue
        if transcript_path(pane.cwd, sid, projects_dir) is None:
            out.append((pane, "claude --resume"))
        else:
            out.append((pane, f"claude --resume '{sid}'"))
    return out


def _tmux(argv: list, socket: str):
    base = ["tmux"] + (["-L", socket] if socket else [])
    return subprocess.run(base + argv, capture_output=True, text=True)


def pane_is_busy(pane, socket: str) -> bool:
    """True if this pane already has Claude running.

    Sending into such a pane does not start a session, it types the command in
    as a chat message. The tool is normally run right after a restore when every
    pane sits at a shell, but `cresume` can be run at any time, so guard it.
    """
    r = _tmux(["list-panes", "-t", f"{pane.session}:{pane.window}",
               "-F", "#{pane_index} #{pane_tty}"], socket)
    tty = ""
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pane.pane):
            tty = parts[1]
    if not tty:
        return False
    ps = subprocess.run(["ps", "-t", tty.replace("/dev/", "", 1), "-o", "comm="],
                        capture_output=True, text=True)
    return any(line.strip().rsplit("/", 1)[-1] == "claude"
               for line in ps.stdout.splitlines())


def send(pane, command: str, socket: str, launch: bool) -> None:
    target = f"{pane.session}:{pane.window}.{pane.pane}"
    argv = ["tmux"]
    if socket:
        argv += ["-L", socket]
    argv += ["send-keys", "-t", target, command]
    if launch:
        argv.append("Enter")
    subprocess.run(argv, capture_output=True, text=True)


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="cresume",
        description="Pick which Claude conversation each restored pane resumes.")
    p.add_argument("--snapshot", default=os.environ.get(
        "TMUX_SAVE_DIR", str(Path.home() / ".tmux-save")))
    p.add_argument("--history", default=os.environ.get(
        "TMUX_SAVE_HISTORY_DIR", str(Path.home() / ".tmux-save-history")))
    p.add_argument("--projects", default=os.environ.get(
        "CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))
    p.add_argument("--socket", default=os.environ.get("TMUX_SOCKET", ""))
    p.add_argument("--width", type=int, default=100)
    p.add_argument("--batch", action="store_true",
                   help="take column 1 with no prompt")
    p.add_argument("--no-launch", action="store_true",
                   help="pre-type the commands without pressing Enter")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be sent and exit; no tmux calls")
    p.add_argument("--force", action="store_true",
                   help="send even into panes already running Claude (types the "
                        "command in as a chat message; almost never what you want)")
    return p.parse_args(argv)


def detail_loop(columns: list, titles) -> dict:
    """Per-pane walk, the escape hatch from the bulk columns.

    This is the old trestore behaviour, kept deliberately: it is the right tool
    when the columns genuinely disagree and you want to decide case by case. The
    difference is that every option now carries a title.
    """
    base = columns[0].assignment
    chosen = {}
    for pane in sorted(base, key=lambda p: p.sort_key):
        options = []
        for col in columns:
            sid = col.assignment.get(pane, "")
            if sid and sid not in [o[1] for o in options]:
                options.append((col.key, sid))
        if not options:
            chosen[pane] = ""
            continue
        if len(options) == 1:
            chosen[pane] = options[0][1]
            continue
        print(f"\n  {pane.label}   {pane.cwd}")
        for key, sid in options:
            print(f"    [{key}] {sid[:8]}  {titles.title_for(pane.cwd, sid)}")
        print("    [n] none")
        try:
            answer = input("  choice> ").strip().lower()
        except EOFError:
            answer = ""
        picked = next((sid for key, sid in options if key == answer), None)
        if answer == "n":
            chosen[pane] = ""
        else:
            chosen[pane] = picked if picked else base.get(pane, "")
    return chosen


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    snapshot, projects = Path(args.snapshot), Path(args.projects)

    source_ids = read_panes(snapshot)
    if not source_ids:
        print(f"No Claude panes found in {snapshot}; nothing to resume.")
        return 1 if not (snapshot / "state.tsv").is_file() else 0

    history = [ref for ref in list_snapshots(snapshot, Path(args.history))
               if ref.path != snapshot]
    titles = TitleResolver(projects)
    max_history = 1

    def compute(mh):
        cands = build_candidates(source_ids, history, projects)
        return build_columns(source_ids, cands, history, max_history=mh)

    columns = compute(max_history)

    if args.batch:
        chosen = columns[0].assignment
    else:
        expand = False
        while True:
            print(render(columns, titles, width=args.width, expand=expand))
            try:
                answer = input("choice> ").strip().lower()
            except EOFError:
                answer = "q"
            if answer == "q":
                print("Nothing resumed.")
                return 0
            if answer == "v":
                expand = not expand
                continue
            if answer == "h":
                max_history += 1
                columns = compute(max_history)
                continue
            if answer == "d":
                chosen = detail_loop(columns, titles)
                break
            if answer == "":
                chosen = columns[0].assignment
                break
            match = next((c for c in columns if c.key == answer), None)
            if match is not None:
                chosen = match.assignment
                break
            print(f"  unknown choice: {answer!r}")

    commands = resume_commands(chosen, projects)
    if args.dry_run:
        for pane, cmd in commands:
            print(f"{pane.label}  {cmd}")
        return 0

    sent, skipped = 0, []
    for pane, cmd in commands:
        if not args.force and pane_is_busy(pane, args.socket):
            skipped.append(pane)
            continue
        send(pane, cmd, args.socket, launch=not args.no_launch)
        sent += 1

    verb = "Pre-typed into" if args.no_launch else "Resumed"
    print(f"{verb} {sent} Claude pane(s).")
    if skipped:
        print(f"Skipped {len(skipped)} pane(s) already running Claude "
              f"(sending would have typed the command in as a message):")
        for pane in skipped:
            print(f"  {pane.label}")
        print("Exit Claude in those panes and re-run, or pass --force.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
