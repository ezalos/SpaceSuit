# ABOUTME: Tests git_pre_commit_guard's content scrub through real git commits and merges in throwaway repos.
# ABOUTME: A merge must scan only what it authors: content taken unchanged from a parent passes, new content is scanned.

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DOTFILES = Path(__file__).resolve().parent.parent / "dotfiles"
GUARD = DOTFILES / "git_pre_commit_guard"
SCRUB = DOTFILES / "git_identity_scrub"

TOKEN = "zz-forbidden-token-zz"
NAME = "Test Author"
EMAIL = "author@example.invalid"
CONTEXT = "synthetic"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def env(tmp_path):
    """An env whose HOME, git config and guard config are all temporary.

    The repo template dir is empty on purpose: a machine whose global
    init.templateDir symlinks the live guard as hooks/pre-commit would otherwise
    hand every test repo that symlink, and installing the guard under test
    through it would overwrite the live guard for every repo on the machine.
    """
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "identity"
    (cfg / "forbidden").mkdir(parents=True)
    (cfg / "contexts.conf").write_text(f"* | {NAME} | {EMAIL} | {CONTEXT}\n")
    (cfg / "forbidden" / f"{CONTEXT}.txt").write_text(TOKEN + "\n")
    (cfg / "scrub.py").symlink_to(SCRUB)
    (tmp_path / "empty-template").mkdir()
    (home / ".gitconfig").write_text(
        f"[user]\n\tname = {NAME}\n\temail = {EMAIL}\n"
        f"[init]\n\tdefaultBranch = main\n\ttemplateDir = {tmp_path / 'empty-template'}\n"
        "[commit]\n\tgpgsign = false\n"
    )
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for k in ("PRO_CONTENT_SCRUB", "GIT_IDENTITY_GUARD"):
        e.pop(k, None)
    e.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_IDENTITY_DIR=str(cfg))
    return e


def git(env, repo, *args, check=True, extra=None):
    r = subprocess.run(
        ["git", *args], cwd=repo, env={**env, **(extra or {})}, capture_output=True, text=True, check=False
    )
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stdout}{r.stderr}")
    return r


@pytest.fixture
def repo(env, tmp_path):
    r = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "-q", f"--template={tmp_path / 'empty-template'}", str(r)],
        env=env, check=True,
    )
    hooks = r / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").symlink_to(GUARD)
    (r / "base.txt").write_text("line one\n")
    (r / "other.txt").write_text("other\n")
    git(env, r, "add", "-A")
    git(env, r, "commit", "-qm", "base")
    return r


def commit_file(env, repo, path, text, bypass=False):
    (repo / path).write_text(text)
    git(env, repo, "add", "--", path)
    git(env, repo, "commit", "-qm", f"edit {path}", extra={"PRO_CONTENT_SCRUB": "off"} if bypass else None)


def blocked(r):
    return r.returncode != 0 and "[content-scrub] BLOCKED" in r.stderr


def test_normal_commit_adding_token_is_blocked(env, repo):
    (repo / "new.txt").write_text(f"see {TOKEN}\n")
    git(env, repo, "add", "new.txt")
    assert blocked(git(env, repo, "commit", "-qm", "leak", check=False))


def test_normal_commit_without_token_passes(env, repo):
    commit_file(env, repo, "new.txt", "harmless\n")


def test_conflict_resolution_adding_token_is_blocked(env, repo):
    git(env, repo, "checkout", "-qb", "side")
    commit_file(env, repo, "base.txt", "side version\n")
    git(env, repo, "checkout", "-q", "main")
    commit_file(env, repo, "base.txt", "main version\n")
    assert git(env, repo, "merge", "side", check=False).returncode != 0
    (repo / "base.txt").write_text(f"resolved with {TOKEN}\n")
    git(env, repo, "add", "base.txt")
    assert blocked(git(env, repo, "commit", "--no-edit", check=False))


def test_merge_bringing_token_unchanged_from_other_parent_passes(env, repo):
    git(env, repo, "checkout", "-qb", "side")
    commit_file(env, repo, "upstream.txt", f"published upstream: {TOKEN}\n", bypass=True)
    git(env, repo, "checkout", "-q", "main")
    commit_file(env, repo, "other.txt", "main diverges\n")
    git(env, repo, "merge", "--no-commit", "--no-ff", "side")
    r = git(env, repo, "commit", "--no-edit", check=False)
    assert r.returncode == 0, r.stderr


def test_resolved_path_is_scanned_but_path_equal_to_a_parent_is_not(env, repo):
    git(env, repo, "checkout", "-qb", "side")
    commit_file(env, repo, "upstream.txt", f"published upstream: {TOKEN}\n", bypass=True)
    commit_file(env, repo, "base.txt", "side version\n")
    git(env, repo, "checkout", "-q", "main")
    commit_file(env, repo, "base.txt", "main version\n")
    assert git(env, repo, "merge", "side", check=False).returncode != 0
    (repo / "base.txt").write_text("clean resolution\n")
    git(env, repo, "add", "base.txt")
    r = git(env, repo, "commit", "--no-edit", check=False)
    assert r.returncode == 0, r.stderr


def test_resolution_in_a_file_named_with_space_and_glob_is_blocked(env, repo):
    odd = "[a] b.txt"
    commit_file(env, repo, odd, "start\n")
    commit_file(env, repo, "a b.txt", "decoy the glob would match instead\n")
    git(env, repo, "checkout", "-qb", "side")
    commit_file(env, repo, odd, "side version\n")
    git(env, repo, "checkout", "-q", "main")
    commit_file(env, repo, odd, "main version\n")
    assert git(env, repo, "merge", "side", check=False).returncode != 0
    (repo / odd).write_text(f"resolved with {TOKEN}\n")
    git(env, repo, "add", "--", odd)
    assert blocked(git(env, repo, "commit", "--no-edit", check=False))


def test_resolution_in_a_file_whose_name_git_quotes_is_blocked(env, repo):
    odd = 'q"uote\\d.txt'
    commit_file(env, repo, odd, "start\n")
    git(env, repo, "checkout", "-qb", "side")
    commit_file(env, repo, odd, "side version\n")
    git(env, repo, "checkout", "-q", "main")
    commit_file(env, repo, odd, "main version\n")
    assert git(env, repo, "merge", "side", check=False).returncode != 0
    (repo / odd).write_text(f"resolved with {TOKEN}\n")
    git(env, repo, "add", "--", odd)
    assert blocked(git(env, repo, "commit", "--no-edit", check=False))


def test_octopus_merge_bringing_token_unchanged_passes(env, repo):
    git(env, repo, "checkout", "-qb", "one")
    commit_file(env, repo, "one.txt", "one\n")
    git(env, repo, "checkout", "-qb", "two", "main")
    commit_file(env, repo, "two.txt", f"published upstream: {TOKEN}\n", bypass=True)
    git(env, repo, "checkout", "-q", "main")
    commit_file(env, repo, "other.txt", "main diverges\n")
    git(env, repo, "merge", "--no-commit", "--no-ff", "one", "two")
    merge_head = repo / git(env, repo, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
    assert len(merge_head.read_text().split()) == 2
    r = git(env, repo, "commit", "--no-edit", check=False)
    assert r.returncode == 0, r.stderr


def test_octopus_merge_adding_token_is_blocked(env, repo):
    git(env, repo, "checkout", "-qb", "one")
    commit_file(env, repo, "one.txt", "one\n")
    git(env, repo, "checkout", "-qb", "two", "main")
    commit_file(env, repo, "two.txt", "two\n")
    git(env, repo, "checkout", "-q", "main")
    git(env, repo, "merge", "--no-commit", "--no-ff", "one", "two")
    (repo / "one.txt").write_text(f"one plus {TOKEN}\n")
    git(env, repo, "add", "one.txt")
    assert blocked(git(env, repo, "commit", "--no-edit", check=False))


def test_merge_in_a_linked_worktree_bringing_token_unchanged_passes(env, repo, tmp_path):
    git(env, repo, "checkout", "-qb", "side")
    commit_file(env, repo, "upstream.txt", f"published upstream: {TOKEN}\n", bypass=True)
    git(env, repo, "checkout", "-q", "main")
    wt = tmp_path / "wt"
    git(env, repo, "worktree", "add", "-q", "-b", "feature", str(wt), "main")
    commit_file(env, wt, "other.txt", "feature diverges\n")
    git(env, wt, "merge", "--no-commit", "--no-ff", "side")
    r = git(env, wt, "commit", "--no-edit", check=False)
    assert r.returncode == 0, r.stderr


def test_scrub_off_still_bypasses_a_merge(env, repo):
    git(env, repo, "checkout", "-qb", "side")
    commit_file(env, repo, "base.txt", "side version\n")
    git(env, repo, "checkout", "-q", "main")
    commit_file(env, repo, "base.txt", "main version\n")
    assert git(env, repo, "merge", "side", check=False).returncode != 0
    (repo / "base.txt").write_text(f"resolved with {TOKEN}\n")
    git(env, repo, "add", "base.txt")
    r = git(env, repo, "commit", "--no-edit", check=False, extra={"PRO_CONTENT_SCRUB": "off"})
    assert r.returncode == 0, r.stderr


# --- private keys, the public-repo list, ssh config modules (2026-10-29) -------------------

EXTRA = "zz-public-only-token-zz"
BIN = Path(__file__).resolve().parent.parent / "bin"


def make_repo(env, path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", f"--template={path.parent / 'none'}", str(path)], env=env, check=True)
    hooks = path / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").symlink_to(GUARD)
    (path / "base.txt").write_text("base\n")
    git(env, path, "add", "-A")
    git(env, path, "commit", "-qm", "base")
    return path


def fresh_private_key(tmp_path):
    k = tmp_path / "throwaway_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(k)], check=True)
    return k.read_text()


def test_private_key_is_blocked(env, repo, tmp_path):
    (repo / "id_test").write_text(fresh_private_key(tmp_path))
    git(env, repo, "add", "id_test")
    r = git(env, repo, "commit", "-qm", "oops", check=False)
    assert r.returncode != 0 and "private key" in r.stderr.lower()


def test_private_key_is_blocked_even_with_scrub_off(env, repo, tmp_path):
    (repo / "id_test").write_text(fresh_private_key(tmp_path))
    git(env, repo, "add", "id_test")
    r = git(env, repo, "commit", "-qm", "oops", check=False, extra={"PRO_CONTENT_SCRUB": "off"})
    assert r.returncode != 0 and "private key" in r.stderr.lower()


def test_public_key_passes(env, repo, tmp_path):
    fresh_private_key(tmp_path)
    (repo / "id_test.pub").write_text((tmp_path / "throwaway_key.pub").read_text())
    git(env, repo, "add", "id_test.pub")
    assert git(env, repo, "commit", "-qm", "pub", check=False).returncode == 0


@pytest.mark.parametrize("where,applies", [
    ("42/Private", False),      # a private repo under ~/42: the public list does not apply
    ("42/SpaceSuit", True),     # the public repo under ~/42
    ("elsewhere/WorkRepo", True),  # anything outside ~/42 (work, anon): applies
])
def test_public_repo_list_scope(env, tmp_path, where, applies):
    home = Path(env["HOME"])
    extra = home / ".config" / "git-content-scrub" / "spacesuit-extra.txt"
    extra.parent.mkdir(parents=True)
    extra.write_text(EXTRA + "\n")
    base = home if where.startswith("42/") else tmp_path
    r = make_repo(env, base / where)
    (r / "f.txt").write_text(f"mentions {EXTRA}\n")
    git(env, r, "add", "f.txt")
    res = git(env, r, "commit", "-qm", "x", check=False)
    assert blocked(res) == applies, res.stderr


GOOD_MODULE = """Host Box
    HostName box.example.ts.net
    HostKeyAlias box.example.ts.net
    IdentitiesOnly yes
    IdentityFile ~/.ssh/to/box
"""


def test_ssh_module_failing_lint_is_blocked(env, repo):
    env = {**env, "PATH": f"{BIN}:{env['PATH']}"}
    d = repo / "secrets" / "ssh" / "config.d"
    d.mkdir(parents=True)
    (d / "box.conf").write_text(GOOD_MODULE + "    ProxyCommand nc %h %p\n")
    git(env, repo, "add", "secrets")
    r = git(env, repo, "commit", "-qm", "module", check=False)
    assert r.returncode != 0 and "proxycommand" in r.stderr.lower()


def test_ssh_module_passing_lint_commits(env, repo):
    env = {**env, "PATH": f"{BIN}:{env['PATH']}"}
    d = repo / "secrets" / "ssh" / "config.d"
    d.mkdir(parents=True)
    (d / "box.conf").write_text(GOOD_MODULE)
    git(env, repo, "add", "secrets")
    r = git(env, repo, "commit", "-qm", "module", check=False)
    assert r.returncode == 0, r.stderr
