# CLI (`cli/`) and shared package (`shared/`)

## CLI: `groundline`

Typer app (`cli/groundline_cli/main.py`, console script `groundline`), httpx
client, PyYAML. Talks to the API with a personal access token.

**Config** (`config.py`): `GROUNDLINE_API_URL` and `GROUNDLINE_TOKEN` env
vars win; otherwise `~/.config/groundline/config.toml` with `api_url` and
`token`. Tokens come from the web app's API tokens page or
`POST /v1/auth/tokens`, and look like `glpat_…`.

### Commands

| Command | What it does |
|---|---|
| `groundline datasets list` | List datasets. |
| `groundline datasets create NAME --schema FILE [--description]` | Create a dataset and PUT its schema. |
| `groundline datasets delete NAME [--yes]` | Delete a dataset with no versions. |
| `groundline datasets diff NAME vA vB` | Rows added / removed / modified between two versions. |
| `groundline pull NAME@vN [--format jsonl\|json\|yaml] [--shape flat\|nested] [-o DIR] [--lock-file]` | Download, verify `content_hash`, write files, update `groundline.lock`. |
| `groundline pull --lock` | Restore every pinned dataset in its pinned format/shape; fail on hash mismatch (CI). |
| `groundline schema infer FILE [--key FIELD] [--records-key] [-o]` | Draft a schema YAML from a data file. |
| `groundline schema pull NAME [-o]` / `schema push NAME FILE [--dry-run] [--yes]` | Download / replace the current schema (absent columns are archived). |
| `groundline rows pull NAME [-o FILE] [--format] [--records-key] [--force]` | Write live rows to an editable file + a sync state file. |
| `groundline rows push NAME FILE [--dry-run] [--force] [--ignore-unknown] [--records-key]` | Key-matched upsert through `POST /sync`; reports conflicts, writes nothing on any conflict. |
| `groundline rows delete NAME [KEYS...] [--file FILE] [--yes]` | Soft-delete rows by key value. |

Typical file-first loop:

```bash
groundline schema infer questions.yaml --key id -o questions.schema.yaml
groundline datasets create questions --schema questions.schema.yaml
groundline rows push questions questions.yaml
# annotators edit in the web app ...
groundline rows pull questions -o questions.yaml
```

### Modules

| File | Role |
|---|---|
| `main.py` | App, sub-apps, top-level `pull`. |
| `client.py` | `ApiClient`: bearer auth, one method per endpoint, name → id resolution; non-2xx → `ApiError` with a one-line message. |
| `commands/_run.py` | `api()` builds a client; `fail()` prints and exits 1. |
| `commands/datasets.py`, `schema.py`, `rows.py` | Sub-commands. |
| `pull.py` | `pull_version`: fetch jsonl bytes, verify hash, then render json/yaml/nested locally with `groundline_schema.render` (byte-identical to the API). Writes `<out>/<name>/v<N>/rows.<fmt>`, `manifest.json`, `<name>.schema.json`. |
| `lock.py` | `groundline.lock` read/write/restore. Entry: `{version, content_hash, format?, shape?}`; missing format/shape = jsonl/flat. Written deterministically (sorted keys). Example: `groundline.lock.example`. |
| `rowfile.py` | Row files (nested records, key column first) and sync state at `<dir>/.groundline/rows/<dataset>/<file>.json` holding `revs` and `bases` (row hashes) from the last pull/push. Keeps other top-level keys of an existing YAML/JSON file. |
| `schemafile.py` | `infer_columns` (type inference rules in the module docstring), schema file load/render. |

Rules, error handling and test commands are in [`cli/AGENTS.md`](../cli/AGENTS.md).

## Shared package: `groundline_schema`

Installed editable into both backend and CLI (`make install`). Anything both
sides must agree on goes here.

| Module | Contents |
|---|---|
| `column_types.py` | `ColumnType` (8 types) and the `Column` Pydantic model (`key, label, type, options, required, order, archived, json_schema, is_key`). |
| `manifest.py` | `Manifest` model for `manifest.json` in each snapshot. |
| `jsonschema.py` | `build_json_schema(columns)` (draft 2020-12 sidecar), `nest_json_schema` for the nested shape. |
| `paths.py` | Dotted paths: `split_path`, `flatten(record, leaves)`, `nest(row)`, `row_hash` (sync identity). `flatten(nest(row)) == row`. |
| `records.py` | `parse_records` / `parse_document` for JSON, JSONL/NDJSON, YAML. Finds the records list (top-level list, export envelope, single list key, or `records_key`). YAML is composed, not constructed, so `NO`, `012`, dates stay text (`PlainScalar`) until a column types them. Per-record problems become `RecordProblem`, whole-file ones raise `RecordsError`. |
| `render.py` | `render(jsonl_bytes, fmt, shape)`: the single export renderer used by API and CLI. Multi-line strings as YAML block scalars. |
| `temporal.py` | `parse_date`, `format_date`, `is_date` for `YYYY-MM-DD`. |

Tests for the shared package live in `backend/tests/` (`test_paths.py`,
`test_jsonschema.py`, `test_temporal.py`, ...).
