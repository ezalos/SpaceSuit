# ABOUTME: `python -m goals report|compare` - the deterministic CLI over one goal manifest and one
# ABOUTME: cost snapshot, or over one frozen protocol and its observation ledger. Exit 0, or 2.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import comparison as comparison_module
from . import comparison_view
from . import report as report_module
from . import view

REPORT_DESCRIPTION = """\
Build one sanitized goal report from a goal manifest and ONE snapshot of the existing
cost engine (`claude-usage cost --json --by session --roots ...`).

It aggregates that snapshot. It does NOT collect usage, parse a transcript, price a model,
call the cost engine, reach the network, or re-verify any acceptance claim: outcome,
implementer, verifier and evidence are attestations copied from the manifest.
Money is reported as allocated API-equivalent value - never as cash, an invoice or a bill.
"""

COMPARE_DESCRIPTION = """\
Build one comparison report from a FROZEN comparison protocol and an observation ledger
of goal reports the `report` subcommand already produced.

It reads those two files and the goal reports they name. It does NOT route work, enroll a
root, call the cost engine, price a token, check a provider, read a subscription, spend
money or admit a job. Outcome, acceptance time, escaped defects and budget admission are
attestations copied from the protocol, the ledger and the goal reports; none is re-verified.

Cash is always reported as unknown. A `candidate` status is a measurement posture, never an
approval and never a promotion - the decision stays with a person.
"""


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m goals", description="Read-only goal, evidence and comparison reporter.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "report", help="build a goal report from a manifest and a cost snapshot",
        description=REPORT_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    build.add_argument("--goal", required=True, type=Path, metavar="FILE",
                       help="the goal manifest (read-only)")
    build.add_argument("--cost", required=True, type=Path, metavar="FILE",
                       help="one cost-engine JSON snapshot for this manifest's roots (read-only)")
    _add_outputs(build, "report")

    compare = subparsers.add_parser(
        "compare", help="build a comparison report from a frozen protocol and its observations",
        description=COMPARE_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    compare.add_argument("--protocol", required=True, type=Path, metavar="FILE",
                         help="the frozen comparison protocol (read-only)")
    compare.add_argument("--observations", required=True, type=Path, metavar="FILE",
                         help="the observation ledger naming one goal report per arm (read-only)")
    _add_outputs(compare, "comparison")
    return parser


def _add_outputs(command, noun):
    command.add_argument("--out", required=True, type=Path, metavar="FILE",
                         help=f"where to write the {noun} JSON (replaced atomically, mode 0600)")
    command.add_argument("--html", type=Path, metavar="FILE",
                         help=f"also write the self-contained HTML {noun} here")
    command.add_argument("--now", metavar="UTC",
                         help="the UTC instant to generate against; defaults to this machine's "
                              "clock. Used for generation only - never to manufacture an "
                              "authorization, an acceptance or an observation time")


COMMANDS = {
    "report": {
        "inputs": ("goal", "cost"),
        "load": lambda args, now: report_module.load_report(args.goal, args.cost, now_utc=now),
        "write": report_module.write_report,
        "render": view.render,
    },
    "compare": {
        "inputs": ("protocol", "observations"),
        "load": lambda args, now: comparison_module.load_comparison(
            args.protocol, args.observations, now_utc=now,
            destinations=_destinations(args)),
        "write": comparison_module.write_comparison,
        "render": comparison_view.render_comparison,
    },
}


def _fail(command, message):
    print(f"goals {command}: {message}", file=sys.stderr)
    return 2


def _destinations(args):
    """Every path this run will write, resolved. `compare` also reads the goal reports its ledger
    names - paths this guard cannot see - so the loader re-checks these against them."""
    resolved = set()
    for name in ("out", "html"):
        target = getattr(args, name, None)
        if target is None:
            continue
        try:
            resolved.add(Path(target).resolve())
        except OSError:
            continue
    return resolved


def _check_destinations(args, inputs):
    """A destination that is also an input would destroy the evidence it was built from."""
    seen = {}
    for name in inputs:
        try:
            seen[Path(getattr(args, name)).resolve()] = name
        except OSError:
            continue
    written = []
    for name in ("out", "html"):
        target = getattr(args, name)
        if target is None:
            continue
        resolved = Path(target).resolve()
        if resolved in seen:
            return f"--{name} points at the {seen[resolved]} input; inputs are read-only"
        if resolved in written:
            return "--out and --html point at the same file"
        written.append(resolved)
    return None


def main(argv=None):
    args = _parser().parse_args(argv)
    command = COMMANDS[args.command]
    problem = _check_destinations(args, command["inputs"])
    if problem:
        return _fail(args.command, problem)

    now = args.now or report_module.utc_now()
    try:
        built = command["load"](args, now)
    except report_module.ReportError as error:
        return _fail(args.command, str(error))

    try:
        command["write"](built, args.out)
    except report_module.ReportError as error:
        return _fail(args.command, str(error))
    except OSError as error:
        return _fail(args.command, f"could not write the output: {error.strerror}")

    if args.html:
        try:
            report_module.write_text_atomic(command["render"](built), args.html)
        except (report_module.ReportError, OSError, RuntimeError) as error:
            # Deliberate partial-output contract: the JSON is already on disk and is correct, so it
            # is KEPT. Pretending the whole run failed would throw away a good document, and
            # silently exiting 0 would hide a missing page. It says both, and exits 2.
            reason = getattr(error, "strerror", None) or str(error)
            return _fail(args.command,
                         f"partial output: {args.out} was written, the HTML at {args.html} was "
                         f"not ({reason})")

    written = str(args.out) + (f" and {args.html}" if args.html else "")
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
