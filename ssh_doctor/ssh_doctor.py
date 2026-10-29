#!/usr/bin/env python3
# ABOUTME: ssh-doctor: lints shared ssh config modules (~/.ssh/config.d) and reports, per module,
# ABOUTME: which key this machine offers (~/.ssh/to/<dest>) and whether it exists. Stdlib only.
"""ssh-doctor

    ssh-doctor                 per module: aliases, key path, ok / MISSING on this machine
    ssh-doctor lint [FILE...]  refuse what a shared module must never contain
    ssh-doctor keys            per module: the key file and its fingerprint (revocation list)

A shared module is code that runs on every machine that loads it: ProxyCommand and friends
execute locally at the next `ssh`, before any host key is checked. The lint is the guard.
"""
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

# Keywords that run a local command or pull in other config: refused outright.
FORBIDDEN = {"proxycommand", "localcommand", "permitlocalcommand", "knownhostscommand",
             "match", "include", "identityagent", "userknownhostsfile"}
# Keywords refused only with a weakening value.
FORBIDDEN_VALUES = {"forwardagent": {"yes"}, "stricthostkeychecking": {"no", "off"}}
# Every Host block must pin these.
REQUIRED = ("hostkeyalias", "identitiesonly", "identityfile")
CONFLICT = re.compile(r"^(<{7}|={7}|>{7})( |$)")
# A Host token: a name or a glob, never anything ssh could read as an option.
ALIAS = re.compile(r"^[A-Za-z0-9_.*?][A-Za-z0-9_.*?-]*$")


def config_dir() -> Path:
    return Path.home() / ".ssh" / "config.d"


def parse(text: str):
    """Yield (lineno, keyword, value) for each directive; comments and blanks skipped."""
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z]+)\s*(?:=\s*|\s+)(.*)", line)
        if m:
            yield n, m.group(1).lower(), m.group(2).strip()
        else:
            yield n, line.lower(), ""


def blocks(text: str):
    """[(lineno, aliases, {keyword: value})] for each Host block."""
    out = []
    for n, kw, val in parse(text):
        if kw == "host":
            out.append((n, val.split(), {}))
        elif out:
            out[-1][2].setdefault(kw, val)
    return out


def lint_file(path: Path) -> list[str]:
    problems = []
    text = path.read_text()
    for n, raw in enumerate(text.splitlines(), 1):
        if CONFLICT.match(raw):
            problems.append(f"{path.name}:{n}: merge conflict marker")
    if path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        problems.append(f"{path.name}: writable by group or others (ssh refuses it too)")
    for n, kw, val in parse(text):
        if kw in FORBIDDEN:
            problems.append(f"{path.name}:{n}: {kw} is not allowed in a shared module")
        elif kw in FORBIDDEN_VALUES and val.lower() in FORBIDDEN_VALUES[kw]:
            problems.append(f"{path.name}:{n}: {kw} {val} is not allowed in a shared module")
        elif kw == "host" and "*" in [p.lstrip("!") for p in val.split()]:
            problems.append(f"{path.name}:{n}: Host * is not allowed: a module names its own hosts")
    bs = blocks(text)
    if not bs:
        problems.append(f"{path.name}: no Host block")
    for n, aliases, opts in bs:
        for req in REQUIRED:
            if req not in opts:
                problems.append(f"{path.name}:{n}: Host {' '.join(aliases)} lacks {req}")
        if opts.get("identitiesonly", "yes").lower() != "yes":
            problems.append(f"{path.name}:{n}: Host {' '.join(aliases)} needs IdentitiesOnly yes")
    for n, aliases, _ in bs:
        for a in aliases:
            if not ALIAS.match(a.lstrip("!")):
                problems.append(f"{path.name}:{n}: alias {a!r} is not a plain host name")
    # ssh -G evaluates the file as it parses it (Match exec runs right there), so the real
    # parser only ever sees a module the static checks above found clean.
    if bs and not problems:
        alias = next(a for a in bs[0][1] if not a.startswith("!"))
        r = subprocess.run(["ssh", "-G", "-F", str(path), "--", alias],
                           capture_output=True, text=True)
        if r.returncode != 0:
            err = (r.stderr.strip().splitlines() or ["(no message)"])[-1]
            problems.append(f"{path.name}: ssh -G refuses it: {err}")
    return problems


def modules(paths) -> list[Path]:
    if paths:
        return [Path(p) for p in paths]
    return sorted(config_dir().glob("*.conf"))


def key_paths(path: Path) -> list[str]:
    seen = []
    for _, _, opts in blocks(path.read_text()):
        k = opts.get("identityfile")
        if k and k not in seen:
            seen.append(k)
    return seen


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def cmd_lint(paths) -> int:
    problems = [p for m in modules(paths) for p in lint_file(m)]
    for p in problems:
        print(p)
    if not problems:
        print(f"lint ok: {len(modules(paths))} module(s)")
    return 1 if problems else 0


def cmd_report() -> int:
    missing = 0
    for m in modules(None):
        aliases = " ".join(a for _, al, _ in blocks(m.read_text()) for a in al)
        for k in key_paths(m):
            target = expand(k)
            if target.exists():
                status = f"ok -> {Path(os.path.realpath(target)).name}"
            else:
                status = "MISSING"
                missing += 1
            print(f"{m.name:<22} {k:<28} {status:<28} {aliases}")
    if missing:
        print(f"\n{missing} key path(s) missing on this machine: make a key there, or "
              "ln -s ../<existing key> ~/.ssh/to/<dest>")
    return 1 if missing else 0


def fingerprint(target: Path) -> str:
    pub = Path(str(target) + ".pub")
    src = pub if pub.exists() else target
    r = subprocess.run(["ssh-keygen", "-lf", str(src)], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else f"(no fingerprint: {r.stderr.strip()})"


def cmd_keys() -> int:
    for m in modules(None):
        for k in key_paths(m):
            target = expand(k)
            if not target.exists():
                print(f"{m.stem:<16} {k:<28} MISSING")
                continue
            real = Path(os.path.realpath(target))
            print(f"{m.stem:<16} {k:<28} {real.name:<26} {fingerprint(real)}")
    return 0


def main(argv) -> int:
    if argv[:1] == ["lint"]:
        return cmd_lint(argv[1:])
    if argv[:1] == ["keys"]:
        return cmd_keys()
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    return cmd_report()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
