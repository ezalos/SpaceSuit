---
name: deck
description: Use when Louis wants a slide deck / talk / presentation made ABOUT the current project or from its results — e.g. "make a deck from this", "prepare slides for the demo", "turn this into a talk". Outside ~/42/Markdowns2Teach this means producing a deck-intake bundle per the portable spec and rsyncing it there — NOT building slides here. Inside ~/42/Markdowns2Teach, follow its deck workflow. Deck building and its citation gates live in ~/42/Markdowns2Teach.
---

# deck — route deck work to where the gates live

Deck building, citation verification (sources.yml + live verbatim-quote checks), and
publishing happen ONLY in `~/42/Markdowns2Teach` ("M2T"), under that repo's CLAUDE.md
rules. Everywhere else, deck work means producing source material.

## Router

1. **cwd inside `~/42/Markdowns2Teach`** → open `docs/references/workflow-new-deck.md` and
   follow it. Stop reading this skill.
2. **Anywhere else — you are SOURCE-SIDE.** Your deliverable is an intake bundle, not
   slides:
   - Read the portable spec at `~/42/Markdowns2Teach/docs/references/deck-intake-spec.md`.
     If this machine does not have that repo, ask Louis to paste or `share-file` it.
   - Produce `deck-intake/` in this project per the spec (HANDOFF.md with story, verified
     numbers + honesty caveats, asset map with sensitivity marks, external sources with
     verbatim quotes). Self-verify the spec's quality checklist.
   - Push the first drop (same machine: drop the host prefix):
     `rsync -av deck-intake/ <m2t-host>:~/42/Markdowns2Teach/docs/talks/<slug>/intake_$(date +%Y-%m-%d-%H%M)/`
   - Record the endpoints in `deck-intake/SYNC.md` (spec §5). Tell Louis the drop is
     pushed; a deck session in M2T takes it from there.
3. **Louis says "pull new requests"** → rsync the talk dir down, read new
   `requests/REQUESTS-*.md`, do the work, push a NEW `intake_<datetime>/` drop containing
   `ANSWERS.md` (spec §6).

## Hard rules

- NEVER build deck HTML source-side — the citation gates only exist in M2T.
- NEVER modify a previously pushed `intake_*/` drop — drops are immutable; push a new one.
- Sensitivity marks (PUBLIC / PERSONAL / HEAVY) are mandatory for every file.
- If a pull or push fails, report it and stop — never guess at bundle state.
