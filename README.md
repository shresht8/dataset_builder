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
| [`web/`](web/) | React + TypeScript annotation UI (Vite): dev login, typed grid, filters, import | §4 |
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

## First admin & dev login (interim, pre-Entra)

Create (or promote) the first admin — idempotent, safe to re-run:

```bash
python -m groundline_api.bootstrap admin@example.com --name "Admin"
```

Alternatively set `BOOTSTRAP_ADMIN_EMAIL=admin@example.com` and the API
ensures that admin exists at startup.

Interactive auth is a passwordless **dev login** (`POST /v1/auth/login` with
`{"email": ...}`) that sets a signed session cookie. It is gated by
`AUTH_DEV_LOGIN` (default `true`) and only legal when `APP_ENV=dev` (default);
the API refuses to start with dev login enabled under any other profile.
Entra OIDC replaces it in Phase 3. Machine access uses personal access tokens
(`POST /v1/auth/tokens`, sent as `Authorization: Bearer glpat_...`; default
TTL 90 days).

## Web app

```bash
make web-install   # npm install
make web-dev        # dev server on http://localhost:5173, proxies /v1 to :8000
make web-build       # production build
make web-lint         # oxlint
```

The dev server proxies `/v1` to `http://localhost:8000` so the session cookie
from dev login round-trips same-origin. Start the backend first (`make run` or
the `api` container in `infra/docker-compose.yml`), then sign in at
`http://localhost:5173` with a known user (e.g. `admin@example.com`).
