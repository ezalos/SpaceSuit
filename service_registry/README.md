# service_registry

`service-registry` (`bin/service`) owns a `services.yaml`: the list of long-lived programs a machine runs. That file is
never hand-edited. `bin/service-checks` runs each entry's `check` command for this host and reports failures as JSON.

The registry path comes from `--registry` or `$SERVICE_REGISTRY`. `service-registry --help` lists the subcommands
(`register`, `set`, `rename`, `list`, `validate`).

## Entry fields

- Required: `id`, `host`, `kind`, `intended` (`running`, `stopped` or `on-demand`), `start`, `stop`, `check`.
- `why`: required when `intended` is `stopped`.
- `since`: optional.
- `owner`: the first 8 hex characters of the registering Claude session's UUID, e.g. `owner: a1b2c3d4`.

## The `owner` field (ruling of 2026-10-29, from that date on, no backfill)

A lane's ack or close proves that a unit belongs to another lane only through a session id or a registry owner. So:

- `register` writes `owner` on every new entry: from `$CLAUDE_CODE_SESSION_ID` (its first 8 characters), or from
  `--owner <8hex>`. It refuses, and writes nothing, when neither gives a valid id.
- An owner is only ever the caller's own session. When `$CLAUDE_CODE_SESSION_ID` is readable, `--owner` must equal its
  first 8 characters, or the command refuses (rc 2). `--owner` exists for contexts with no session id. Why: a session
  that wrote another session's id would make its own units look foreign to a lane's ack, and hide its live work.
- An entry registered before the field existed stays valid without it.
- `set` and `rename` give such an entry the caller's id (session or `--owner`) when there is one.
- They never replace a different owner. They refuse instead: only the owning session changes that entry, or the
  service is registered again under a new id.
- `validate` refuses an owner that is not a string of exactly 8 lowercase hex characters.

## Unit names

The tool starts nothing: `start`, `stop` and `check` are commands the registering session writes, and only
`service-checks` runs one (the `check`). A unit that a `start` command names is the registering session's to name.

Registered units and transient units are proven differently (ruling of 2026-10-29):

- A registered service is long-lived on purpose and outlives the session that started it. Its unit name carries no
  session token, since that token would be wrong a day later. Its proof of ownership is the entry's `owner` plus its
  exact `start` command.
- A transient unit (a guard, or a keeper a lane starts) is not registered. Its proof is the starting session's 8-hex
  id inside its unit name, which the tools that start it write.
