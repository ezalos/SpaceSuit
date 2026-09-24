---
description: Family Claude accounts: usage meters of every account in one table, switch the whole machine to another account without a browser, and the failover watcher (claude-usage tool)
argument-hint: [stats] | switch [<name>] | adopt <name> | list | add <name> [--email <addr>] | remove <name> | add-gpt <name> | remove-gpt <name> | priority <name> [<n>] | cost [--by model|session|repo|day] [--roots <ids>] | prices [--refresh] | auto on|off|status | statusline install | doctor [--fix]
---
Run the local tool and report its output verbatim (it is a short table):

    claude-usage $ARGUMENTS

With no arguments run `claude-usage stats`.

What the rows mean:
- One row per stored account: 5-hour window, weekly window, per-model weekly, extra usage; `↻` = resets in.
  `●` marks the account checked out into Claude Code (every session on this machine runs on it); `⌂` marks home.
- A `live (this terminal, not stored)` row means the login of this terminal is not a stored account yet:
  `claude-usage adopt <name>` takes it, no browser.
- `gpt-*` rows are the ChatGPT accounts Astra and the GPT-5.6 tiers run on, read from
  `chatgpt.com/backend-api/wham/usage`. `●` marks the one the gateway is serving. Which windows a row has depends on
  the plan: on Pro Lite there is no 5-hour window at all, so that column reads `—` and the weekly one is the whole
  budget. `extra` shows the credit balance there rather than extra-usage spend.
- A number in `claude-usage priority` marks an account a LAST RESORT (0 = preferred). Failover picks the account
  whose weekly reset is soonest (EDF; ChatGPT rows: the emptiest) in the lowest tier that has room, so a preferred
  account always beats a deprioritised one with a sooner reset; if only a deprioritised one has room it is still
  taken — a dead machine is worse. The alert warns only when a preferred account existed and had no room, since with
  no preferred candidate the switch was the only possible move.
  `perso` and `gpt-perso` are both 1.
- `4m old` on a window means that reading came from the meter, not from a fresh call: the usage endpoint has a
  request budget and the tool reuses a reading rather than spend one (45 s checked out, 5 min parked, and longer
  while an account is backed off after a 429). It is the real number, just not this second's. `stats --fresh`
  spends the requests and asks anyway — worth it before a decision, not for a glance.

ChatGPT accounts (Louis asks "add my other ChatGPT", "which OpenAI account am I on", "Astra says I hit the limit"):
- `claude-usage add-gpt <name>` — device login for another ChatGPT subscription. Needs a TTY, so tell Louis to paste
  it in a normal tmux window himself. It logs in against an isolated config root, so the account the gateway is
  serving CANNOT be clobbered by a login in progress, and it refuses a duplicate account or a name a Claude account
  already uses. `remove-gpt <name>` drops one (never the active one).
- `claude-usage switch <gpt-name>` — retargets `~/.config/claude-code-proxy/codex`, a symlink to the active
  account's directory. The proxy re-reads its credential on every upstream call, so the NEXT request uses the new
  account: no restart, no session restart. One namespace with the Claude accounts, so `switch` needs no flag.
- **Never hand-copy an `auth.json`.** The refresh token rotates on every use, and on a 401 the proxy DELETES the file
  and forces a browser re-login. The store points at credentials, it never copies them. The active account is
  refreshed by the proxy alone; parked ones by this tool alone.
- Failover is two domains that never cross: a spent ChatGPT account moves to another ChatGPT account, a spent Claude
  account to another Claude account. Swapping a Claude credential does nothing for Astra. When every ChatGPT account
  is spent nothing reroutes — the alert names the Claude model at the same tier (Astra → Fable) and Louis switches
  the session himself with `/model`.
- Alerts name the tmux pane that spent the window when it can be identified (exactly when the session was resumed,
  otherwise the pane(s) whose claude process sits in that repo).

Cost (Louis asks "what did that cost", "what am I burning", "cost vs results", "how much did the swarm spend"):
- `claude-usage cost [--by model|session|repo|day] [--since YYYY-MM-DD] [--until] [--repo <path>] [--json]` —
  API-price-equivalent spend recomputed from the transcripts. **Claude Code's own `total_cost_usd` is wrong for
  gateway models**: it prices an unrecognised id with its fallback table, so an Astra turn bills at roughly Opus-5
  rates, about half Astra's real $10/$50. This tool uses its own auditable table instead.
- It counts each assistant message ONCE by `message.id`. Claude Code rewrites earlier turns into a transcript, so
  the same billed message appears several times (973 of 1967 lines in one measured file); counting lines would
  roughly double the bill.
- Cache reads dominate the corpus (billions of tokens vs millions of input), so cache rates matter more than
  input/output. A missing rate is never substituted with another — the row is marked `*` and those tokens are left
  out, so the figure sits BELOW the truth.
- **The money column is a KNOWN PRICED SUBTOTAL, not an invoice.** A `*` row says what it had to leave out, and
  `--json` gives the cause per row in `partialReasons`: `rate-missing` (the subtotal is below the truth),
  `unknown-ttl` (a write that will not say which TTL it used), `count-conflict` (a turn whose own counts contradict
  each other, withheld entirely — `withheldTurns` counts them). Read the cause before quoting the number: only
  `rate-missing` and `unknown-ttl` mean "at least this much".
- Cache WRITES carry a TTL and the two are priced differently: Anthropic charges 2x input for a 1-hour write and
  1.25x for a 5-minute one. On this machine ~100% of cache writes are 1-hour, so treating them as 5-minute
  understated the bill by about 16%. A write whose TTL the transcript does not establish is NOT assumed to be
  5-minute: it lands in `cwUnknownTtl`, is priced nowhere, and marks the row a floor. That covers an absent split, an
  explicit all-zero one, and anything the split leaves unaccounted for against the flat count. A split claiming MORE
  than the flat count is a contradiction rather than a remainder, and there is no honest way to pick a winner:
  believing the split billed 2M tokens where the transcript's own flat count said 1M were written. Such a turn is
  **withheld from pricing entirely** — the whole turn, because the same disagreement also moves the prompt size that
  decides the long-context tier. Its counters stay on the books (`cwConflict` sizes the disagreement), other turns in
  the same model, day and bucket price normally, and the row reports `count-conflict`.
- `--repo <path>` selects **sessions that touched** that directory or anything under it, considering every cwd a
  session reported (a trailing slash from shell completion is accepted). It is not a per-turn allocation: a session
  that moved between repos is counted in full on both sides, so do not treat a repo total as a precise attribution.
- `--roots <id,id,...>` selects an explicit SET of root sessions plus their descendants, deduped, for attributing a
  campaign rather than a directory. Roots that match no transcript are listed rather than dropped. With `--json` the
  output echoes the resolved scope, the engine and cache version, and a fingerprint of the price table, so an answer
  can be pinned to the rates it was computed with.
- **A scoped subtotal can be short, and `scope.allocationCoverage` is the only thing that says so.** Every message is
  allocated once corpus-wide to one source transcript; if a selected source holds a copy whose owner sits outside the
  selection, that message is counted there instead, and this selection never sees it. `complete: false` with
  `allocatedElsewhereIds: N` means N distinct shared message ids went elsewhere (one id held by several selected
  sources counts once); the full corpus is always `complete: true`. This is **allocation coverage, not a second
  total**, and not evidence that another goal or agent produced those turns — widen the selection to include the
  owning source if you need them. It is independent of `row.partial`, which only says whether what the scope *was
  given* could be priced: a scope can be short and every row still `partial: false`.
- **Time precision is whole UTC days.** `--since`/`--until` resolve to UTC day boundaries and nothing finer: per-turn
  timestamps exist in the transcripts but aggregates do not retain them, so sub-day attribution is not supported and
  must not be claimed. The token classes stay distinct in `--json` (`cacheRead`, `cw5m`, `cw1h`, `cwUnknownTtl`,
  plus the diagnostic `cwConflict`, which is never a billable class), because their rates differ by up to 12.5x.
- Which file a billed id is counted under is deterministic SOURCE ALLOCATION — the first transcript, in the sorted
  walk, that still contains it — and it is re-derived from the corpus on every scan, not frozen into the cache. So
  the same final transcripts give the same totals AND the same per-session split whether the report was built from
  scratch or accumulated over weeks, and a compacted or deleted transcript hands its shared ids back to whatever copy
  survives. It says which source a turn is counted under, never which agent produced it.
- `--json`'s `sessions` map describes only the sessions the rows cover, and merges the two transcripts of a session
  id that exists under two project directories (a repo move) instead of letting one replace the other: `files` lists
  every transcript, `cwds` every directory, `first`/`last` span both.
- **The status line and the report can disagree, and both are right.** `claude-usage session-cost <id>` (what the
  status line shows) prices the complete named transcript, while `cost --by session` allocates each shared message
  globally to one source transcript. They differ only where a message id appears in more than one transcript.
  Neither is an invoice, and neither is a claim about which agent truly produced a turn.
- Subagent transcripts are included. They live BELOW the session that spawned them
  (`<session>/subagents/agent-*.jsonl`, and deeper under `subagents/workflows/`) and used to be missed entirely —
  on this machine that was 49,320 billed turns and 7.9G cache-read tokens, about a third of the whole bill. `--json`
  carries `kind` and `parent` per session, derived from path nesting; that is the filesystem's statement of ancestry,
  not verified metadata, so attribute a campaign by explicit root ids rather than by a repo total.
- Turns are bucketed by prompt size because above a threshold every rate roughly doubles (Astra $10/$50 → $20/$75
  over 272k, Grok over 200k). With a 920k window that is routine, not an edge case.
- `claude-usage prices` shows the table for every model seen; `prices --refresh` replaces it from ONE unauthenticated
  GET of `https://models.dev/api.json` (per-million numbers, cache and tier fields), cross-checked against LiteLLM
  with any >1% disagreement reported rather than averaged. Cron-able: no agent needed. Curated values go in
  `~/.claude/claude-usage/prices.override.json`, which always wins.
- First run scans the whole corpus (~13 s for ~4 GB); after that only appended bytes are read.

Switching (Louis asks "switch to work", "use the perso account", "which account am I on"):
- `claude-usage switch work` — hands EVERY session on this machine, subagents included, to `work` at their next
  request. No restart, no browser. `claude-usage switch` toggles between two stored accounts.
- `claude-usage auto status` — live/home, the four gates, the EDF pick and why, the last decision. `auto on|off`
  controls the watcher (systemd user timer here, launchd on a Mac), every 60 s:
  - **Hard rule** — the live account cannot serve: a locked window, 5-hour ≥ 85 % (`sessionHot`), weekly ≥ 95 %
    (`switchAt`), or Fable ≥ 90 % (`fableCeiling`; `auto on --fable-ceiling off` drops that gate while Fable is not
    the model in use, `--fable-ceiling 90` restores it). It moves now, to the usable account whose weekly reset is
    soonest (EDF) — perso only when nothing preferred is usable, and the log says so. Never held back. The 5-hour
    bound sits under 100 because a switch lands at the next request: at 98 % there is no headroom to absorb that.
  - **Rotation** — otherwise, the EDF pick among usable accounts in the live account's tier or better (perso is
    never rotated onto), entered only when its own 5-hour window is under 65 % (`sessionWarm`), and only when it
    leads the live account's weekly reset by ≥ 2 h (`edfLeadMs`), or a preferred account has become usable while
    the machine is on perso, or the live 5-hour window is itself ≥ 65 %. At most once per 10 min (`minHoldMs`),
    hand switches included. Two accounts resetting minutes apart never ping-pong.
  - Why EDF: headroom left when a window resets is lost, so spend the account whose reset is soonest; the 5-hour
    window is a rate cap, not a score — it decides whether an account can take work now, never which is "better".
  - `stats` has an `edf` column: `▶` the next pick, `✓` usable, otherwise the reason (`5h 98% ≥ 85% cap`,
    `weekly 100% ≥ 95% ceiling`, `locked`, `weekly window unreadable`). Reading it is reading the decision.
  - Telegram (`🖥️ [claude-usage] <host>:`) only when nothing can take the machine or a switch failed; every
    successful move, forced or proactive, is a line in `~/.claude/claude-usage/auto.log` only.
- A switch means connector tools (`mcp__claude_ai_Gmail__*` and other claude.ai-hosted connectors) in any session
  that was already running are dead until that session restarts — the connector is an account-specific server
  resolved once at session start; `/mcp` Reconnect cannot revive it and removes the tools. Use `gmail-read` for
  mail work instead of the connector tools directly; it runs in a fresh process and sees whichever account is live.
- A parked account is only a target when it has ANSWERED on both windows: an Anthropic reading always carries the
  5-hour and the weekly one, so a missing half is a failed read, not room, and the account is skipped rather than
  scored on the half that parsed. A locked window is unavailable at any percentage.
- After a switch this session's claude.ai link (Remote Control) drops; the terminal session itself is fine.

Rules:
- Never `/logout`: it revokes the live grant server-side and the stored record goes dead (`add` again). Switch instead.
- Never set `CLAUDE_CODE_OAUTH_TOKEN` in a session that should follow switches; it overrides the file.
- `add <name>` runs Claude Code's own browser login in a private config dir; it needs a TTY, so tell Louis to run
  `! claude-usage add <name>` himself (never enter anyone's credentials). Remote family: they open the printed URL,
  sign in as themselves, and send back the code the page shows.
- A parked grant is refreshed by this tool only; the checked-out grant by Claude Code only. Never run Claude Code on a
  parked grant and never hand-edit files under ~/.claude/claude-usage. Never print token values.
- `claude-usage doctor --fix` repairs a switch that died halfway (an account "in transition").
