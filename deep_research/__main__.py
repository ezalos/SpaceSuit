# ABOUTME: CLI for detached deep research runs: launch, status, collect, stop, list.
# ABOUTME: Invoked as `deep-research`; all research logic lives in the sibling modules.
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .charter import CharterError, parse_charter
from .launcher import LaunchError, launch, session_alive
from .manifest import Manifest, find_runs
from .status import DONE_SENTINEL, REPORT_NAME, RESULT_NAME, RunState, resolve_state

DEFAULT_RUNS_ROOT = Path.home() / "research-runs"
DEFAULT_NOTIFY = Path.home() / ".claude" / "skills" / "notify-louis" / "notify.sh"


def _find(runs_root: Path, run_id: str) -> Manifest | None:
    for m in find_runs(runs_root):
        if m.run_id == run_id:
            return m
    return None


def _state(m: Manifest) -> RunState:
    # Short-circuit on the sentinel before probing liveness: a finished run needs no
    # subprocess, which also keeps the unit tests from spawning a real claude.
    out = Path(m.out_dir)
    if (out / DONE_SENTINEL).exists():
        return RunState.DONE
    alive = m.status == "running" and session_alive(m.bg_session_id)
    return resolve_state(out, session_alive=alive)


def cmd_launch(args: argparse.Namespace) -> int:
    try:
        charter = parse_charter(Path(args.charter).read_text(encoding="utf-8"))
    except (OSError, CharterError) as exc:
        print(f"cannot read charter: {exc}", file=sys.stderr)
        return 2

    # When the operator does not pin a runs root, derive it from --out. Otherwise the
    # run lands outside the default root: status and collect would not find it, and the
    # concurrency cap would count zero runs and never fire.
    if args.runs_root == str(DEFAULT_RUNS_ROOT):
        runs_root = Path(args.out).resolve().parent
    else:
        runs_root = Path(args.runs_root)

    notify = str(DEFAULT_NOTIFY) if DEFAULT_NOTIFY.exists() else None
    try:
        m = launch(
            charter,
            out_dir=Path(args.out),
            runs_root=runs_root,
            model=args.model,
            effort=args.effort,
            notify_script=notify,
            force=args.force,
        )
    except LaunchError as exc:
        print(f"launch failed: {exc}", file=sys.stderr)
        return 1

    print(f"launched {m.run_id}")
    print(f"  session {m.bg_session_id} ({m.model}, effort {m.effort})")
    print(f"  output  {m.out_dir}")
    print(f"  watch   deep-research status {m.run_id} --runs-root {runs_root}")
    print(f"  collect deep-research collect {m.run_id} --runs-root {runs_root}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    runs = find_runs(Path(args.runs_root))
    if args.run_id:
        runs = [m for m in runs if m.run_id == args.run_id]
        if not runs:
            print(f"no run named {args.run_id}", file=sys.stderr)
            return 2
    if not runs:
        print("no runs found")
        return 0
    for m in runs:
        print(f"{m.run_id}  {_state(m).value}  {m.out_dir}")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    m = _find(Path(args.runs_root), args.run_id)
    if m is None:
        print(f"no run named {args.run_id}", file=sys.stderr)
        return 2

    out = Path(m.out_dir)
    state = _state(m)
    print(f"{m.run_id}  {state.value}")

    report = out / REPORT_NAME
    if report.exists():
        print(f"  report: {report}")
    else:
        print("  report: not written")

    result_path = out / RESULT_NAME
    if not result_path.exists():
        # Never 0 here. The runner prompt requires run-result.json to be written before
        # the DONE sentinel, so a finished run missing it broke its own contract and we
        # have no idea whether any source was verified. That needs attention by
        # definition, which is exactly what exit 1 means.
        print("  no run-result.json; the run did not report on its own sources")
        return 1

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  run-result.json is unreadable: {exc}", file=sys.stderr)
        return 1

    total = result.get("sources_total", 0)
    verified = result.get("sources_verified", 0)
    print(f"  sources: {verified}/{total} verified")

    problems = False
    unanswered = result.get("unanswered") or []
    if unanswered:
        problems = True
        print("  UNANSWERED questions:")
        for q in unanswered:
            print(f"    - {q}")

    unverified = result.get("unverified") or []
    if unverified:
        problems = True
        print("  UNVERIFIED sources, do not cite these without checking them:")
        for entry in unverified:
            print(f"    - {entry.get('url')}  ({entry.get('reason')})")

    if state is not RunState.DONE:
        problems = True

    return 1 if problems else 0


def cmd_stop(args: argparse.Namespace) -> int:
    m = _find(Path(args.runs_root), args.run_id)
    if m is None:
        print(f"no run named {args.run_id}", file=sys.stderr)
        return 2
    try:
        result = subprocess.run(
            ["claude", "stop", m.bg_session_id],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        # Same posture as launcher.session_alive: a missing claude binary is a message,
        # not a traceback, because a scripted caller reads our exit code.
        print(f"cannot run claude: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(f"stop failed: {result.stderr or result.stdout}", file=sys.stderr)
        return 1
    print(f"stopped {m.run_id} (session {m.bg_session_id})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    runs = find_runs(Path(args.runs_root))
    if not runs:
        print("no runs found")
        return 0
    for m in runs:
        print(f"{m.run_id}  {_state(m).value}  {m.model}/{m.effort}  {m.out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # --runs-root must work both before AND after the subcommand (every call site in
    # the tests and in real usage puts it after, e.g. `status --runs-root X`), so it is
    # registered on a shared parent rather than only on the top-level parser: argparse
    # only hands remaining tokens to the chosen subparser, and a subparser with no
    # matching option would reject them as unrecognized.
    runs_root_parent = argparse.ArgumentParser(add_help=False)
    runs_root_parent.add_argument(
        "--runs-root",
        default=str(DEFAULT_RUNS_ROOT),
        help=f"where runs are discovered (default: {DEFAULT_RUNS_ROOT})",
    )

    parser = argparse.ArgumentParser(
        prog="deep-research",
        description="Launch and collect detached deep research runs.",
        parents=[runs_root_parent],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("launch", help="start a detached research run", parents=[runs_root_parent])
    p.add_argument("--charter", required=True, help="path to the charter Markdown file")
    p.add_argument("--out", required=True, help="output directory for this run")
    p.add_argument("--model", default="fable")
    p.add_argument("--effort", default="max")
    p.add_argument("--force", action="store_true", help="ignore the concurrency cap")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("status", help="show run state", parents=[runs_root_parent])
    p.add_argument("run_id", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "collect", help="summarise a finished run and flag problems", parents=[runs_root_parent]
    )
    p.add_argument("run_id")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("stop", help="stop a running run", parents=[runs_root_parent])
    p.add_argument("run_id")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("list", help="list every known run", parents=[runs_root_parent])
    p.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
