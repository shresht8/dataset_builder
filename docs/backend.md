# Backend (`backend/groundline_api`)

FastAPI + SQLAlchemy 2 (sync sessions) + Alembic, Python ≥ 3.11. Settings via
pydantic-settings. Object storage via an S3 client (`services/storage.py`).

## Layout

| Path | What lives there |
|---|---|
| `main.py` | `create_app()`: mounts `api/v1/router.py` at `/v1`; refuses to start if dev login is on outside `APP_ENV=dev`; lifespan runs the optional admin bootstrap. |
| `config.py` | `Settings` (env / `.env`). Defaults match `infra/docker-compose.yml`. Full list in `.env.example`. |
| `db.py` | engine, `SessionLocal`, `Base`. |
| `deps.py` | `get_db`, `get_current_user` (bearer PAT, else session cookie; 401), `has_role`, `require_role(min)` (403). |
| `api/v1/*.py` | One router per resource (below). Handlers are plain `def` except multipart ones (`async def`). |
| `schemas/*.py` | Pydantic request/response models (`DatasetRead`, `RowRead`, `RowPatch`, `VersionCut`, `VersionDiff`, ...). |
| `models/*.py` | ORM models. See [database.md](database.md). |
| `services/*.py` | Domain logic used by routes (below). |
| `auth/` | `sessions.py` (HMAC cookies), `tokens.py` (PATs), `oidc.py` (Entra OIDC + PKCE, joserfc), `provisioning.py` (JIT user on SSO), `roles.py` (group → role). |
| `bootstrap.py` | `python -m groundline_api.bootstrap admin@example.com --name "Admin"`: create or promote the first admin (idempotent). Also runs at startup if `BOOTSTRAP_ADMIN_EMAIL` is set. |

## API (`/v1`)

Minimum role in brackets. `{id}` is the dataset UUID.

**Health / auth / users**

| Method + path | Notes |
|---|---|
| `GET /health` | 200 if `select 1` works, else 503. No auth. |
| `GET /auth/config` | `{dev_login, sso}`: which sign-in options to show. No auth. |
| `POST /auth/login` | Dev only. `{email}` → session cookie. No credential check. |
| `GET /auth/sso/login`, `GET /auth/sso/callback` | Entra OIDC code flow with PKCE, state and nonce; JIT-provisions and sets the session cookie. |
| `GET /auth/me`, `POST /auth/logout` | Current user; clear cookie. |
| `POST/GET /auth/tokens`, `DELETE /auth/tokens/{token_id}` | Caller's PATs. Raw token returned once on create. |
| `GET/POST /users`, `PATCH /users/{user_id}` | [admin] Local user management: role, display name, active. |

**Datasets, schema, rows**

| Method + path | Notes |
|---|---|
| `GET /datasets`, `GET /datasets/{id}` | [viewer] List supports `feature_id` filter. |
| `POST /datasets` | [editor] Name must be unique (409). |
| `DELETE /datasets/{id}` | [editor] Only while it has no versions (409 otherwise). |
| `GET /datasets/{id}/schema` | [viewer] |
| `PUT /datasets/{id}/schema` | [editor] Full column list. Present keys upsert (re-adding un-archives), absent keys archive. `order` = list position. Validates options, unique keys, no key a dotted prefix of another, JSON Schema with local `$ref` only, at most one key column (text + required). Setting/changing the key column backfills `row_key` or refuses. |
| `GET /datasets/{id}/rows?status=&assignee=&q=` | [viewer] `assignee` = `me` or a user id; `q` is case-insensitive substring over `text` and `long_text` columns. Excludes deleted rows. Ordered by `created_at`. |
| `POST /datasets/{id}/rows` | [annotator] Validated; new rows are `draft`. |
| `PATCH /datasets/{id}/rows/{rid}` | [annotator] Requires `If-Match: <rev>` (428 if missing). Mismatch → 409 with the current row as body. Merges `data`; sets `status`, `assignee`. Changing the key value needs editor. Writes `row_edits`, bumps `rev`. |
| `DELETE /datasets/{id}/rows/{rid}` | [editor] Soft delete, `If-Match` as PATCH. |
| `POST /datasets/{id}/rows/delete` | [editor] `{"ids": [...]}` or `{"keys": [...]}`; all or nothing. |
| `GET/POST /datasets/{id}/rows/{rid}/comments` | [viewer]/[annotator] |
| `GET /datasets/{id}/rows/{rid}/edits` | [viewer] Edit history. |

**Import, sync, versions**

| Method + path | Notes |
|---|---|
| `POST /datasets/{id}/import/preview` | [editor] Multipart `file` [+ `mapping`, `records_key`]. Parses only: source columns, suggested mapping, first 20 rows, total, and a full validation report if `mapping` is given. |
| `POST /datasets/{id}/import` | [editor] Multipart `file` + `mapping` [+ `skip`, `fixes`, `records_key`]. Creates valid rows as `draft`; every source row is reported as imported, skipped or errored. Create-only: an existing key is a row error. No server-side session: each call re-parses the file. |
| `POST /datasets/{id}/sync` | [editor] Multipart `file` [+ `records_key`, `revs`, `bases`, `force`, `dry_run`, `ignore_unknown`]. Key-matched upsert used by `groundline rows push`. Always 200 when well-formed; `applied` says whether it wrote. All or nothing. |
| `POST /datasets/{id}/versions` | [editor] `{notes?, include_unapproved=false}`. Empty selection → 422. |
| `GET /datasets/{id}/versions` | [viewer] Includes `created_by_email`. |
| `GET /datasets/{id}/versions/{v}?format=jsonl\|json\|yaml&shape=flat\|nested` | [viewer] Export, read from storage. |
| `GET /datasets/{id}/versions/{v}/manifest`, `.../jsonschema?shape=` | [viewer] |
| `GET /datasets/{id}/versions/diff?from=&to=` | [viewer] Added / removed / modified rows plus schema changes, from stored snapshots only. |

`api/v1/traces.py` (create rows from production traces) is a registered but
empty router: not implemented yet.

## Services

| File | Responsibility |
|---|---|
| `validation.py` | `row_errors` / `validate_row_data`: type checks per column type, required fields, select options, `json` against its JSON Schema and `JSON_CELL_MAX_BYTES`, key value without surrounding whitespace. Archived columns and unknown fields pass through. Shared by rows, import and sync. |
| `importer.py` | `parse_upload`, `suggest_mapping`, `preview`, `commit`. CSV/XLSX streamed; JSON/JSONL/YAML via `groundline_schema.records`. Coerces spreadsheet strings per column type (numbers, booleans, comma/semicolon multi-select, strict dates). Enforces key uniqueness within the file and against the dataset. |
| `sync.py` | Key-based upsert with conflict detection. Per key: unchanged since pull (`bases`) → skip; no field differs → unchanged; `revs` matches or `force` → update; else conflict. New keys → draft rows. Updates merge fields; explicit `null` clears. A changed `approved` row goes back to `needs_review`. Never deletes. |
| `versioning.py` | `cut`: lock dataset row, select rows (approved only by default, deleted excluded), canonical JSONL, sha256, write `manifest.json` / `schema.json` / `rows.jsonl`, insert `DatasetVersion`. |
| `storage.py` | `put_snapshot` (each object with `If-None-Match: *`), `get_object`. |
| `export.py` | Render a stored snapshot as jsonl/json/yaml, flat/nested, plus the JSON Schema sidecar. Delegates to `groundline_schema.render`. |
| `diff.py` | Diff two stored snapshots, matching rows by `id`. |

## Conventions

- Validation failures are 422 with a `detail` string naming the column.
  Uniqueness / state conflicts are 409. Missing resources are 404, including
  soft-deleted rows.
- Every route takes `db: Annotated[Session, Depends(get_db)]` and a user via
  `require_role(...)`. Add new routes the same way and register the router in
  `api/v1/router.py`.
- Put logic shared by API and CLI in `shared/groundline_schema`, not in the
  backend.
- Limits come from settings: `IMPORT_MAX_BYTES` (413), `IMPORT_MAX_NODES`
  (YAML alias expansion), `JSON_CELL_MAX_BYTES`, `PAT_DEFAULT_TTL_DAYS`.

## Tests

`backend/tests/`, pytest with FastAPI `TestClient` against the real Postgres
and object storage from `DATABASE_URL` / `STORAGE_*`. `conftest.py` creates one
user per role per run (unique emails) and offers a dev-login helper. Bring
services up and migrate first:

```bash
make db-up && make migrate
pytest backend                              # whole suite
pytest backend/tests/test_rows_patch.py -q  # one file
```

`pytest backend` and `pytest cli` must run separately (both packages are
named `tests`). `make test` does both.
