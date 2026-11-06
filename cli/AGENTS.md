# CLI (`cli/`) agent notes

Read [../AGENTS.md](../AGENTS.md) first. Code map: [../docs/cli.md](../docs/cli.md).

`groundline` is a Typer app on httpx. Package: `groundline_cli`. It talks to
the API with a personal access token (`glpat_...`).

## Commands (repo root)

- `make install` installs `shared`, `backend` and `cli` editable.
- `pytest cli -q`. It needs no server and no database: the `install_transport`
  fixture in `cli/tests/conftest.py` routes every `httpx.Client` through a mock
  handler. Run it separately from `pytest backend`.
- `groundline --help` lists the commands.

## Rules

- User-facing failures raise one of `ApiError`, `LockError`, `RowFileError`,
  `SchemaFileError`, `ConfigError`; the command prints one line and exits 1.
  A traceback is a bug.
- Rendering and parsing shared with the API (`render`, `paths`, `records`,
  JSON Schema) is imported from `groundline_schema`, never reimplemented.
- `pull` verifies `content_hash` before writing any file.
- `groundline.lock` is written deterministically (sorted keys). Keep it
  diff-friendly.
- New command: add it under `commands/`, put HTTP calls in `client.py`, and
  add a test with the mock transport.

## Anti-patterns

- Rendering export formats in the CLI with different code from the API.
- Writing files from a failed or hash-mismatched pull.
- Tests that hit a real server.
