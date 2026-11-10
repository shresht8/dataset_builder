# Architecture

Groundline is a tabular annotation tool that produces versioned evaluation
datasets. Teams define a typed column schema, fill and review rows in a web
grid (or sync them from files with the CLI), then cut immutable versions that
code and CI pull by path (`collection/folder/name@vN`, or `name@vN` for an
unfiled dataset) with hash verification. Datasets can be grouped into
collections: a folder tree plus a shared schema store, exportable as a bundle.

## Components

```mermaid
flowchart TB
    browser["Browser<br/>web/ (React, Vite)"] -->|"/v1 via dev proxy<br/>session cookie"| api
    cli["laptop / CI<br/>cli/ (groundline, Typer)"] -->|"/v1<br/>PAT bearer"| api
    api["backend/ groundline_api<br/>(FastAPI)"]
    api --> pg[("Postgres 16<br/>collections, folders, schema store,<br/>datasets, columns, rows, edits,<br/>comments, versions, users, PATs")]
    api --> s3[("S3-compatible bucket<br/>version snapshots:<br/>manifest.json, schema.json, rows.jsonl")]
    shared["shared/ groundline_schema"] -.imported by.-> api
    shared -.imported by.-> cli
```

| Dir | Package | Role |
|---|---|---|
| `shared/` | `groundline_schema` | Column types, `Column` model, manifest, JSON Schema sidecar, dotted-path flatten/nest, JSON/JSONL/YAML record parsing, export rendering, date helpers, schema inference, canonical schema hash, collection bundle format. One source of truth so API and CLI never disagree. |
| `backend/` | `groundline_api` | FastAPI app under `/v1`, SQLAlchemy models, Alembic migrations, auth, domain services. |
| `cli/` | `groundline_cli` (script `groundline`) | Pull versions with a lock file, diff, schema infer/pull/push/apply, row pull/push/delete, collections / folders / store management, collection pull/push as a directory. |
| `web/` | Vite + React 19 + TS | Login, home (collections, then unfiled datasets), collection page (folder tree, schema store, export/import), new-dataset wizard, schema editor, annotation grid, import wizard, versions panel, API tokens page. |
| `infra/` | — | `docker-compose.yml` (Postgres, RustFS object storage, API container, mock OIDC), `Dockerfile`, Entra setup notes. |

Layer details: [database.md](database.md), [backend.md](backend.md),
[frontend.md](frontend.md), [cli.md](cli.md). Feature-to-code map:
[features.md](features.md).

## Core data flow

1. **Schema.** An editor creates a dataset with a column list (web wizard,
   which can infer one from a file; CLI; or `PUT /v1/datasets/{id}/schema`).
   An unfiled dataset owns its columns. A dataset in a collection uses one
   entry of the collection's schema store, and its columns always equal that
   entry (see invariants). Columns are never hard-deleted; absent keys are
   archived. Column keys may be dotted paths (`expected.answer`) that stand
   for nested fields.
2. **Rows.** Rows are stored flat in `dataset_rows.data` (JSONB keyed by column
   key). Every write is validated against the schema
   (`services/validation.py`). Rows come from the grid, the import wizard
   (CSV/XLSX/JSON/JSONL/YAML, create-only) or CLI sync (key-matched upsert).
3. **Review.** Each row has `status` (`draft → needs_review → approved`), an
   optional `assignee`, a comment thread and an append-only edit log
   (`row_edits`). Concurrent edits use optimistic locking (`rev` + `If-Match`,
   409 on mismatch).
4. **Version.** An editor cuts a version (`POST /versions`). By default only
   approved rows go in. The rows are serialised to canonical JSONL, hashed
   (`sha256`), and written with the schema and manifest to object storage
   under `datasets/{dataset_id}/v{n}/`. Versions cut before collections
   existed sit under `datasets/{name}/v{n}/`; every read follows the
   version's `snapshot_uri`. Versions are immutable and never deleted.
5. **Consume.** Export and CLI `pull` always read the stored snapshot, never
   live rows. The CLI verifies `content_hash` before writing files and records
   the pin in `groundline.lock` (keyed by path, with the `dataset_id`);
   `groundline pull --lock` restores every pin in CI by id, and warns if the
   dataset has moved.
6. **Bundle.** A whole collection exports as a zip (web/API) or a directory
   (`groundline collections pull/push`) in one layout defined in
   `groundline_schema/bundle.py`: `collection.yaml`, `_store/`, one rows file
   plus `.meta.yaml` per dataset. It carries rows only; imported rows start as
   `draft`.

## Auth model

- **Interactive:** signed session cookie `groundline_session`
  (`auth/sessions.py`, HMAC with `APP_SECRET_KEY`, 12h). It is issued by
  either the passwordless dev login (`POST /v1/auth/login`, only when
  `APP_ENV=dev` and `AUTH_DEV_LOGIN=true`; the app refuses to start
  otherwise) or Entra OIDC SSO (`/v1/auth/sso/login` → `/callback`, PKCE,
  JIT-provisioning with role from group claims via `ROLE_MAPPING`).
- **Machine:** personal access tokens `glpat_…` sent as
  `Authorization: Bearer`. Only the sha256 is stored. A token acts as its user
  with that user's role. The `scopes` column exists but is not enforced.
- **Roles** (`viewer < annotator < editor < admin`, `deps.require_role`):
  viewer reads and pulls; annotator edits row data/status/assignee and
  comments; editor also changes schema, imports, syncs, deletes rows/datasets,
  changes a row's key value and cuts versions; admin also manages users.

## Invariants worth knowing before you change code

- Versions are immutable: storage writes use `If-None-Match: *`, there is no
  delete endpoint, and a dataset with any version can't be deleted (409).
- A dataset `name` is unique within its parent: among unfiled datasets, or
  within one folder (or the root) of one collection. Its **path** is
  `collection/folder/…/name`, or the bare name when unfiled. Endpoints take
  the dataset **id**; the CLI resolves paths (`GET /datasets/resolve?path=`).
  Storage keys and lock entries use the id, so a move never breaks a pin.
  Datasets can't be renamed; paths change only through moves and
  collection/folder renames.
- A dataset in a collection uses exactly one store entry, and its
  `dataset_columns` equal that entry. Every schema write for such a dataset
  goes through `services/store.py`, never straight to `dataset_columns`. A
  write is resolved by canonical hash (`groundline_schema.schema_hash`): an
  identical entry is reused, otherwise a new entry named after the dataset is
  added. An entry used by any dataset is frozen (409 with `used_by`).
- Deleted rows are soft-deleted (`deleted_at`). They disappear from lists,
  versions, sync, pull and key uniqueness, but keep data and edit history.
- At most one active **key column** per dataset (text, required). Its value is
  mirrored into `dataset_rows.row_key` with a partial unique index; CLI sync
  matches rows on it.
- Export bytes are canonical (sorted keys, compact separators). JSON/YAML and
  the `nested` shape are renderings of the same stored JSONL, so the hash does
  not change with format or shape, and the CLI renders locally with the same
  code (`groundline_schema.render`).
- `date` values are `YYYY-MM-DD` strings everywhere, never JS `Date` objects
  or timestamps.
