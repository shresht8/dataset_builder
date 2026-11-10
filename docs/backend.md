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
| `GET /datasets`, `GET /datasets/{id}` | [viewer] List filters: `feature_id`, `collection_id`, `folder_id`, `unfiled`. Every read carries `path` and `store_schema`. |
| `GET /datasets/resolve?path=` | [viewer] Path (`collection/folder/…/name`, or a bare unfiled name) → dataset. Declared before `/{id}`. |
| `POST /datasets` | [editor] Name unique within its parent (409). Optional `collection_id`/`folder_id` and either `columns` (resolved against the store when in a collection) or `store_schema_id`. |
| `POST /datasets/{id}/move` | [editor] `{collection_id, folder_id}`; `collection_id: null` = unfiled. Into/between collections the schema is resolved against the target store; out of one the entry is cleared. A name clash is 409 and nothing moves. |
| `DELETE /datasets/{id}` | [editor] Only while it has no versions (409 otherwise). |
| `GET /datasets/{id}/schema` | [viewer] |
| `PUT /datasets/{id}/schema` | [editor] Full column list. Present keys upsert (re-adding un-archives), absent keys archive. `order` = list position. Validates options, unique keys, no key a dotted prefix of another, JSON Schema with local `$ref` only, at most one key column (text + required). Setting/changing the key column backfills `row_key` or refuses. In a collection the columns are resolved against the store (identical entry reused, else one added) and applied. |
| `POST /datasets/{id}/schema/apply` | [editor] `{store_schema_id}`: switch a collection dataset to another entry of its store. 422 if unfiled. |
| `POST /schema/infer` | [editor] Multipart `file` [+ `records_key`, `key`, `delimiter`] → draft columns, warnings, `record_count` and the CSV `delimiter` used. Nothing is stored. |
| `GET /datasets/{id}/rows?status=&assignee=&q=` | [viewer] `assignee` = `me` or a user id; `q` is case-insensitive substring over `text` and `long_text` columns. Excludes deleted rows. Ordered by `created_at`. |
| `POST /datasets/{id}/rows` | [annotator] Validated; new rows are `draft`. |
| `PATCH /datasets/{id}/rows/{rid}` | [annotator] Requires `If-Match: <rev>` (428 if missing). Mismatch → 409 with the current row as body. Merges `data`; sets `status`, `assignee`. Changing the key value needs editor. Writes `row_edits`, bumps `rev`. |
| `DELETE /datasets/{id}/rows/{rid}` | [editor] Soft delete, `If-Match` as PATCH. |
| `POST /datasets/{id}/rows/delete` | [editor] `{"ids": [...]}` or `{"keys": [...]}`; all or nothing. |
| `GET/POST /datasets/{id}/rows/{rid}/comments` | [viewer]/[annotator] |
| `GET /datasets/{id}/rows/{rid}/edits` | [viewer] Edit history. |

**Collections, folders, schema store, bundles**

| Method + path | Notes |
|---|---|
| `GET/POST /collections`, `GET/PATCH/DELETE /collections/{cid}` | [viewer]/[editor] `GET /collections/{cid}` returns the tree: folders and datasets. Delete is 409 while it holds datasets; it removes folders and store entries. |
| `POST /collections/{cid}/folders`, `PATCH/DELETE /folders/{fid}` | [editor] PATCH renames and/or re-parents (not into itself or a subfolder). Delete is 409 unless empty. |
| `GET/POST /collections/{cid}/schemas`, `GET/PUT/DELETE …/{sid}`, `POST …/{sid}/duplicate` | [viewer]/[editor] The schema store. PUT and DELETE on an entry used by a dataset → 409 with `used_by`. |
| `GET /collections/{cid}/export?source=draft\|latest` | [viewer] Zip bundle. Skipped datasets are listed in the `X-Groundline-Skipped` header. |
| `POST /collections/import/preview`, `POST /collections/import` | [editor] Multipart `file` [+ `name`]. Preview reports `name_taken` (200); commit is one transaction (409 on a taken name). |

**Import, sync, versions**

| Method + path | Notes |
|---|---|
| `POST /datasets/{id}/import/preview` | [editor] Multipart `file` [+ `mapping`, `records_key`, `delimiter`]. Parses only: source columns, suggested mapping, first 20 rows, total, the CSV `delimiter` used (detected unless given), and a full validation report if `mapping` is given. |
| `POST /datasets/{id}/import` | [editor] Multipart `file` + `mapping` [+ `skip`, `fixes`, `records_key`, `delimiter`]. Creates valid rows as `draft`; every source row is reported as imported, skipped or errored. Create-only: an existing key is a row error. No server-side session: each call re-parses the file. |
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
| `versioning.py` | `cut`: lock dataset row, select rows (approved only by default, deleted excluded), canonical JSONL, sha256, write `manifest.json` / `schema.json` / `rows.jsonl` under `datasets/{dataset_id}/v{n}/`, insert `DatasetVersion`. The manifest records `dataset_id` and `path`. |
| `storage.py` | `put_snapshot` (each object with `If-None-Match: *`), `get_object`, `parse_snapshot_uri` / `get_snapshot_file` (reads always go through the version's `snapshot_uri`). |
| `columns.py` | Column validation (`validate_columns`) and `write_columns` (upsert present, archive absent, key backfill). Shared by dataset schemas and store entries. |
| `paths.py` | `Paths` (bulk path builder for list reads), `resolve_path`, `dataset_name_taken`. |
| `store.py` | The schema store. `set_dataset_schema` is the one entry point for a column-list write; `resolve_or_add` reuses by hash or adds an entry; `apply` makes a dataset's columns equal an entry (absent columns archive, key change backfills or refuses). |
| `bundles.py` | `export_bundle`, then `plan` / `commit` for import (all datasets and rows in one transaction; rows validated against the store schema). |
| `export.py` | Render a stored snapshot as jsonl/json/yaml, flat/nested, plus the JSON Schema sidecar. Delegates to `groundline_schema.render`. |
| `diff.py` | Diff two stored snapshots, matching rows by `id`. |

## Conventions and tests

Rules, anti-patterns and test commands are in [`backend/AGENTS.md`](../backend/AGENTS.md).
Settings limits worth knowing: `IMPORT_MAX_BYTES` (413), `IMPORT_MAX_NODES`
(YAML alias expansion), `JSON_CELL_MAX_BYTES`, `PAT_DEFAULT_TTL_DAYS`,
`BUNDLE_MAX_UNCOMPRESSED_BYTES` (the zip itself is capped by `IMPORT_MAX_BYTES`).
