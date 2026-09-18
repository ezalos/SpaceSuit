# goals - a read-only goal and evidence reporter

```
python -m goals report  --goal FILE --cost FILE            --out FILE [--html FILE] [--now UTC]
python -m goals compare --protocol FILE --observations FILE --out FILE [--html FILE] [--now UTC]
```

## Why this exists, and why it is this small

A goal's outcome (who implemented it, who verified it, what evidence was cited, what
blocked it) and the machine cost of the work that produced it live in two different
places. This joins them into **one sanitized document** a reviewer can read.

It is a *reporter*, not an accounting system. On **money** there is exactly one arithmetic
operation here - summing the session rows of one snapshot the existing
`claude-usage cost` engine already produced - and it is done with `Decimal` on the
numbers the engine wrote. There is no transcript parser, no price table, no usage
database, no collector and no clock trick. Adding any of those would create a second
source of truth for a number that already has one.

## What its answers are worth

- **Money is `allocated API-equivalent` value, never cash.** It is recomputed from
  token counts against an auditable price table. It is not an invoice, and no budget
  is consumed, checked or enforced by it. Cash spend and headroom are reported as
  UNKNOWN because nothing here is connected to a bill.
- **Rows are a source allocation**, not attribution: they say which transcript a turn
  was counted under. Lineage comes from path nesting, not verified metadata, so even
  a complete price is not proof of the producing goal.
- **Outcome and evidence are attestations copied from the manifest.** Nothing is
  re-run or re-checked. An `accepted` goal is one whose manifest declares an
  implementer, a distinct verifier and at least one evidence reference - that is
  bookkeeping, not a security boundary.
- **Unknown is never zero.** A root that matched no transcript, a missing rate, an
  unplaceable cache TTL and a turn whose counts contradict themselves are four
  different incompletenesses and stay four different things. The report and the HTML
  both draw an unknown as an unknown.
- **Worker time is aggregate**, summed across intervals. It is not the goal's wall
  time and can legitimately exceed it when workers overlap.

## What never leaves

The cost snapshot carries private material - session maps, working directories,
transcript paths, a local price-override path, raw gateway model ids. None of it is
exported. The report keeps labels, dates, counts and the price fingerprint; an
evidence reference that is an absolute path or an unapproved URL scheme is withheld
with its class named and its label kept.

## Limits worth knowing before you trust a number

- `session-cost` is **not** an alternative input: its own-transcript scope answers a
  different question from corpus allocation.
- The engine's `usd` values are JSON numbers produced by floating-point arithmetic
  upstream. They are parsed as decimals from the literal and summed exactly - the
  unrounded sum can therefore show the engine's own float artefacts. It is reproduced,
  never tidied. Rounding happens once, on the display figure.
- A snapshot from a different engine cache version is refused, not adapted.
- **A snapshot's time is not proof its source bytes were fresh.** The upstream cost
  engine caches per file and resumes reading from its stored offset, so a transcript
  **rewritten in place** can leave that cache stale in two ways: a rewrite that keeps
  the **same size**, and - the more plausible case - one that **grows**, where the file
  is read as an append from the old offset so only the new tail is parsed and the stale
  aggregate survives beneath it. Both are recorded upstream
  (`upstream-repair/final-r2-review.md`, D-5, present at BASE and in both builds); this
  is **not** a defect this layer introduced, and it is **not** one upstream has fixed.
  This layer cannot detect either case - it validates and reports what the snapshot
  says, and its `at` describes when the snapshot was taken, never when the sources were
  last read in full. If any source may have been rewritten in place, rebuild the
  upstream cache, take a fresh snapshot and compare the two before a real decision.
  Nothing here changes upstream cache behaviour.
- `--now` sets the report's generation instant only. Authorization and acceptance
  times are never inferred from a clock, a file time or a session timestamp.
- **Two digests, two different things.** `snapshot_sha256` is the sha256 of the cost
  snapshot *file*, over the exact bytes that were parsed, so `sha256sum` reproduces
  it. It is `null` when `build_report` was handed an in-memory object, because there
  are then no bytes to fingerprint. `snapshot_canonical_sha256` is a digest of the
  *parsed object* (`json.dumps(..., sort_keys=True, default=str,
  separators=(',',':'))`), so two files differing only in whitespace share it - which
  is exactly why it is never presented as the file's hash.
- Coverage is reported only when the snapshot's `resolvedRoots` and `unknownRoots`
  partition the requested roots exactly. A snapshot leaving a requested root out of
  both is refused rather than read as complete coverage.
- **Source coverage and price completeness are two different questions.**
  `pricing_complete` asks whether what was selected is fully priced.
  `scope.allocation_coverage` asks whether the selection HOLDS all the work allocated
  to it: the engine reports how many shared message ids in the selected sources are
  allocated - deterministically, corpus-wide - to a source outside the selection. A
  scope can be perfectly priced and still incomplete, and the report says both
  plainly. That count is a number of shared message ids, never a producing-goal
  claim, and no money or token count is missing from what was selected. The count is
  measured across the selected sources and is **not clipped to `since`/`until`**, so a
  day-bounded report never implies a per-window figure. `scope.coverage_complete`
  needs **both** an exact root partition and a known-complete allocation verdict.
- A snapshot predating that verdict reports `allocation_coverage.known: false` and is
  treated as **incomplete, never complete**. It still prices and renders, so an old
  snapshot can be read forensically, but it cannot pass for full coverage.
- **Producer text is never exported.** The engine's `meaning` sentence and its
  `lineageFrom` descriptor are both supplied by the snapshot. `meaning` is validated
  as a bounded, control-free string (a producer-contract check) and then dropped: no
  character class can tell prose from a UUID, a root handle or a digest, so what the
  report says about coverage is G1's own constant wording instead. `lineageFrom` maps
  to an enum - the known engine value becomes `path-nesting-unverified`, anything else
  becomes `unknown-lineage-descriptor` with a `source-lineage-descriptor-withheld`
  limitation. An unrecognised descriptor qualifies the report; it never rejects it.
- **A resolved scope with no priced row is an unknown, not a measured zero.** The
  subtotal reads `$0.00` because summing no rows gives zero; the report marks the
  scope `no-priced-rows`, reports pricing and source coverage as incomplete, and says
  on the page that this is not evidence the work was free or that none was done.
- **Withheld evidence references are not symmetric, on purpose.** A withheld
  *terminal* reference blocks the classification outright - a non-acceptance nobody
  dated with a citable attestation stays `unresolved`. A withheld *defect* reference
  does not: the escaped-defect count is a declared external attestation, and the
  defect-rate gate stays eligible on it. Neither is independently verified by this
  tool, and the counts of withheld references are disclosed in the Sources section
  and in `evidence-ref-withheld`. If you need the defect count to be checkable, cite
  a reference this report may carry.
- Whether worker intervals overlap is `true`, `false` or `null`. An interval with no
  end can make an overlap possible without establishing it; that is reported as
  unknown, never as "no".

## Keyed scope handles

A report never exports a raw root id, and it never will. It does export, under
`source.scope`, a **keyed** handle for the selection it was built over:

- `root_fingerprints` - `hmac-sha256(scope_fingerprint_salt, goal-root-v2\0<root-id>)`
  per requested root, sorted;
- `scope_fingerprint` - the same HMAC over canonical JSON of
  `{root_fingerprints, mode, since, until, engine_time_precision}`;
- `root_fingerprint_algorithm` - that recipe, written out.

`scope_fingerprint_salt` is a required manifest field: at least 32 printable ASCII
characters with at least 12 distinct ones. It is a **key**, it never appears in any
output, and a manifest without one is refused.

### Where that key lives, and who may read it

The salt is a secret, and it has to sit **in cleartext in two files**: the goal
manifest, and - identically - the comparison protocol, because it is what binds an
arm's scope handles to the protocol that compares them. Two arms fingerprinted under
different salts are not comparable and the comparison refuses them.

This tool **reads** those files. It does not create them and it cannot set their mode,
so the handling is yours:

- Keep the manifest and the protocol **private and gitignored**. They are key
  material, not configuration to commit.
- Make them **owner-readable only**; `0600` is the sensible mode. Nothing here checks
  or changes it.
- **Never paste a salt** into a report, an issue, a chat message, a log or a shell
  history line. The tool never emits one - not in output, not in an error - and that
  guarantee ends the moment a human copies the file.
- **Rotate it for a new cohort.** A fresh salt re-keys every handle, which is what you
  want when a new comparison begins; reusing one across cohorts lets handles be
  correlated between them.
- A leaked salt turns every handle back into a confirmation oracle for a guessable
  root id, which is the whole reason the handles are keyed rather than hashed.

The handles answer exactly one question a comparison has to ask - *are these two
scopes the same set, or disjoint?* - and nothing else. The same roots in any order share them; one
different root does not; a different mode, bound, engine precision **or salt** changes
the scope fingerprint.

The handles are keyed rather than merely hashed because an unsalted digest of an id is a
confirmation oracle: anyone holding a small dictionary of plausible root ids can hash
each one and read the answer off the report. Under HMAC that attack needs the salt, and
the salt never leaves those two files: it appears in no report, no page and no error
message this tool produces. The `v2` in the domain prefix marks the change: a v1
unkeyed report is **refused** by a comparison rather than accepted as if it were
opaque.

# compare - a frozen delegation comparison

`python -m goals compare` reads a **frozen protocol** and an **observation ledger** that
names one goal report per arm, and reports what the evidence supports. It routes no
work, enrolls no root, calls no cost engine, prices no token, checks no provider and
spends nothing.

## What it takes

- The protocol is frozen before its window opens: at most two work classes, six pairs,
  twelve arms, a horizon of at most 336 hours, globally unique goal ids and source
  roots, one changed dimension with every other control declared shared, and both arm
  values as scalars. Matching fields, the acceptance oracle and the policy are declared
  there and never inferred from a report.
- The ledger names the protocol by the **sha256 of its exact bytes**, and each
  observation names its goal report the same way (`report_sha256`). Reformat the
  protocol, or edit a report after the ledger recorded it - even by one byte of
  whitespace - and the freeze no longer matches, which is the point. That digest is
  checked against the bytes read, *before* they are parsed.
- A report `generated_utc` after the ledger's `as_of_utc` is refused: the ledger could
  not have seen it, so nothing in it can establish a result. `source.snapshot_sha256`
  is validated only against its own documented meaning - it fingerprints the upstream
  **cost snapshot** file, and is never compared to a report digest.
- Every arm report must be a `whole-session-roots`, `dedicated`-ownership goal report
  whose root fingerprints are exactly the ones its protocol arm declares.

## What its answers are worth

- **A `candidate` is a measurement posture, never an approval.** No status here
  promotes, approves or admits anything; the decision stays with a person. Synthetic
  input can never read beyond `inconclusive`, however the metrics fall.
- **Missing evidence is `inconclusive`, not a verdict.** `not-candidate` is reserved
  for the case where every piece of evidence is in hand and the comparison still went
  the other way.
- **The median is accepted-only and never travels alone.** It is always reported with
  its accepted denominator, the arm count, and **every one of the seven outcome
  counts** - accepted, failed, blocked, abandoned, censored, pending and unresolved.
  A qualification naming only two of them hides the arms nobody could place at all.
- **An outcome nobody dated is UNRESOLVED, not a result.** A goal report states a
  status and never the instant it was reached. A `failed`, `blocked` or `abandoned` arm
  counts as that outcome only when the ledger attests `terminal_at_utc` with a safe
  `terminal_evidence_ref`, inside the window, at or before the horizon, and at or before
  the observation cutoff. Without that it is `unresolved`: it does not settle its pair,
  and every rate that would count it is unknown. `reopened` is always unresolved - it
  may hide a prior acceptance.
- **The horizon is measured against the ledger, not the clock.** An `in_progress` arm is
  `pending` when the cutoff falls before the horizon and `censored` when it falls at or
  after it - and a censored arm's state is what was attested *at the cutoff*, not proof
  of what it was doing at any other instant. The document's own generation clock decides
  one thing only: whether the window has opened.
- **The duration is re-derived, never trusted.** A goal report publishes the
  authorization instant, the acceptance instant and the seconds between them; the
  comparison recomputes the third from the first two, with the reporter's own
  truncation, and refuses the report on any mismatch. That number is the only input to
  the latency threshold, and an externally supplied report can be altered *before* being
  frozen into a ledger — at which point the byte digest faithfully carries the edit. A
  mismatch is a malformed report (exit 2, no output), never an unresolved arm.
- **Acceptance is bounded at both ends.** An acceptance before the window start, after
  the horizon, or after the observation cutoff is `unresolved`, never accepted and never
  censored evidence.
- **An exact disjoint root partition is not attribution.** It proves the arms did not
  share a source. It is still not evidence that a goal produced the turns counted under
  its own roots.
- **Interventions are pilot-wide** and are never merged into a goal report's own
  per-goal counts. A calendar day with no record is *not recorded*, never zero, and a
  ledger with no REQUIRED intervention on any day leaves the cap answers unknown - an
  optional-only ledger proves nothing about a required cap.
- **The ledger may only cite its own window.** Every intervention must fall at or after
  the window start, at or before the horizon, and at or before the ledger's own
  `as_of_utc`. An out-of-window record is refused, not dropped: it is an input the author
  corrects. `budget_admission.checked_utc` is bounded by `created_utc <= checked <=
  as_of_utc` - a check before the window opens is a documented preflight and is kept, one
  before the protocol existed or after the ledger looked is refused.
- **A rate is never divided by a population whose outcome is unknown.** If any arm on a
  side is `unresolved`, that side's acceptance rate and defect rate are `null` with a
  `reason`, and the whole denominator plus the count of arms actually placed stay
  visible. The defect rate is also `null` when any arm never attested a count - averaging
  over the ones that did would credit the others with the mean. Rates are never made
  numerical by dropping the unresolved arms; that changes the population the experiment
  ran over.
- **Controls are attested, never verified.** The protocol's `controls_evidence_ref` is a
  reference this document never opens. Missing or withheld, the controls gate is unknown
  and no class can be a candidate; present, it is still only an attestation, and the
  `controls-attested-not-verified` limitation is always emitted.
- **A cohort headline is the weakest of its classes.** `candidate` requires every
  declared class to be a candidate; a mix of candidate and not-candidate is
  `inconclusive` with `mixed-class-results`. Synthetic input forces `inconclusive` in
  *both* directions - a decisive negative on fabricated data is the same illusion as a
  decisive positive.
- **Cash stays `UNKNOWN - not connected`**, with or without a budget attestation. A
  budget admission is an attestation with a timestamp and a reference, not a provider
  check.
- **Escaped defects are an external attestation** tied to an evidence reference. They
  are never inferred from an arm's status, and a missing count is unknown, not zero.

## What never leaves

The protocol's raw root ids, the ledger's report paths, the goal reports' file digests
and upstream snapshot digests, model ids, price fingerprints and the ledger's own defect
evidence references all stay behind. What is exported: opaque root fingerprint *counts*,
the protocol and ledger file digests (labelled as what they are - private input
integrity fingerprints), safe evidence labels and revisions, aggregate counts, and
limitations. References are classified by the same predicate the goal report uses, so
the JSON and the page can never disagree about what is safe.

## The partial-output contract

`--html` is written after the JSON. If the page cannot be written, the JSON stays on
disk, stderr says which file was written and which was not, and the exit code is 2.
Discarding a correct document because its rendering failed would lose evidence; exiting
0 would hide a missing page. It says both.
