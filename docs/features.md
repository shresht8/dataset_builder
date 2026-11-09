# Features → code

Where each shipped feature lives. Paths are relative to the repo root;
`api/` = `backend/groundline_api/api/v1/`, `svc/` =
`backend/groundline_api/services/`, `ds/` = `web/src/datasets/`,
`cmd/` = `cli/groundline_cli/commands/`, `tests/` = `backend/tests/`.

| Feature | DB | API / service | Web | CLI | Tests |
|---|---|---|---|---|---|
| Dev login (passwordless, dev only) | `users` | `api/auth.py` `login`, `auth/sessions.py`, guard in `main.py` | `auth/LoginForm.tsx` | — | `test_auth.py` |
| Entra OIDC SSO + JIT users + group → role | `users.entra_oid` | `api/auth.py` `sso_*`, `auth/oidc.py`, `auth/provisioning.py`, `auth/roles.py` | `auth/authConfig.ts`, `LoginForm.tsx` | — | `test_sso.py`; local mock IdP: compose profile `sso-mock`, `infra/ENTRA-SETUP.md` |
| Roles / RBAC | `user_role` enum | `deps.py` `require_role` | role flags in `DatasetPage.tsx`, `Workflow.tsx` | — | role cases across tests |
| User management (admin) + first-admin bootstrap | `users` | `api/users.py`, `bootstrap.py` | — | — | `test_users.py` |
| Personal access tokens | `personal_access_tokens` | `api/auth.py` `/tokens`, `auth/tokens.py` | `tokens/TokensPage.tsx` | `config.py` | `test_pats.py` |
| Datasets: list / create / delete (no versions) | `datasets` | `api/datasets.py` | `DatasetListPage.tsx`, `DeleteDatasetDialog.tsx` | `cmd/datasets.py` | `test_datasets_crud.py`, `test_delete.py` |
| Column schema (8 types, archive, dotted keys) | `dataset_columns` | `api/schema.py`, `svc/columns.py` | `ds/SchemaPanel.tsx`, `schema/SchemaEditor.tsx` | `cmd/schema.py`, `schemafile.py` | `test_schema.py` |
| `json` columns + JSON Schema + key column | `json_schema`, `is_key`, `row_key` | `api/schema.py`, `svc/validation.py`, `api/rows.py` | `DetailDrawer.tsx`, `json.ts` | `schema infer --key` | `test_schema_json_key.py`, `test_rows_json_key.py`, `test_json_key_model.py` |
| `date` columns | `column_type` value | `svc/validation.py`, `groundline_schema/temporal.py`, `svc/importer.py` | `Cell.tsx` `DateInput`, `dates.ts` | `schemafile.py` inference | `test_date_values.py`, `test_import_dates.py`, `test_temporal.py` |
| Row CRUD + typed validation | `dataset_rows` | `api/rows.py`, `svc/validation.py` | `DatasetPage.tsx`, `Cell.tsx` | — | `test_datasets_rows.py`, `test_rows_validation.py` |
| Keyboard-first grid + detail drawer | — | — | `DatasetPage.tsx`, `Cell.tsx`, `DetailDrawer.tsx` | — | manual |
| Optimistic locking + conflict UI | `rev` | `api/rows.py` `patch_row` (`If-Match`, 409) | `ConflictDialog.tsx` | — | `test_rows_patch.py` |
| Status + assignee workflow | `status`, `assignee` | `api/rows.py` | `Workflow.tsx` | — | `test_rows_status_assignee.py` |
| Filters, search, saved views | indexes on status/assignee | `api/rows.py` `list_rows` | `FilterBar.tsx`, `views.ts` (localStorage) | — | `test_rows_validation.py`, `test_rows_status_assignee.py` |
| Comments + edit history | `row_comments`, `row_edits` | `api/rows.py` `/comments`, `/edits` | `CommentsPanel.tsx` | — | `test_rows_comments_edits.py` |
| Soft-delete rows (single, bulk by id/key) | `deleted_at/by` | `api/rows.py` | row delete in `DatasetPage.tsx` | `rows delete` | `test_delete.py` |
| Import CSV/XLSX/JSON/JSONL/YAML (two-step, mapping, fix/skip) | `dataset_rows` | `api/imports.py`, `svc/importer.py`, `groundline_schema/records.py` | `ImportWizard.tsx` | — | `test_import.py`, `test_import_structured.py`, `test_import_dates.py` |
| File sync (key-matched upsert, conflicts) | `row_key`, `rev` | `api/sync.py`, `svc/sync.py` | — | `rows pull/push`, `rowfile.py` | `test_sync.py`, `cli/tests/test_rows_schema_commands.py` |
| Cut immutable versions | `dataset_versions` + bucket | `api/versions.py`, `svc/versioning.py`, `svc/storage.py` | `VersionsPanel.tsx` | — | `test_versions.py` |
| Export jsonl/json/yaml, flat/nested + JSON Schema sidecar | snapshot in bucket | `api/versions.py`, `svc/export.py`, `groundline_schema/render.py`, `jsonschema.py` | — | `pull.py` | `test_export.py`, `test_export_nested.py`, `test_jsonschema.py` |
| Version diff | snapshots | `api/versions.py`, `svc/diff.py` | — | `datasets diff` | `test_diff.py` |
| Pull + lock file (CI) | — | export endpoints | — | `main.py` `pull`, `pull.py`, `lock.py` | `cli/tests/test_pull*.py`, `test_lock.py` |
| Health check | — | `api/health.py` | — | — | `test_health.py` |
| Collections + folders, dataset paths, move | `collections`, `folders`, `datasets.collection_id/folder_id` | `api/collections.py`, `api/datasets.py` `resolve`/`move`, `svc/paths.py` | `DatasetListPage.tsx` (home), `collections/CollectionPage.tsx`, `collections/dialogs.tsx`, `collections/DatasetBreadcrumb.tsx` | `cmd/collections.py`, `cmd/folders.py`, `datasets list --collection`, `datasets move`, `paths.py` | `test_collections.py`, `cli/tests/test_collections_commands.py` |
| Per-collection schema store (reuse by hash, freeze in use, apply, resolve on move) | `collection_schemas`, `datasets.store_schema_id` | `api/store.py`, `api/schema.py` `apply`, `svc/store.py`, `groundline_schema/schema_hash.py` | `collections/StorePanel.tsx`, `ds/SchemaPanel.tsx` | `cmd/store.py`, `schema apply`, `datasets create --from-store` | `test_store.py` |
| Schema inference from a data file | — | `api/infer.py`, `groundline_schema/infer.py` | `collections/NewDatasetWizard.tsx` | `schema infer` | `test_infer.py` |
| New-dataset wizard (multi-file: infer or store → create → import) | — | `POST /datasets`, `/schema/infer`, import endpoints | `collections/NewDatasetWizard.tsx`, `ImportWizard.tsx` (`initialFile`) | — | manual |
| Collection bundles: zip export/import, directory pull/push | — | `api/bundles.py`, `svc/bundles.py`, `groundline_schema/bundle.py` | `collections/BundleDialogs.tsx` | `collections pull/push` | `test_bundles.py`, `cli/tests/test_collections_commands.py` |
| Version storage keyed by dataset id | `snapshot_uri` | `svc/versioning.py`, `svc/storage.py` `get_snapshot_file` | — | — | `test_version_storage.py` |

## Not implemented yet

- Create rows from production traces: `api/traces.py` is an empty router;
  `dataset_rows.source_trace_id` exists but nothing writes it.
- PAT scopes: stored, not enforced.
- `datetime` column type: only `date` exists.
- Renaming a dataset: paths change only through moves and collection/folder renames.
- A store shared across collections, collection-level versions, per-collection permissions.
- Bundles carry rows only: imported rows start as `draft`, without assignees,
  comments, edit history or versions. Bundle import validates rows strictly,
  so a collection whose live rows break their own schema exports but won't
  import until fixed.
