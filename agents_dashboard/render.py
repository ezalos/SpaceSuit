# ABOUTME: Render a Snapshot as a self-contained dark HTML page - no build, no external assets.
# ABOUTME: All interpolated text is escaped; titles are model-generated and never trusted raw.

from __future__ import annotations

import html
import time

from .models import Snapshot, WaitingReason

REASON_LABEL = {
    WaitingReason.PERMISSION: "permission",
    WaitingReason.QUESTION: "question",
    WaitingReason.UNSENT_INPUT: "unsent",
    WaitingReason.IDLE: "idle",
}

CSS = """
:root { color-scheme: dark; }
body { margin:0; padding:1rem; background:#0d1117; color:#c9d1d9;
       font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
h1 { font-size:1rem; margin:0 0 .75rem; color:#8b949e; font-weight:600; }
.summary { position:sticky; top:0; background:#0d1117; padding:.5rem 0 .75rem;
           border-bottom:1px solid #21262d; margin-bottom:1rem; }
.chip { display:inline-block; margin-right:.75rem; }
.card { border:1px solid #21262d; border-radius:6px; padding:.75rem;
        margin-bottom:.75rem; background:#11161d; }
.card.not-started { opacity:.45; }
.card h2 { font-size:.9rem; margin:0 0 .5rem; color:#58a6ff; }
.row { display:flex; flex-wrap:wrap; gap:.5rem; align-items:baseline;
       padding:.35rem 0; border-top:1px solid #1b2129; }
.pill { padding:.05rem .4rem; border-radius:3px; background:#21262d; font-size:.8rem; }
.phase-design{color:#d2a8ff}.phase-implem{color:#7ee787}.phase-review{color:#ffa657}
.phase-wrap_up{color:#79c0ff}.phase-unknown{color:#6e7681}
.badge { padding:.05rem .4rem; border-radius:3px; font-size:.8rem; }
.r-permission{background:#8e1519;color:#fff}.r-question{background:#9e6a03;color:#fff}
.r-unsent_input{background:#1f6feb;color:#fff}.r-idle{background:#21262d;color:#8b949e}
.r-working{background:#21262d;color:#8b949e}
/* A phase guessed from edit activity alone: dimmed, italic and suffixed "?".
   It must not read as the same kind of claim as a skill-evidenced one. */
.pill.weak { opacity:.45; font-style:italic; }
.badge.tasks { background:#21262d; }
.t-open{color:#ffa657}.t-done{color:#6e7681}
.title { flex:1 1 12rem; color:#c9d1d9; }
.meta { color:#6e7681; font-size:.8rem; }
.attach { color:#6e7681; font-size:.8rem; cursor:pointer; }
"""

STALE_BANNER_CSS = ".stale-banner { background:#8e1519; color:#fff; padding:.6rem .8rem; border-radius:6px; margin-bottom:1rem; font-weight:600; }"

SCRIPT = """
document.addEventListener('click', function (e) {
  var el = e.target.closest('.attach');
  if (!el) return;
  navigator.clipboard.writeText(el.dataset.attach);
  var old = el.textContent; el.textContent = 'copied'; setTimeout(function(){ el.textContent = old; }, 900);
});
setTimeout(function () { location.reload(); }, 15000);
(function () {
  // Staleness is computed against the BROWSER's clock, not the server's, on
  // purpose: this one mechanism covers both outage holes at once. (1) The
  // Pi's fallback now serves this same rendered page instead of raw JSON, so
  // a phone browser during an outage still gets a page that can tell it is
  // looking at old data. (2) A live server whose collector has silently
  // stopped updating (process up, collection wedged) would otherwise serve an
  // arbitrarily old snapshot as a plain 200 with no cue at all.
  //
  // 120s threshold: the live path re-collects within a 3s TTL and the
  // fallback snapshot is pushed to the Pi every 60s, so anything past two
  // minutes means data has genuinely stopped updating, not a momentary blip.
  var STALE_THRESHOLD_SECONDS = 120;
  var generatedAt = parseFloat(document.documentElement.dataset.generatedAt);
  var banner = document.getElementById('stale-banner');
  if (!banner || isNaN(generatedAt)) return;
  var ageSeconds = (Date.now() / 1000) - generatedAt;
  if (ageSeconds <= STALE_THRESHOLD_SECONDS) return;
  var mins = Math.floor(ageSeconds / 60);
  var age = mins >= 1 ? (mins + 'm') : (Math.max(0, Math.floor(ageSeconds)) + 's');
  banner.textContent = 'Data is ' + age + ' old - the dashboard has stopped updating.';
  banner.hidden = false;
})();
"""


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _summary(snapshot: Snapshot) -> str:
    counts: dict[str, int] = {}
    for card in snapshot.cards:
        for pane in card.panes:
            key = REASON_LABEL[pane.waiting_reason] if pane.waiting_reason else "working"
            counts[key] = counts.get(key, 0) + 1
    order = ["permission", "question", "unsent", "idle", "working"]
    parts = [
        f'<span class="chip">{counts[k]} {html.escape(k)}</span>'
        for k in order
        if counts.get(k)
    ]
    return "".join(parts) or '<span class="chip">no sessions</span>'


def _pane_row(pane, now: float) -> str:
    if pane.waiting_reason:
        label = REASON_LABEL[pane.waiting_reason]
        since = _duration(now - pane.waiting_since) if pane.waiting_since else ""
        badge = (
            f'<span class="badge r-{pane.waiting_reason.value}">'
            f"{html.escape(label)}{' · ' + since if since else ''}</span>"
        )
    else:
        badge = '<span class="badge r-working">working</span>'

    meta = " · ".join(
        html.escape(x) for x in (pane.model, pane.git_branch) if x
    )

    # A phase guessed from edit activity alone is marked, not dressed up as the
    # skill-evidenced ones. On the live board 14 of 25 sessions reached `implem`
    # that way, and rendering them identically made the whole column look far
    # more certain than it was.
    guess = pane.phase_is_guess
    phase_label = f"{pane.phase.value}?" if guess else pane.phase.value
    phase_title = (
        "inferred from edit activity only — no skill invocation found"
        if guess else f"evidence: {pane.phase_evidence.value}"
    )
    phase_pill = (
        f'<span class="pill phase-{pane.phase.value}{" weak" if guess else ""}"'
        f' title="{html.escape(phase_title, quote=True)}">{phase_label}</span>'
    )

    # Outstanding work, where the session actually declared it. Sessions that
    # never used the task tools show nothing rather than a misleading zero.
    tasks = ""
    if pane.tasks.known:
        open_n = pane.tasks.outstanding
        tasks = (
            f'<span class="badge tasks{" t-open" if open_n else " t-done"}"'
            f' title="{pane.tasks.completed} of {pane.tasks.total} tasks completed">'
            f"{open_n} open</span>"
            if open_n
            else f'<span class="badge tasks t-done" title="all '
                 f'{pane.tasks.total} tasks completed">all done</span>'
        )

    return (
        '<div class="row">'
        f"{phase_pill}"
        f"{badge}{tasks}"
        f'<span class="title">{html.escape(pane.title)}</span>'
        f'<span class="meta">{meta}</span>'
        f'<span class="attach" data-attach="{html.escape(pane.attach, quote=True)}">'
        f"{html.escape(pane.attach)}</span>"
        "</div>"
    )


def _card(card, now: float) -> str:
    if card.not_started:
        return (
            '<div class="card not-started"><h2>'
            f"{html.escape(card.name)}</h2>"
            '<div class="meta">not started</div></div>'
        )
    rows = "".join(_pane_row(p, now) for p in card.panes)
    return (
        '<div class="card"><h2>'
        f"{html.escape(card.name)}"
        f'</h2><div class="meta">{len(card.panes)} claude</div>{rows}</div>'
    )


def render(snapshot: Snapshot) -> str:
    """Render a Snapshot as a complete HTML page.

    Staleness is no longer a server-side parameter (see SCRIPT): the page
    always carries its own generated_at timestamp and a hidden banner, and the
    browser decides at load time whether the data is too old. That closes two
    holes a `stale_seconds` argument the server never passed could not: the
    Pi's fallback (which has no renderer of its own to hand a value to) and a
    live server whose collector has silently wedged while the process stays
    up.
    """
    now = snapshot.generated_at
    generated = time.strftime("%H:%M:%S", time.localtime(now))
    cards = "".join(_card(c, now) for c in snapshot.cards)
    css = CSS + STALE_BANNER_CSS
    banner = '<div class="stale-banner" id="stale-banner" hidden></div>'
    return (
        '<!doctype html>\n<html lang="en" data-generated-at="' + repr(now) + '">'
        '<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>agents</title><style>" + css + "</style></head><body>"
        + banner
        + f'<h1>claude sessions &middot; {generated}</h1>'
        + f'<div class="summary">{_summary(snapshot)}</div>'
        + cards
        + "<script>" + SCRIPT + "</script></body></html>"
    )
