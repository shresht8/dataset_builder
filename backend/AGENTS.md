# Backend (`backend/`) agent notes

Read [../AGENTS.md](../AGENTS.md) first. Code map: [../docs/backend.md](../docs/backend.md),
data model: [../docs/database.md](../docs/database.md).

FastAPI + SQLAlchemy 2 (sync sessions) + Alembic, Python >= 3.11, settings via
pydantic-settings. Package: `groundline_api`.

## Commands (repo root)

- `make db-up && make migrate` before running anything that touches the DB.
- `make run` (API on :8000), `make lint` (ruff).
- `pytest backend -q`, or one file: `pytest backend/tests/test_rows_patch.py -q`.
  `pytest backend` and `pytest cli` must run separately; `make test` does both.
- New migration: `cd backend && alembic revision --autogenerate -m "..."`.
  Review the output by hand: autogenerate misses enum value changes and
  partial-index `WHERE` clauses. Keep one linear chain and write a working
  `downgrade`.

Tests run against real Postgres and object storage from `DATABASE_URL` and
`STORAGE_*` (see `.env.example`), through FastAPI `TestClient`.

## Rules

- One router per resource under `api/v1/`, registered in `api/v1/router.py`.
  Every handler takes `db: Annotated[Session, Depends(get_db)]` and a user via
  `require_role(Role.X)`.
- Status codes: 422 validation (with a `detail` naming the column), 409
  conflict, 404 missing (including soft-deleted rows), 428 missing `If-Match`.
- Row values are validated only through `services/validation.py`, whether they
  come from a route, import or sync.
- Anything the API and CLI must agree on goes in `shared/groundline_schema`.
- Config is read through `config.py` `Settings`. Do not read `os.environ`
  elsewhere in the backend.
- A model change needs an Alembic migration in the same change.
- Add a test with each endpoint or rule you change, in `backend/tests/`.
  Cover the role boundary (which role gets 403) and the failure path, not
  just the happy one.

## Anti-patterns

- Hard-deleting rows or columns (rows soft-delete, columns archive).
- Updating or deleting `row_edits`, or any `dataset_versions` row or its
  snapshot objects.
- Reading live rows in export, pull or diff; they read stored snapshots.
- PATCH without `If-Match` handling.
- A second validation path next to `services/validation.py`.
- Enabling dev login outside `APP_ENV=dev`.
