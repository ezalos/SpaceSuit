# tls Terminal View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `tls` zsh function with a dense terminal grid showing every tmux window plus, for Claude panes, the session title, blocked state, waiting duration and task progress — faster than the command it replaces.

**Architecture:** No new project. `tls` becomes a terminal renderer over `agents_dashboard`, which already computes this data under 153 tests. Four widenings there (a batched `ps`, richer tmux fields, non-Claude windows in the model, an opt-out phase scan) plus a pure renderer and a CLI entry point.

**Tech Stack:** Python 3.10+, stdlib only, `fire` for the CLI (already a repo dependency), `pytest` via `uv run`. No new dependencies.

**Spec:** `docs/plans/2026-08-04-tls-terminal-view-design.md`. Read it before starting.

## Global Constraints

- Python `>=3.10,<3.14` (Setup's `requires-python`).
- **Zero new dependencies.** Nothing added to `pyproject.toml`.
- Every code file starts with a 2-line `ABOUTME:` comment.
- Run tests with `uv run pytest` from `/home/ezalos/Setup`. Never bare `python`/`python3`.
- Never use `rm`; use `rip`.
- Never bypass pre-commit hooks. No `--no-verify`.
- **Strictly read-only toward tmux.** Only `tmux list-sessions`, `tmux list-panes`, `tmux capture-pane` and `ps` may ever run. This machine hosts ~20 live Claude sessions doing the owner's real work; `send-keys`, `kill-*`, `respawn-*`, `select-*`, `set-option`, `rename-*` are forbidden.
- **A live systemd service `agents-dashboard` runs on port 8770.** Do not stop, restart or disturb it, and never bind that port in a test.
- The full suite must pass after every task. It is 153 tests at the start of this plan.

## Baseline to beat

Measured 2026-08-04 on the live machine, 30 panes / 20 Claude sessions:

| | |
|---|---|
| today's `tls` | **0.59 s** |
| `collect()` | 1.75 s — of which `ps -t` per pane **1.048 s**, phase scan **0.434 s** |
| `uv` startup | 0.04 s |

Target for the new `tls`: **under 0.30 s**. Task 6 measures it.

## File structure

| File | Responsibility |
|---|---|
| `agents_dashboard/tmux.py` | *(modify)* batched pid map; richer pane fields |
| `agents_dashboard/models.py` | *(modify)* `WindowRecord`; `SessionCard.windows` |
| `agents_dashboard/collect.py` | *(modify)* build windows; `with_phase` flag |
| `agents_dashboard/termview.py` | *(create)* `Snapshot` → aligned, coloured text |
| `agents_dashboard/__main__.py` | *(modify)* `tls` CLI command |
| `dotfiles/bin/tls` | *(create)* wrapper the zsh function calls |
| `tests/test_agents_dashboard.py` | *(modify)* append tests per task |

---

### Task 1: Batched `ps` — one call instead of thirty

The single biggest win: 1.048 s of the 1.75 s collection is 30 `ps -t` spawns.

**Files:**
- Modify: `agents_dashboard/tmux.py`
- Modify: `tests/test_agents_dashboard.py` (append)

**Interfaces:**
- Consumes: `subprocess_runner`, `TmuxPane` (existing).
- Produces:
  - `claude_pids_by_tty(runner=subprocess_runner) -> dict[str, int]` — keys are **bare** ttys (`pts/5`), values pids.
  - `normalise_tty(tty: str) -> str` — strips a `/dev/` prefix.
  - `claude_pid_for_tty` is unchanged and stays for single lookups.

**Verified facts this task depends on:** `ps -eo pid=,tty=,comm=` prints the tty **without** a `/dev/` prefix (`pts/1`) and `?` for processes with no tty, while `tmux` reports `#{pane_tty}` **with** it (`/dev/pts/1`). Mismatching these yields an empty map and a dashboard showing zero Claude sessions — hence `normalise_tty` and a test for exactly that.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents_dashboard.py`:

```python
class TestClaudePidsByTty:
    def test_indexes_claude_processes_by_bare_tty(self):
        runner = FakeRunner({"ps": "  17313 pts/1    claude\n  17318 pts/2    claude\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {"pts/1": 17313, "pts/2": 17318}

    def test_ignores_non_claude_processes(self):
        runner = FakeRunner({"ps": "  1 ?        systemd\n  99 pts/1    zsh\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {}

    def test_matches_an_absolute_path_to_claude(self):
        runner = FakeRunner({"ps": "  42 pts/3    /usr/local/bin/claude\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {"pts/3": 42}

    def test_does_not_match_a_lookalike(self):
        runner = FakeRunner({"ps": "  42 pts/3    claude-log\n  43 pts/4    /usr/bin/claude-log\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {}

    def test_first_pid_wins_for_a_shared_tty(self):
        # Matches claude_pid_for_tty, which returns the first match. A nested
        # claude on the same tty must not displace the one already recorded.
        runner = FakeRunner({"ps": "  100 pts/1    claude\n  200 pts/1    claude\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {"pts/1": 100}

    def test_skips_malformed_lines_without_raising(self):
        runner = FakeRunner({"ps": "garbage\n\n  42 pts/3    claude\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {"pts/3": 42}

    def test_no_ps_output_returns_empty(self):
        assert tmux.claude_pids_by_tty(runner=FakeRunner({})) == {}


class TestNormaliseTty:
    def test_strips_the_dev_prefix_tmux_reports(self):
        # tmux says /dev/pts/5, ps says pts/5. Getting this wrong yields an
        # empty map and a dashboard showing no Claude sessions at all.
        assert tmux.normalise_tty("/dev/pts/5") == "pts/5"

    def test_leaves_an_already_bare_tty_alone(self):
        assert tmux.normalise_tty("pts/5") == "pts/5"

    def test_a_map_built_from_ps_is_reachable_with_a_tmux_tty(self):
        runner = FakeRunner({"ps": "  42 pts/5    claude\n"})
        pids = tmux.claude_pids_by_tty(runner=runner)
        assert pids.get(tmux.normalise_tty("/dev/pts/5")) == 42
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q -k "ClaudePidsByTty or NormaliseTty"`
Expected: FAIL — `AttributeError: module 'agents_dashboard.tmux' has no attribute 'claude_pids_by_tty'`

- [ ] **Step 3: Implement**

Add to `agents_dashboard/tmux.py`:

```python
def normalise_tty(tty: str) -> str:
    """tmux reports /dev/pts/5; ps reports pts/5. Normalise to the ps form."""
    return tty.removeprefix("/dev/")


def claude_pids_by_tty(runner=subprocess_runner) -> dict[str, int]:
    """Map every tty running Claude to its pid, in one `ps`.

    Replaces one `ps -t` per pane. Profiled over 30 panes, the per-pane form
    cost 1.048 s - about 60% of a full collection - because each call is a
    process spawn. One `ps -eo` costs roughly 0.03 s.

    Keys are bare ttys (`pts/5`), the form `ps` prints. Callers holding a tmux
    tty must pass it through `normalise_tty` first.
    """
    out = runner(["ps", "-eo", "pid=,tty=,comm="])
    found: dict[str, int] = {}
    for line in out.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3:
            continue
        pid_text, tty, comm = fields[0], fields[1], fields[2].strip()
        if comm != "claude" and not comm.endswith("/claude"):
            continue
        if tty in found:
            continue  # first wins, matching claude_pid_for_tty
        try:
            found[tty] = int(pid_text)
        except ValueError:
            continue
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q`
Expected: PASS, 153 + 10 = 163 tests.

- [ ] **Step 5: Check the map against the real machine**

Run:
```bash
cd ~/Setup && uv run python -c "
from agents_dashboard import tmux
pids = tmux.claude_pids_by_tty()
panes = tmux.list_panes()
hit = sum(1 for p in panes if tmux.normalise_tty(p.tty) in pids)
old = sum(1 for p in panes if tmux.claude_pid_for_tty(p.tty))
print(f'batched map: {hit} claude panes | per-pane ps: {old}')
assert hit == old, 'the two methods disagree'
print('agree')
"
```
Expected: both counts equal and non-zero. A disagreement means the parse is wrong — fix before continuing, because every later task trusts this map.

- [ ] **Step 6: Commit**

```bash
cd ~/Setup
git add agents_dashboard/tmux.py tests/test_agents_dashboard.py
git commit -m "perf(agents-dashboard): index claude pids with one ps instead of one per pane"
```

---

### Task 2: Richer tmux pane fields

The grid needs the pane's command, when it last drew output, and its session's attached state and activity. All are available from the `list-panes` call already being made.

**Files:**
- Modify: `agents_dashboard/tmux.py`
- Modify: `tests/test_agents_dashboard.py` (append)

**Interfaces:**
- Produces: `TmuxPane` gains four fields, all **with defaults, appended last**: `command: str = ""`, `quiet_since: float = 0.0`, `session_attached: bool = False`, `session_activity: float = 0.0`.

**Why defaults matter:** existing tests construct `TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")` positionally. Appending defaulted fields keeps every one of those working. Do not reorder the existing five.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents_dashboard.py`:

```python
class TestListPanesRicherFields:
    LINE = ("alfred@2026-08-01:1:0:/home/ezalos/42/Alfred:/dev/pts/5"
            ":1785833806:1:1785833791:claude")

    def test_parses_the_new_fields(self):
        panes = tmux.list_panes(runner=FakeRunner({"list-panes": self.LINE + "\n"}))
        assert len(panes) == 1
        p = panes[0]
        assert p.session == "alfred@2026-08-01"
        assert p.window_index == 1
        assert p.cwd == "/home/ezalos/42/Alfred"
        assert p.tty == "/dev/pts/5"
        assert p.command == "claude"
        assert p.quiet_since == 1785833806.0
        assert p.session_attached is True
        assert p.session_activity == 1785833791.0

    def test_detached_session_parses_as_false(self):
        line = self.LINE.replace(":1:1785833791:claude", ":0:1785833791:claude")
        assert tmux.list_panes(runner=FakeRunner({"list-panes": line + "\n"}))[0].session_attached is False

    def test_session_name_containing_colons_survives(self):
        # rsplit from the right: only the trailing fields have fixed positions.
        line = "we:ird:name:2:0:/tmp:/dev/pts/9:1785833806:0:1785833791:zsh"
        p = tmux.list_panes(runner=FakeRunner({"list-panes": line + "\n"}))[0]
        assert p.session == "we:ird:name"
        assert p.command == "zsh"

    def test_malformed_line_is_skipped_not_fatal(self):
        runner = FakeRunner({"list-panes": "garbage\n" + self.LINE + "\n"})
        assert len(tmux.list_panes(runner=runner)) == 1

    def test_defaults_keep_positional_construction_working(self):
        # Earlier tasks' tests build TmuxPane with five positional args.
        p = tmux.TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")
        assert p.command == "" and p.quiet_since == 0.0
        assert p.session_attached is False and p.session_activity == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q -k ListPanesRicherFields`
Expected: FAIL — the new fields do not exist and the format string yields the old five.

- [ ] **Step 3: Implement**

In `agents_dashboard/tmux.py`, replace the format constant and widen the dataclass and parser:

```python
# Trailing fields have fixed positions so the parser can rsplit; the session
# name is first because it is the only field that may itself contain ':'.
PANE_FORMAT = (
    "#{session_name}:#{window_index}:#{pane_index}:#{pane_current_path}"
    ":#{pane_tty}:#{window_activity}:#{session_attached}:#{session_activity}"
    ":#{pane_current_command}"
)
_PANE_FIELDS = 9


@dataclass
class TmuxPane:
    session: str
    window_index: int
    pane_index: int
    cwd: str
    tty: str
    # Appended with defaults so existing positional construction keeps working.
    command: str = ""
    quiet_since: float = 0.0        # epoch seconds, last output in this window
    session_attached: bool = False
    session_activity: float = 0.0   # epoch seconds, last activity in the session


def list_panes(runner=subprocess_runner) -> list[TmuxPane]:
    out = runner(["tmux", "list-panes", "-a", "-F", PANE_FORMAT])
    panes = []
    for line in out.splitlines():
        # rsplit: session names may contain ':' but the trailing fields cannot.
        parts = line.rsplit(":", _PANE_FIELDS - 1)
        if len(parts) != _PANE_FIELDS:
            continue
        session, window, pane, cwd, tty, activity, attached, sess_activity, command = parts
        try:
            panes.append(
                TmuxPane(
                    session=session,
                    window_index=int(window),
                    pane_index=int(pane),
                    cwd=cwd,
                    tty=tty,
                    command=command,
                    quiet_since=float(activity),
                    session_attached=attached == "1",
                    session_activity=float(sess_activity),
                )
            )
        except ValueError:
            continue
    return panes
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q`
Expected: PASS. Existing `TestListPanes` tests use the old 5-field line and will now be skipped by the length check — if any of them fail, update those fixtures to the 9-field form rather than loosening the parser.

- [ ] **Step 5: Verify against the real tmux server**

```bash
cd ~/Setup && uv run python -c "
from agents_dashboard import tmux
ps = tmux.list_panes()
print(f'{len(ps)} panes')
bad = [p for p in ps if not p.command or p.quiet_since == 0.0]
print('panes missing command/activity:', len(bad))
print('attached sessions:', sorted({p.session.split(\"@\")[0] for p in ps if p.session_attached}))
"
```
Expected: pane count matches `tmux list-panes -a | wc -l`, zero missing fields, and the attached list matches the sessions you actually have open.

- [ ] **Step 6: Commit**

```bash
cd ~/Setup
git add agents_dashboard/tmux.py tests/test_agents_dashboard.py
git commit -m "feat(agents-dashboard): carry pane command, quiet-since and session attach state"
```

---

### Task 3: Non-Claude windows enter the model

The riskiest task. `SessionCard.panes` becomes a derived property, which means `card.panes.append(...)` and `card.panes.sort(...)` would silently mutate a throwaway list — so the model change and the collector change must land together.

**Files:**
- Modify: `agents_dashboard/models.py`
- Modify: `agents_dashboard/collect.py`
- Modify: `tests/test_agents_dashboard.py` (append)

**Interfaces:**
- Produces:
  - `WindowRecord` dataclass: `window_index: int`, `pane_index: int`, `command: str`, `cwd: str`, `quiet_since: float`, `claude: PaneRecord | None = None`.
  - `SessionCard.windows: list[WindowRecord]`, `SessionCard.attached: bool = False`, `SessionCard.activity: float = 0.0`.
  - `SessionCard.panes` — **property**, returns Claude panes ordered by urgency then longest wait.
  - `SessionCard.not_started` — unchanged meaning, now derived from the property.

**The ordering split, stated once so it is not re-litigated:** `windows` stays in numeric window/pane order, which is what a terminal listing needs. `panes` returns urgency-ordered, which is what the web dashboard needs and what its existing tests assert. Each consumer reads the collection that matches its job, and neither sorts the other's.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents_dashboard.py`:

```python
class TestWindowRecordModel:
    def test_panes_property_returns_only_claude_windows(self):
        claude = PaneRecord(session_id="a", tmux_session="s", window_index=1,
                            pane_index=0, cwd="/tmp")
        card = SessionCard(name="s", windows=[
            WindowRecord(0, 0, "zsh", "/tmp", 100.0, None),
            WindowRecord(1, 0, "claude", "/tmp", 100.0, claude),
        ])
        assert card.panes == [claude]

    def test_not_started_is_true_when_no_window_runs_claude(self):
        card = SessionCard(name="s", windows=[WindowRecord(0, 0, "zsh", "/tmp", 100.0, None)])
        assert card.not_started is True

    def test_not_started_is_false_with_a_claude_window(self):
        claude = PaneRecord(session_id="a", tmux_session="s", window_index=0,
                            pane_index=0, cwd="/tmp")
        card = SessionCard(name="s", windows=[WindowRecord(0, 0, "claude", "/tmp", 100.0, claude)])
        assert card.not_started is False

    def test_panes_is_ordered_by_urgency_not_window_index(self):
        # The web dashboard depends on this ordering; the terminal view reads
        # `windows` instead, which stays in numeric order.
        idle = PaneRecord(session_id="i", tmux_session="s", window_index=0, pane_index=0,
                          cwd="/tmp", activity=Activity.WAITING,
                          waiting_reason=WaitingReason.IDLE, waiting_since=500.0)
        blocked = PaneRecord(session_id="b", tmux_session="s", window_index=9, pane_index=0,
                             cwd="/tmp", activity=Activity.WAITING,
                             waiting_reason=WaitingReason.PERMISSION, waiting_since=500.0)
        card = SessionCard(name="s", windows=[
            WindowRecord(0, 0, "claude", "/tmp", 100.0, idle),
            WindowRecord(9, 0, "claude", "/tmp", 100.0, blocked),
        ])
        assert [p.session_id for p in card.panes] == ["b", "i"]
        assert [w.window_index for w in card.windows] == [0, 9]
```

- [ ] **Step 2: Write the failing collector tests**

Also append:

```python
class TestSnapshotCarriesAllWindows:
    def test_non_claude_windows_appear_in_the_snapshot(self):
        panes = [
            tmux.TmuxPane("s", 0, 0, "/home/ezalos/Setup", "/dev/pts/1",
                          command="zsh", quiet_since=900.0),
            tmux.TmuxPane("s", 1, 0, "/home/ezalos/Setup", "/dev/pts/2",
                          command="claude", quiet_since=950.0),
        ]
        sessions = {42: ClaudeSession(42, "sid", "/home/ezalos/Setup", "idle", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/2": 42})
        card = snap.cards[0]
        assert [w.command for w in card.windows] == ["zsh", "claude"]
        assert card.windows[0].claude is None
        assert card.windows[1].claude is not None
        assert len(card.panes) == 1

    def test_window_carries_its_own_cwd_and_quiet_time(self):
        panes = [tmux.TmuxPane("s", 0, 0, "/home/ezalos/42/Alfred", "/dev/pts/1",
                               command="zsh", quiet_since=900.0)]
        card = build(panes, {}, {}).cards[0]
        assert card.windows[0].cwd == "/home/ezalos/42/Alfred"
        assert card.windows[0].quiet_since == 900.0

    def test_windows_are_in_numeric_order(self):
        panes = [
            tmux.TmuxPane("s", 11, 0, "/tmp", "/dev/pts/3", command="zsh", quiet_since=1.0),
            tmux.TmuxPane("s", 2, 0, "/tmp", "/dev/pts/1", command="zsh", quiet_since=1.0),
        ]
        assert [w.window_index for w in build(panes, {}, {}).cards[0].windows] == [2, 11]

    def test_card_records_attached_state_and_activity(self):
        panes = [tmux.TmuxPane("s", 0, 0, "/tmp", "/dev/pts/1", command="zsh",
                               quiet_since=1.0, session_attached=True, session_activity=777.0)]
        card = build(panes, {}, {}).cards[0]
        assert card.attached is True
        assert card.activity == 777.0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q -k "WindowRecordModel or SnapshotCarriesAllWindows"`
Expected: FAIL — `WindowRecord` does not exist.

- [ ] **Step 4: Widen the model**

In `agents_dashboard/models.py`, add `WindowRecord` and replace `SessionCard`:

```python
@dataclass
class WindowRecord:
    """One tmux window/pane, with its Claude session attached when it has one."""

    window_index: int
    pane_index: int
    command: str
    cwd: str
    quiet_since: float  # epoch seconds, when this pane last drew output
    claude: PaneRecord | None = None


@dataclass
class SessionCard:
    """One tmux session and every window inside it."""

    name: str
    windows: list[WindowRecord] = field(default_factory=list)
    attached: bool = False
    activity: float = 0.0  # epoch seconds, session_activity

    @property
    def panes(self) -> list[PaneRecord]:
        """Claude panes, most urgent first.

        A property, not a field: `windows` is the single source of truth. The
        ordering differs deliberately from `windows` - the web dashboard wants
        worst-first, a terminal listing wants numeric order, so each consumer
        reads the collection that matches its job.

        Because this returns a new list, it must never be mutated. Build
        `windows` instead.
        """
        claude = [w.claude for w in self.windows if w.claude is not None]
        claude.sort(key=lambda p: (urgency_rank(p.waiting_reason), p.waiting_since or 0.0))
        return claude

    @property
    def not_started(self) -> bool:
        """A tmux session with no Claude in any window."""
        return not self.panes
```

`urgency_rank` lives in `classify.py`. Importing it into `models.py` would create a cycle (`classify` imports `models`), so move the sort key inline: order by `(WaitingReason` position, `waiting_since)` using a module-level tuple in `models.py`:

```python
# Local copy of the urgency order. classify.py imports models, so models
# cannot import classify; this tuple is the one duplicated fact, and
# test_urgency_order_matches_classify below pins the two together.
_URGENCY_ORDER = (
    WaitingReason.PERMISSION,
    WaitingReason.QUESTION,
    WaitingReason.UNSENT_INPUT,
    WaitingReason.IDLE,
)


def _pane_urgency(pane: "PaneRecord") -> tuple[int, float]:
    reason = pane.waiting_reason
    rank = _URGENCY_ORDER.index(reason) if reason in _URGENCY_ORDER else len(_URGENCY_ORDER)
    return (rank, pane.waiting_since or 0.0)
```

and use `claude.sort(key=_pane_urgency)`.

- [ ] **Step 5: Pin the duplicated urgency order**

Append this test — a duplicated constant that can drift silently is worse than the import cycle it avoids:

```python
def test_urgency_order_matches_classify():
    from agents_dashboard.classify import URGENCY, urgency_rank
    from agents_dashboard.models import _URGENCY_ORDER
    assert list(_URGENCY_ORDER) == sorted(URGENCY, key=urgency_rank)
```

- [ ] **Step 6: Rebuild the collector around windows**

In `agents_dashboard/collect.py`, `build_snapshot` currently does `card.panes.append(...)` and sorts `card.panes`. Both must go — the property forbids mutation. Replace the body with:

```python
def build_snapshot(
    now, panes, sessions, pid_lookup, transcript_reader, pane_capturer
) -> Snapshot:
    by_pid = dict(sessions)
    cards: dict[str, SessionCard] = {}

    for pane in panes:
        card = cards.setdefault(pane.session, SessionCard(name=pane.session))
        # Session-level facts arrive on every pane; last one wins, they agree.
        card.attached = pane.session_attached
        card.activity = pane.session_activity

        record = None
        pid = pid_lookup(pane.tty)
        session = by_pid.get(pid) if pid is not None else None
        if session is not None:
            info = transcript_reader(session)
            activity = map_activity(session.status)

            reason = None
            if activity is Activity.WAITING:
                # Only ever computed for waiting sessions: text in the prompt
                # box while the agent works is type-ahead, not a dropped
                # thread. This guard is also why the fragile pane capture
                # rarely runs.
                text = pane_capturer(pane.session, pane.window_index, pane.pane_index)
                reason = panescan.scan(text) or (
                    WaitingReason.QUESTION if info.asked_question else WaitingReason.IDLE
                )

            phase, evidence = classify_phase_with_evidence(info.signals, info.mode)
            record = PaneRecord(
                session_id=session.session_id,
                tmux_session=pane.session,
                window_index=pane.window_index,
                pane_index=pane.pane_index,
                cwd=session.cwd or pane.cwd,
                phase=phase,
                phase_evidence=evidence,
                activity=activity,
                waiting_reason=reason,
                waiting_since=session.status_updated_at if reason else None,
                tasks=info.tasks,
                title=info.title or session.name,
                model=info.model,
                git_branch=info.git_branch,
            )

        card.windows.append(
            WindowRecord(
                window_index=pane.window_index,
                pane_index=pane.pane_index,
                command=pane.command,
                cwd=pane.cwd,
                quiet_since=pane.quiet_since,
                claude=record,
            )
        )

    working_rank = urgency_rank(None)

    def card_key(card: SessionCard):
        if not card.panes:
            return (2, 0, 0.0)  # not-started cards sort last
        best = min(urgency_rank(p.waiting_reason) for p in card.panes)
        oldest = min((p.waiting_since or now) for p in card.panes)
        return (0 if best < working_rank else 1, best, oldest)

    for card in cards.values():
        card.windows.sort(key=lambda w: (w.window_index, w.pane_index))

    return Snapshot(generated_at=now, cards=sorted(cards.values(), key=card_key))
```

Keep the exact `classify_phase_with_evidence`, `info.tasks` and any other fields the current file already sets — copy them across rather than dropping them. If the current `build_snapshot` sets a field not shown above, it stays.

- [ ] **Step 7: Run the full suite**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q`
Expected: PASS. **If any pre-existing test fails, do not weaken it** — the property is meant to preserve every current behaviour. A failure means the widening changed semantics; fix the widening.

- [ ] **Step 8: Confirm the web dashboard is unchanged**

```bash
cd ~/Setup && uv run python -c "
from agents_dashboard.collect import collect
from agents_dashboard.render import render
snap = collect()
html = render(snap)
print('cards:', len(snap.cards), '| claude panes:', sum(len(c.panes) for c in snap.cards))
print('windows total:', sum(len(c.windows) for c in snap.cards))
print('html bytes:', len(html))
"
```
Expected: claude-pane count matches what the dashboard showed before (about 20–23), windows total matches `tmux list-panes -a | wc -l` (about 30), and the HTML still renders.

- [ ] **Step 9: Commit**

```bash
cd ~/Setup
git add agents_dashboard/models.py agents_dashboard/collect.py tests/test_agents_dashboard.py
git commit -m "feat(agents-dashboard): carry every tmux window, not only Claude panes"
```

---

### Task 4: Make the phase scan optional

`tls` does not show phase by default, and the scan costs 0.434 s.

**Files:**
- Modify: `agents_dashboard/collect.py`
- Modify: `tests/test_agents_dashboard.py` (append)

**Interfaces:**
- Produces: `collect(now: float | None = None, with_phase: bool = True) -> Snapshot`. Default stays `True`, so the web dashboard and every existing caller are unaffected.

- [ ] **Step 1: Write the failing tests**

```python
class TestCollectWithPhaseFlag:
    def test_with_phase_false_skips_the_deep_scan(self, monkeypatch):
        deep = self._stub_sources(monkeypatch)
        collect_mod.collect(with_phase=False)
        assert deep == [], "the 4 MB scan must not run when phase is not shown"

    def test_with_phase_true_performs_the_deep_scan(self, monkeypatch):
        deep = self._stub_sources(monkeypatch)
        collect_mod.collect(with_phase=True)
        assert deep, "the phase scan must still run when phase is requested"

    def test_default_is_with_phase(self, monkeypatch):
        # The web dashboard relies on the default; flipping it would silently
        # blank its phase column.
        deep = self._stub_sources(monkeypatch)
        collect_mod.collect()
        assert deep

    def test_skipping_the_scan_does_not_invent_a_phase(self, monkeypatch):
        self._stub_sources(monkeypatch)
        snap = collect_mod.collect(with_phase=False)
        phases = {p.phase for c in snap.cards for p in c.panes}
        assert phases <= {Phase.UNKNOWN}
```

The helper that makes these deterministic — **every source is stubbed, so the
tests never touch the live machine**. An earlier draft of this plan called the
real `collect()`, which would have failed on any machine with no Claude session
running and passed for the wrong reason on this one:

```python
    def _stub_sources(self, monkeypatch):
        """Stub every source so collect() is deterministic. Returns a list that
        records each read_for_phase call."""
        deep = []
        pane = tmux.TmuxPane("s", 0, 0, "/tmp", "/dev/pts/1",
                             command="claude", quiet_since=900.0)
        session = ClaudeSession(42, "sid", "/tmp", "idle", 900.0, "n")
        monkeypatch.setattr(collect_mod.tmux, "list_panes", lambda **k: [pane])
        monkeypatch.setattr(collect_mod.tmux, "claude_pids_by_tty",
                            lambda **k: {"pts/1": 42})
        monkeypatch.setattr(collect_mod.tmux, "capture_pane", lambda *a, **k: "")
        monkeypatch.setattr(collect_mod.claude_sessions, "load_all", lambda **k: {42: session})
        monkeypatch.setattr(collect_mod.claude_sessions, "find_transcript",
                            lambda *a, **k: Path("/nonexistent.jsonl"))
        monkeypatch.setattr(collect_mod.transcripts, "read_tail", lambda *a, **k: [])
        monkeypatch.setattr(collect_mod.transcripts, "read", lambda *a, **k: transcripts.TranscriptInfo())
        monkeypatch.setattr(collect_mod.transcripts, "read_for_phase",
                            lambda *a, **k: deep.append(1) or transcripts.TranscriptInfo())
        return deep
```

Add `from agents_dashboard import collect as collect_mod` and `from pathlib import Path` to the imports at the point these tests are appended.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q -k CollectWithPhaseFlag`
Expected: FAIL — `collect() got an unexpected keyword argument 'with_phase'`

- [ ] **Step 3: Implement**

In `agents_dashboard/collect.py`:

```python
def collect(now: float | None = None, with_phase: bool = True) -> Snapshot:
    """Wire the real sources together.

    `with_phase=False` skips the 4 MB phase scan, which profiled at 0.434 s
    across 20 sessions. The terminal view does not show phase by default, so
    it opts out; the web dashboard keeps the default.
    """
    sessions = claude_sessions.load_all()
    pids = tmux.claude_pids_by_tty()

    def read_transcript(session):
        path = claude_sessions.find_transcript(session)
        if with_phase:
            info = transcripts.read_for_phase(path)
        else:
            info = transcripts.read(path)
        info.asked_question = detect_question(transcripts.read_tail(path))
        return info

    return build_snapshot(
        now=now if now is not None else time.time(),
        panes=tmux.list_panes(),
        sessions=sessions,
        pid_lookup=lambda tty: pids.get(tmux.normalise_tty(tty)),
        transcript_reader=read_transcript,
        pane_capturer=tmux.capture_pane,
    )
```

Note this also swaps the per-pane `ps` for the batched map built in Task 1 — that is the 1.048 s win landing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q`
Expected: PASS.

- [ ] **Step 5: Measure the win**

```bash
cd ~/Setup && python3 -c "
import subprocess, time
for label, arg in (('collect() with phase','True'), ('collect(with_phase=False)','False')):
    s=time.time()
    subprocess.run(['uv','run','python','-c',
        f'from agents_dashboard.collect import collect; collect(with_phase={arg})'],
        capture_output=True)
    print(f'{label:<28} {time.time()-s:5.2f}s')
"
```
Expected: with phase well under the 1.75 s baseline (target ~0.7 s), without phase lower still (~0.25 s). Report both. If the with-phase figure has not improved, the batched map is not actually being used.

- [ ] **Step 6: Commit**

```bash
cd ~/Setup
git add agents_dashboard/collect.py tests/test_agents_dashboard.py
git commit -m "perf(agents-dashboard): batched pid lookup and an opt-out phase scan"
```

---

### Task 5: The terminal renderer

**Files:**
- Create: `agents_dashboard/termview.py`
- Modify: `tests/test_agents_dashboard.py` (append)

**Interfaces:**
- Produces: `render_terminal(snapshot: Snapshot, width: int = 100, color: bool = True, show_phase: bool = False, now: float | None = None) -> str`

Pure: no `isatty`, no `os.get_terminal_size`, no `time.time()` unless `now` is omitted. The caller resolves all three and passes them in, which is what makes alignment and colour testable without a pty.

**Target layout**, from the spec:

```
   WIN   CMD      QUIET  STATE       WAITING  TASKS  TITLE
  alfred (attached)
   0.0   claude     15h  ○ idle         15h  12/12  Build personal agent with calendar…
   1.0   claude      0s  ◉ working        ·    8/9  Create dashboard for Claude sessio…
   2.0   zsh         9m  ·                ·      ·  ~/42/Alfred
```

- [ ] **Step 1: Write the failing tests**

```python
class TestTermView:
    def _snap(self, windows, name="alfred", attached=True, now=1000.0):
        return Snapshot(generated_at=now,
                        cards=[SessionCard(name=name, windows=windows, attached=attached)])

    def _claude(self, **kw):
        base = dict(session_id="a", tmux_session="alfred", window_index=0, pane_index=0,
                    cwd="/tmp", title="Build personal agent")
        base.update(kw)
        return PaneRecord(**base)

    def test_non_claude_row_shows_cwd_with_home_collapsed(self):
        w = [WindowRecord(2, 0, "zsh", "/home/ezalos/42/Alfred", 940.0, None)]
        out = termview.render_terminal(self._snap(w), color=False, now=1000.0)
        assert "~/42/Alfred" in out
        assert "/home/ezalos" not in out

    def test_non_claude_row_has_no_claude_columns(self):
        w = [WindowRecord(2, 0, "zsh", "/tmp", 940.0, None)]
        line = [l for l in termview.render_terminal(self._snap(w), color=False).splitlines()
                if "zsh" in l][0]
        assert "idle" not in line and "working" not in line

    def test_claude_row_shows_title_state_and_both_clocks(self):
        pane = self._claude(activity=Activity.WAITING, waiting_reason=WaitingReason.UNSENT_INPUT,
                            waiting_since=1000.0 - 7 * 3600)
        w = [WindowRecord(3, 0, "claude", "/tmp", 1000.0 - 300, pane)]
        line = [l for l in termview.render_terminal(self._snap(w), color=False, now=1000.0)
                .splitlines() if "claude" in l][0]
        assert "5m" in line      # QUIET
        assert "unsent" in line  # STATE
        assert "7h" in line      # WAITING
        assert "Build personal agent" in line

    def test_task_progress_renders_as_completed_over_total(self):
        pane = self._claude(tasks=TaskProgress(known=True, total=9, completed=8))
        w = [WindowRecord(1, 0, "claude", "/tmp", 1000.0, pane)]
        assert "8/9" in termview.render_terminal(self._snap(w), color=False)

    def test_unknown_task_progress_renders_a_placeholder_not_a_zero(self):
        pane = self._claude(tasks=TaskProgress(known=False))
        w = [WindowRecord(1, 0, "claude", "/tmp", 1000.0, pane)]
        out = termview.render_terminal(self._snap(w), color=False)
        assert "0/0" not in out

    def test_session_header_shows_attached_state(self):
        w = [WindowRecord(0, 0, "zsh", "/tmp", 1000.0, None)]
        assert "(attached)" in termview.render_terminal(self._snap(w, attached=True), color=False)
        assert "(detached)" in termview.render_terminal(self._snap(w, attached=False), color=False)

    def test_no_ansi_when_color_is_false(self):
        pane = self._claude(activity=Activity.WAITING, waiting_reason=WaitingReason.PERMISSION,
                            waiting_since=900.0)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        assert "\x1b[" not in termview.render_terminal(self._snap(w), color=False)

    def test_ansi_present_when_color_is_true(self):
        pane = self._claude(activity=Activity.WAITING, waiting_reason=WaitingReason.PERMISSION,
                            waiting_since=900.0)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        assert "\x1b[" in termview.render_terminal(self._snap(w), color=True)

    def test_columns_align_across_rows(self):
        pane = self._claude(title="short")
        w = [WindowRecord(0, 0, "zsh", "/tmp", 1000.0, None),
             WindowRecord(11, 0, "claude", "/tmp", 1000.0, pane)]
        rows = [l for l in termview.render_terminal(self._snap(w), color=False).splitlines()
                if "zsh" in l or "claude" in l]
        assert len({l.index("  ", 6) for l in rows}) == 1  # same first column boundary

    def test_title_is_truncated_to_the_given_width(self):
        pane = self._claude(title="x" * 400)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        for width in (80, 120, 200):
            out = termview.render_terminal(self._snap(w), width=width, color=False)
            assert all(len(line) <= width for line in out.splitlines())

    def test_wide_characters_do_not_break_the_width_budget(self):
        # Titles are model-generated and may contain CJK or emoji, which are
        # two columns wide but one character long. len() would overflow the row.
        pane = self._claude(title="日本語のタイトル" * 20)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        out = termview.render_terminal(self._snap(w), width=100, color=False)
        assert all(termview.display_width(line) <= 100 for line in out.splitlines())

    def test_blank_line_between_sessions(self):
        a = SessionCard(name="alfred", windows=[WindowRecord(0, 0, "zsh", "/tmp", 1.0, None)])
        b = SessionCard(name="setup", windows=[WindowRecord(0, 0, "zsh", "/tmp", 1.0, None)])
        out = termview.render_terminal(Snapshot(generated_at=1.0, cards=[a, b]), color=False)
        assert "\n\n" in out

    def test_phase_column_only_when_requested(self):
        pane = self._claude(phase=Phase.WRAP_UP)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        assert "wrap" not in termview.render_terminal(self._snap(w), color=False)
        assert "wrap" in termview.render_terminal(self._snap(w), color=False, show_phase=True)

    def test_guessed_phase_is_marked_with_a_question_mark(self):
        pane = self._claude(phase=Phase.IMPLEM, phase_evidence=PhaseEvidence.EDITS)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        out = termview.render_terminal(self._snap(w), color=False, show_phase=True)
        assert "implem?" in out

    def test_empty_snapshot_renders_without_raising(self):
        assert isinstance(termview.render_terminal(Snapshot(generated_at=1.0, cards=[]),
                                                   color=False), str)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q -k TermView`
Expected: FAIL — `cannot import name 'termview'`

- [ ] **Step 3: Implement**

Create `agents_dashboard/termview.py`:

```python
# ABOUTME: Render a Snapshot as an aligned, coloured terminal grid for `tls`.
# ABOUTME: Pure - the caller resolves width, colour and clock and passes them in.

from __future__ import annotations

import time
import unicodedata

from .models import (
    Activity,
    PhaseEvidence,
    SessionCard,
    Snapshot,
    WaitingReason,
    WindowRecord,
)

RESET = "\x1b[0m"
BOLD_CYAN = "\x1b[1;36m"
GREEN = "\x1b[32m"
AMBER = "\x1b[33m"
RED = "\x1b[31m"
BLUE = "\x1b[34m"
DIM = "\x1b[2m"
DIM_RED = "\x1b[2;31m"
BRIGHT = "\x1b[1m"

# Idle is deliberately not grey: it IS waiting on Louis, just not blocked, so
# it should register without competing with the warning states.
STATE_STYLE = {
    WaitingReason.PERMISSION: (RED, "⚠ permission"),
    WaitingReason.QUESTION: (AMBER, "⚠ question"),
    WaitingReason.UNSENT_INPUT: (BLUE, "⚠ unsent"),
    WaitingReason.IDLE: (AMBER, "○ idle"),
}
WORKING_STYLE = (GREEN, "◉ working")
NONE_CELL = "·"

HEADERS = ("WIN", "CMD", "QUIET", "STATE", "WAITING", "TASKS", "TITLE")


def display_width(text: str) -> int:
    """Columns a string occupies, counting CJK and emoji as two.

    Titles come from `aiTitle` and routinely contain both; len() would
    under-count and overflow the row.
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _truncate(text: str, budget: int) -> str:
    if display_width(text) <= budget:
        return text
    out, used = [], 0
    for char in text:
        w = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + w > budget - 1:
            break
        out.append(char)
        used += w
    return "".join(out) + "…"


def _pad(text: str, width: int, right: bool = False) -> str:
    gap = max(0, width - display_width(text))
    return (" " * gap + text) if right else (text + " " * gap)


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _colour(text: str, style: str, color: bool) -> str:
    return f"{style}{text}{RESET}" if color and style else text


def _collapse_home(path: str) -> str:
    import os

    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def _waiting_style(age: float) -> str:
    """Warms with age: a five-minute wait and a fifteen-hour one differ."""
    if age >= 86400:
        return DIM_RED
    if age >= 43200:
        return BRIGHT
    return ""


def _row(window: WindowRecord, now: float, color: bool, show_phase: bool,
         title_budget: int) -> str:
    target = f"{window.window_index}.{window.pane_index}"
    quiet = _duration(now - window.quiet_since) if window.quiet_since else NONE_CELL
    pane = window.claude

    if pane is None:
        cells = [_pad(target, 5, right=True), _pad(window.command, 8),
                 _pad(quiet, 5, right=True)]
        if show_phase:
            cells.append(_pad(NONE_CELL, 8))
        cells += [_pad(NONE_CELL, 11), _pad(NONE_CELL, 7, right=True),
                  _pad(NONE_CELL, 5, right=True)]
        title = _truncate(_collapse_home(window.cwd), title_budget)
        line = "  ".join(cells) + "  " + title
        return _colour(line, DIM, color)

    if pane.activity is Activity.WORKING or pane.waiting_reason is None:
        style, label = WORKING_STYLE
        waiting = NONE_CELL
        wait_style = ""
    else:
        style, label = STATE_STYLE[pane.waiting_reason]
        age = now - (pane.waiting_since or now)
        waiting = _duration(age)
        wait_style = _waiting_style(age)

    tasks = f"{pane.tasks.completed}/{pane.tasks.total}" if pane.tasks.known else NONE_CELL

    cells = [_pad(target, 5, right=True), _pad(window.command, 8),
             _pad(quiet, 5, right=True)]
    if show_phase:
        mark = "?" if pane.phase_evidence is PhaseEvidence.EDITS else ""
        cells.append(_pad(pane.phase.value + mark, 8))
    cells += [
        _colour(_pad(label, 11), style, color),
        _colour(_pad(waiting, 7, right=True), wait_style, color),
        _pad(tasks, 5, right=True),
    ]
    return "  ".join(cells) + "  " + _truncate(pane.title, title_budget)


# (name, width, right-aligned). Numbers and durations sit right, labels left.
_BASE_COLUMNS = (("WIN", 5, True), ("CMD", 8, False), ("QUIET", 5, True))
_PHASE_COLUMN = ("PHASE", 8, False)
_TAIL_COLUMNS = (("STATE", 11, False), ("WAITING", 7, True), ("TASKS", 5, True))


def _columns(show_phase: bool):
    return _BASE_COLUMNS + ((_PHASE_COLUMN,) if show_phase else ()) + _TAIL_COLUMNS


def _header(show_phase: bool) -> str:
    cells = [_pad(name, width, right) for name, width, right in _columns(show_phase)]
    return "  ".join(cells) + "  TITLE"


def render_terminal(snapshot: Snapshot, width: int = 100, color: bool = True,
                    show_phase: bool = False, now: float | None = None) -> str:
    """Render the snapshot as an aligned grid."""
    now = now if now is not None else time.time()
    columns = _columns(show_phase)
    fixed = sum(w for _, w, _ in columns)
    gaps = 2 * len(columns)  # two spaces after each column, incl. before TITLE
    title_budget = max(20, width - fixed - gaps - 1)  # -1 for the row's leading space

    lines = ["  " + _header(show_phase)]
    # Freshest session nearest the prompt, as today's tls does.
    for card in sorted(snapshot.cards, key=lambda c: c.activity):
        lines.append("")
        label = "(attached)" if card.attached else "(detached)"
        style = GREEN if card.attached else AMBER
        short = card.name.split("@")[0]
        lines.append("  " + _colour(short, BOLD_CYAN, color) + " " +
                     _colour(label, style, color))
        for window in card.windows:
            lines.append(" " + _row(window, now, color, show_phase, title_budget))
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q`
Expected: PASS. If a width assertion fails, fix `_truncate`/`_pad` rather than relaxing the assertion — the width budget is the point.

- [ ] **Step 5: Commit**

```bash
cd ~/Setup
git add agents_dashboard/termview.py tests/test_agents_dashboard.py
git commit -m "feat(agents-dashboard): terminal grid renderer for tls"
```

---

### Task 6: CLI, wrapper, and the reality check

**Files:**
- Modify: `agents_dashboard/__main__.py`
- Create: `dotfiles/bin/tls`
- Modify: `tests/test_agents_dashboard.py` (append)

**Interfaces:**
- Consumes: `render_terminal`, `collect`.
- Produces: `tls(phase: bool = False, json: bool = False) -> None` in `__main__.py`, registered with `fire`.

- [ ] **Step 1: Write the failing tests**

```python
class TestTlsCommand:
    def test_passes_show_phase_and_skips_the_scan_by_default(self, monkeypatch, capsys):
        seen = {}
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: seen.update(kw) or Snapshot(generated_at=1.0))
        monkeypatch.setattr(main_mod, "render_terminal",
                            lambda snap, **kw: seen.update(kw) or "GRID\n")
        main_mod.tls()
        assert seen["with_phase"] is False
        assert seen["show_phase"] is False
        assert "GRID" in capsys.readouterr().out

    def test_phase_flag_enables_both_the_scan_and_the_column(self, monkeypatch, capsys):
        seen = {}
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: seen.update(kw) or Snapshot(generated_at=1.0))
        monkeypatch.setattr(main_mod, "render_terminal",
                            lambda snap, **kw: seen.update(kw) or "GRID\n")
        main_mod.tls(phase=True)
        assert seen["with_phase"] is True
        assert seen["show_phase"] is True

    def test_json_output_includes_non_claude_windows(self, monkeypatch, capsys):
        card = SessionCard(name="s", windows=[
            WindowRecord(0, 0, "zsh", "/tmp", 5.0, None)], attached=True)
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: Snapshot(generated_at=1.0, cards=[card]))
        main_mod.tls(json=True)
        payload = _json.loads(capsys.readouterr().out)
        window = payload["sessions"][0]["windows"][0]
        assert window["command"] == "zsh"
        assert window["claude"] is None

    def test_no_tmux_server_exits_nonzero_with_the_old_message(self, monkeypatch, capsys):
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: Snapshot(generated_at=1.0, cards=[]))
        with pytest.raises(SystemExit) as exc:
            main_mod.tls()
        assert exc.value.code == 1
        assert "No tmux sessions" in capsys.readouterr().err
```

Add `from agents_dashboard import __main__ as main_mod` and `import json as _json` alongside the other imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q -k TlsCommand`
Expected: FAIL — `module 'agents_dashboard.__main__' has no attribute 'tls'`

- [ ] **Step 3: Implement the command**

In `agents_dashboard/__main__.py`, add the imports and command, and register it with `fire`:

```python
import json as _json
import os
import shutil
import sys

from .collect import collect
from .termview import render_terminal


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
```

Then extend the `fire.Fire` mapping at the bottom of the file to include `"tls": tls` alongside the existing commands.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Setup && uv run pytest tests/test_agents_dashboard.py -q`
Expected: PASS.

- [ ] **Step 5: Create the wrapper**

Create `dotfiles/bin/tls`:

```bash
#!/usr/bin/env bash
# ABOUTME: Terminal view of every tmux window and its Claude session.
# ABOUTME: Thin wrapper; the grid lives in ~/Setup/agents_dashboard/termview.py.
set -euo pipefail
exec /home/ezalos/.local/bin/uv run --project /home/ezalos/Setup \
  python -m agents_dashboard tls "$@"
```

Then `chmod +x dotfiles/bin/tls`.

- [ ] **Step 6: Reality check against the live machine — the gate on this task**

```bash
cd ~/Setup && ./dotfiles/bin/tls
```

Verify by hand, and do not proceed until all hold:

1. **Every tmux window appears.** Compare the row count against `tmux list-panes -a | wc -l`.
2. **Non-Claude windows show their cwd**, `~`-collapsed, with `·` in the three Claude columns.
3. **The attached session is marked** and matches the session you are actually in.
4. **Columns line up** — no ragged edges — and nothing wraps at your real terminal width.
5. **Pick two Claude rows and confirm both clocks.** `QUIET` should match what `tmux list-panes -a -F '#{window_activity}'` implies; `WAITING` should match what the web dashboard shows for the same pane.
6. `./dotfiles/bin/tls | cat` contains **no** escape sequences.
7. `./dotfiles/bin/tls --phase` adds the column and marks guesses with `?`.

A renderer that satisfies its fixtures but disagrees with the machine has failed. Fix and add the real case as a fixture before moving on.

- [ ] **Step 7: Measure against the baseline**

```bash
cd ~/Setup && python3 -c "
import subprocess, time
for label, cmd in (
    ('new tls',        ['./dotfiles/bin/tls']),
    ('new tls --phase',['./dotfiles/bin/tls','--phase']),
    ('old tls',        ['zsh','-ic','tls'])):
    s=time.time(); subprocess.run(cmd, capture_output=True)
    print(f'{label:<18} {time.time()-s:5.2f}s')
"
```
Expected: new `tls` under 0.30 s against the 0.59 s baseline. Report the actual numbers. If it is slower than the old command, stop and say so rather than shipping a regression dressed as a feature.

- [ ] **Step 8: Register the wrapper as a dotfile**

Use the `add-dotfile` skill. Never hand-edit `dotfiles/dotfiles.json`.

Then replace the old `tls` zsh function so it calls the wrapper. Keep the name; the point is that the reflex is unchanged.

- [ ] **Step 9: Commit**

```bash
cd ~/Setup
git add agents_dashboard/__main__.py dotfiles/bin/tls tests/test_agents_dashboard.py dotfiles/dotfiles.json
git commit -m "feat(agents-dashboard): tls terminal command and wrapper"
```

---

## Self-review

**Spec coverage:** dense grid Task 5; non-Claude windows Tasks 3, 5; two labelled clocks Tasks 2, 5; `TITLE` absorbing width Task 5; model column absent throughout; phase behind `--phase` Tasks 4, 5, 6; session grouping with blank lines and activity ordering Task 5; attached marker Tasks 2, 3, 5; colour hierarchy with muted-amber idle and age-warmed `WAITING` Task 5; no-ANSI-when-piped Tasks 5, 6; `--json` including non-Claude windows Task 6; batched `ps` Task 1; `collect(with_phase=)` Task 4; degradation table Tasks 3, 6; latency target measured Tasks 4, 6; reality check Task 6.

**Not covered, by decision:** the interactive TUI and any filter/sort flags, both explicitly non-goals.

**Type consistency:** `WindowRecord(window_index, pane_index, command, cwd, quiet_since, claude)` is constructed positionally in tests and by keyword in `collect.py` — field order matches. `TmuxPane`'s four new fields are appended with defaults, so the five-positional-argument form used across earlier tasks' tests still works. `render_terminal(snapshot, width, color, show_phase, now)` is called with the same keyword names in Task 6 as it is defined with in Task 5. `display_width` is used by both the renderer and its wide-character test. `SessionCard.panes` stays a `list[PaneRecord]` so `render.py` and `snapshot_to_dict` need no change.

**One duplication accepted:** `models._URGENCY_ORDER` restates the order in `classify.URGENCY` to avoid an import cycle. Task 3 Step 5 pins them together with a test, because a duplicated constant that can drift silently is worse than the cycle it avoids.
