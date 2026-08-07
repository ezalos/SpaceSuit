# ABOUTME: Census of secret-shaped plaintext in .envrc/.secrets files: entropy heuristic +
# ABOUTME: explicit "# not-vaulted" tags. Prints file:var:class lines; never prints values.
import math
import re
import sys
from pathlib import Path

EXPORT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(['\"]?)(.*?)\2\s*(#.*)?$")
SKIP_VALUE = re.compile(r"^(pass://|\$|~|/|\.|https?://|[0-9.]+$)")


def shannon(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in freq.values())


def classify(name: str, value: str, comment: str) -> str | None:
    if comment and "not-vaulted" in comment:
        return "declared-not-vaulted"
    if SKIP_VALUE.match(value):
        return None
    if len(value) >= 20 and shannon(value) >= 3.5:
        return "secret-shaped"
    return None


def scan(paths: list[Path]) -> int:
    hits = 0
    for p in paths:
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = EXPORT_RE.match(line)
            if not m:
                continue
            name, _, value, comment = m.groups()
            verdict = classify(name, value, comment or "")
            if verdict:
                hits += 1
                print(f"{p}\t{name}\t{verdict}\tlen={len(value)}")
    return hits


if __name__ == "__main__":
    roots = [Path(a).expanduser() for a in sys.argv[1:]] or [Path.home()]
    targets: list[Path] = []
    for root in roots:
        targets += list(root.glob("*/.envrc")) + list(root.glob("*/*/.envrc"))
    targets += [Path.home() / "Setup/.secrets.sh"]
    n = scan(sorted(set(t for t in targets if t.exists())))
    print(f"# total: {n}", file=sys.stderr)
