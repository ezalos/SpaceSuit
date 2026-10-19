<!-- ABOUTME: Grader reference for scenario S2: axes, option families, MUSTs, shape. -->
<!-- ABOUTME: Never shown to the agent under test, only to the judge. -->
Lens: design. Principal: Louis's team.

Axes (obvious): throughput; reliability.
Axes (often-missed):
1. idempotency / no double execution for billing jobs
2. operability for a single on-call person at 3am
3. cost to run
4. reversibility (cost of migrating away later)
5. time to ship
6. number of new moving parts (simplicity)
7. observability (seeing stuck or failed jobs)

Option families (at least 5 expected):
A. Celery + Redis
B. Postgres-backed queue (SKIP LOCKED, procrastinate, pg-boss style)
C. managed cloud queue (SQS, Cloud Tasks)
D. cron or scheduler + a jobs table
E. durable workflow engine (Temporal style)
F. lighter Python queue (RQ, Dramatiq, arq)
G. in-process async with retries

MUSTs: no double charge; no silent job loss.
Expected shape: a design record: chosen or recommended option, rejected ones with why,
the tradeoff and sensitivity points, and what would trigger a revisit.
