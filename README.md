# Groundline — Dataset Builder

Tabular annotation tool that produces versioned evaluation datasets. See the
design doc: [docs/groundline-dataset-builder.md](docs/groundline-dataset-builder.md).

## Components

| Dir | Component | Design ref |
|---|---|---|
| [`shared/`](shared/) | `groundline_schema` — column types, manifest, JSON Schema sidecar (single source of truth shared by API and CLI) | §3, §7 |
| [`backend/`](backend/) | `groundline_api` — FastAPI service: data model, annotation API, Entra OIDC auth, versioning, import/export | §3–§8 |
| [`cli/`](cli/) | `groundline` — developer CLI: `list`, `pull`, `diff`, lock file | §7 |
| [`backend/migrations/`](backend/migrations/) | Alembic migrations for the data model | §3 |
| [`infra/`](infra/) | Local dev services (Postgres, object storage) | — |

## Getting started

```bash
make install     # editable-install shared, backend, cli
make db-up       # start Postgres + object storage
make migrate     # apply migrations
make run         # run the API locally
make test        # run all tests
```

Copy [`.env.example`](.env.example) to `.env` and fill in Entra OIDC and storage
settings before running.
