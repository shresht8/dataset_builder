# Database

Postgres 16, SQLAlchemy 2 ORM (`Mapped[...]` style), Alembic migrations.
Models live in `backend/groundline_api/models/`; importing the package
registers them all on `Base.metadata` (`groundline_api/db.py`). The engine and
`SessionLocal` are built from `DATABASE_URL` (`config.py`). All primary keys
are UUIDs generated server-side with `gen_random_uuid()`.

## Tables

| Table | Model (file) | Purpose |
|---|---|---|
| `users` | `User` (`user.py`) | People. `email` unique, `role` (`user_role` enum), `entra_oid` (set on SSO JIT), `active` (inactive users can't authenticate). |
| `personal_access_tokens` | `PersonalAccessToken` (`user.py`) | Machine tokens. Stores `token_hash` (sha256, unique) only, `expires_at`, `revoked_at`, `scopes` (JSONB, stored but not enforced). FK `user_id` → users, cascade. |
| `datasets` | `Dataset` (`dataset.py`) | `name` unique (`uq_datasets_name`), `description`, `project_id` and `feature_id` (free strings, no FK), `created_by`. |
| `dataset_columns` | `DatasetColumn` (`dataset.py`) | Schema. `key` (may be a dotted path), `label`, `type` (`column_type` enum), `options` (JSONB list for select types), `required`, `order`, `archived`, `json_schema` (JSONB, `json` columns only), `is_key`. |
| `dataset_rows` | `DatasetRow` (`row.py`) | `data` (JSONB, flat, keyed by column key), `status` (`row_status` enum), `assignee`, `row_key`, `rev`, `source_trace_id`, `updated_by/at`, soft delete `deleted_at/by`. |
| `row_edits` | `RowEdit` (`row.py`) | Append-only audit: `row_id`, `field`, `old_value`, `new_value` (JSONB), `user_id`, `at`. Written on every row PATCH that changes a field, status or assignee. Never updated or deleted by app code. |
| `row_comments` | `RowComment` (`comment.py`) | Per-row thread: `row_id`, `user_id`, `body`, `created_at`. |
| `dataset_versions` | `DatasetVersion` (`version.py`) | Immutable cuts: `version` (int, unique per dataset), `snapshot_uri` (`s3://bucket/datasets/{name}/v{n}/`), `content_hash` (sha256 hex; API shows `sha256:<hex>`), `schema_snapshot` (JSONB), `row_count`, `notes`, `created_by`. |

Foreign keys to `users` use `ON DELETE SET NULL`; child tables of a dataset or
row use `ON DELETE CASCADE`, so deleting a dataset removes its columns, rows,
edits and comments.

## Enums (native Postgres)

| Enum | Values | Python |
|---|---|---|
| `user_role` | `admin`, `editor`, `annotator`, `viewer` | `models.user.Role` |
| `row_status` | `draft`, `needs_review`, `approved` | `models.row.RowStatus` |
| `column_type` | `text`, `long_text`, `select`, `multi_select`, `number`, `boolean`, `json`, `date` | `groundline_schema.ColumnType` (shared) |

Adding an enum value needs a migration (`ALTER TYPE ... ADD VALUE`); see
`05bf8a875f6f_date_column_type_gl_3_5_7.py` for the pattern.

## Constraints and indexes that encode rules

- `uq_dataset_columns_one_key`: unique `dataset_id` where
  `is_key AND NOT archived`, so a dataset has at most one active key column.
- `uq_dataset_rows_row_key`: unique `(dataset_id, row_key)` where
  `row_key IS NOT NULL AND deleted_at IS NULL`. Live rows can't share a key
  value; soft-deleted rows free theirs. API code also checks first and turns
  an index race into a 409.
- `ix_dataset_rows_dataset_id_status`, `ix_dataset_rows_dataset_id_assignee`:
  back the grid filters.
- `uq_dataset_versions_dataset_id_version`: one row per version number.
  Cutting also locks the dataset row, so two cuts can't race to the same
  number.

## Value storage conventions

- `data` is flat: a dotted key like `expected.answer` is one column, not a
  nested object. `groundline_schema.paths.flatten/nest` convert at the edges
  (import, sync, export `shape=nested`, CLI row files).
- `json` columns keep their value as-is (object, array, scalar). Flattening
  never recurses into them.
- `date` is a `YYYY-MM-DD` string. `multi_select` is a list of strings.
  Absent and `null` are both "empty".
- `row_key` mirrors `data[<key column>]` and is kept in sync by row create,
  patch, import, sync and by schema changes that set or change the key
  column (which backfill every row, or refuse if values are missing or
  duplicated).
- `rev` starts at 1 and increments on every PATCH; clients send it in
  `If-Match`.

## Migrations

`backend/migrations/versions/`, one linear chain (oldest first):

1. `1b36afbb3997` baseline: datasets, dataset_columns, dataset_rows
2. `01f808ef26d3` full data model: users, PATs, row_edits, versions, enums
3. `9b7e0072c5b8` row_comments
4. `37024be68b69` unique dataset name
5. `42726e27aada` `json` column type, `json_schema`, `is_key`, `row_key`
6. `05bf8a875f6f` `date` column type
7. `724089577f90` soft-delete rows (head)

Commands (run from repo root):

```bash
make migrate                                   # alembic upgrade head
cd backend && alembic revision --autogenerate -m "short description"
cd backend && alembic downgrade -1
```

Rules: always add a migration with any model change, keep the chain linear,
write a working `downgrade`, and check autogenerate output by hand (it misses
enum value changes and partial-index `WHERE` clauses).
