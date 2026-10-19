<!-- ABOUTME: Facts the simulated Louis knows for scenario S2 (job queue design). -->
<!-- ABOUTME: Revealed only when the agent under test asks for them. -->
- Team of two engineers; one person is on call.
- Postgres is already the main database; there is no Redis today.
- About 5,000 jobs a day, bursts at the top of the hour.
- Some jobs charge customers: they must never run twice.
- Deployed on a single VM with docker compose.
- Wants it shipped within two weeks; budget is tight.
