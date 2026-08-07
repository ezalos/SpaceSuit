# ABOUTME: CLI entry point for the agents dashboard: `uv run python -m agents_dashboard once`.
# ABOUTME: `once` prints a snapshot for eyeballing; `serve` runs the HTTP server.

from __future__ import annotations

import json
import json as _json
import os
import shutil
import sys

import fire

from .collect import collect, snapshot_to_dict
from .termview import render_terminal


def once(as_json: bool = False) -> None:
    """Collect one snapshot and print it."""
    snapshot = collect()
    if as_json:
        print(json.dumps(snapshot_to_dict(snapshot), indent=2))
        return
    for card in snapshot.cards:
        if card.not_started:
            print(f"{card.name}: not started")
            continue
        print(f"{card.name}:")
        for pane in card.panes:
            flag = pane.waiting_reason.value if pane.waiting_reason else "-"
            print(f"  [{pane.phase.value:<8}] [{pane.activity.value:<7}] {flag:<13} {pane.title}")


def serve(port: int = 8770, host: str = "127.0.0.1") -> None:
    # Default to loopback for safety: binding all interfaces is opt-in via --host=0.0.0.0
    from .server import run

    run(port=port, host=host)


def tls(phase: bool = False, json: bool = False) -> None:
    """Terminal view of every tmux window and its Claude session.

    Skips the 4 MB phase scan unless --phase is given; it costs 0.434 s and
    the default grid does not show a phase column.
    """
    snapshot = collect(with_phase=phase)

    if not snapshot.cards:
        print("No tmux sessions", file=sys.stderr)
        raise SystemExit(1)

    if json:
        print(_json.dumps({
            "generated_at": snapshot.generated_at,
            "sessions": [
                {
                    "name": card.name,
                    "attached": card.attached,
                    "windows": [
                        {
                            "window_index": w.window_index,
                            "pane_index": w.pane_index,
                            "command": w.command,
                            "cwd": w.cwd,
                            "quiet_since": w.quiet_since,
                            "claude": None if w.claude is None else {
                                "session_id": w.claude.session_id,
                                "title": w.claude.title,
                                "phase": w.claude.phase.value,
                                "phase_is_guess": w.claude.phase_is_guess,
                                "activity": w.claude.activity.value,
                                "waiting_reason": (w.claude.waiting_reason.value
                                                   if w.claude.waiting_reason else None),
                                "waiting_since": w.claude.waiting_since,
                                "tasks": {"known": w.claude.tasks.known,
                                          "total": w.claude.tasks.total,
                                          "completed": w.claude.tasks.completed},
                                "git_branch": w.claude.git_branch,
                                "attach": w.claude.attach,
                            },
                        }
                        for w in card.windows
                    ],
                }
                for card in snapshot.cards
            ],
        }, indent=2))
        return

    # Colour only for a terminal, so `tls | grep` stays clean.
    print(render_terminal(
        snapshot,
        width=shutil.get_terminal_size((100, 24)).columns,
        color=sys.stdout.isatty() and os.environ.get("NO_COLOR") is None,
        show_phase=phase,
    ), end="")


if __name__ == "__main__":
    fire.Fire({"once": once, "serve": serve, "tls": tls})
