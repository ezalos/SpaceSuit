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
- An entry registered before the field existed stays valid without it.
- `set` and `rename` give such an entry the calling session's owner when it is readable. They keep an existing owner.
  `set --owner <8hex>` replaces it.
- `validate` refuses an owner that is not a string of exactly 8 lowercase hex characters.

## Unit names

The tool starts nothing: `start`, `stop` and `check` are commands the registering session writes, and only
`service-checks` runs one (the `check`). A unit that a `start` command names is the registering session's to name.
