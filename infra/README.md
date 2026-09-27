# Local infrastructure

Local development services (not for production). Defined in
[docker-compose.yml](docker-compose.yml): Postgres 16, MinIO, and the
containerised API (GL-1-11).

## Bring-up / teardown

```bash
make db-up     # docker compose -f infra/docker-compose.yml up -d
make db-down   # docker compose -f infra/docker-compose.yml down  (volumes persist)
```

Data lives in named volumes (`pgdata`, `miniodata`) and survives `make db-down`.
To wipe it, add `-v`: `docker compose -f infra/docker-compose.yml down -v`.

## Postgres

- Container: `infra-postgres-1`
- Host port: `localhost:5432`, or `POSTGRES_HOST_PORT` if set in `infra/.env`
  (untracked; compose reads it automatically). Set it when 5432 is taken by
  another Postgres � if the bind fails, Docker can leave the container running
  but detached from the compose network, and the api reports
  `database unreachable`. Containers reach Postgres on the network either way;
  only host tools (`make run`, host `psql`) use this port, so match
  `DATABASE_URL` in the root `.env` to it.
- User / password / database: `groundline` / `groundline` / `groundline`

Connection string for the app (`DATABASE_URL` in `.env`):

```
DATABASE_URL=postgresql+psycopg://groundline:groundline@localhost:5432/groundline
```

Plain libpq form (for tools that don't use SQLAlchemy):

```
postgresql://groundline:groundline@localhost:5432/groundline
```

### Reaching psql

`psql` doesn't need to be installed on the host — exec into the container:

```bash
docker exec -it infra-postgres-1 psql -U groundline -d groundline
# one-off query:
docker exec infra-postgres-1 psql -U groundline -d groundline -c 'select 1'
```

## MinIO (object storage for version snapshots, design §6)

- S3 API: `http://localhost:9000`, console: `http://localhost:9001`
- Root credentials: `minioadmin` / `minioadmin`

## Containerised API (GL-1-11)

The `api` service builds [Dockerfile](Dockerfile) with the **repo root** as
build context (it installs `shared/` then `backend/`). On start it runs
`alembic upgrade head` (idempotent — a no-op on an already-migrated database;
watch `docker logs infra-api-1` for the `[entrypoint]` lines), then serves
uvicorn on `http://localhost:8000`.

```bash
# full stack (postgres + minio + api)
docker compose -f infra/docker-compose.yml up -d --build

# just the api (postgres starts automatically as a healthy dependency)
docker compose -f infra/docker-compose.yml up -d --build api
docker compose -f infra/docker-compose.yml stop api

# smoke test
curl http://localhost:8000/v1/health   # -> {"status":"ok"}
```

The api service maps host port `8000`. If something else on your machine holds
8000, change the mapping to e.g. `"8080:8000"` in docker-compose.yml (the
container-side port stays 8000).

### Configuration

All settings are environment variables read by `backend/groundline_api/config.py`
(pydantic-settings; every var has a local-dev default, so the stack boots with no
`.env`). Compose passes them through with `${VAR:-default}` — set overrides in
`infra/.env` (compose reads it automatically) or in the shell environment.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://groundline:groundline@localhost:5432/groundline` | SQLAlchemy DB URL. Inside the compose network the api uses `...@postgres:5432/groundline` (set in docker-compose.yml). |
| `STORAGE_ENDPOINT_URL` | `http://localhost:9000` | S3-compatible endpoint (§6). Inside compose: `http://minio:9000`. |
| `STORAGE_BUCKET` | `groundline` | Snapshot bucket name. |
| `STORAGE_ACCESS_KEY` | `minioadmin` | Object-storage access key. **Secret in prod.** |
| `STORAGE_SECRET_KEY` | `minioadmin` | Object-storage secret key. **Secret in prod.** |
| `APP_ENV` | `dev` | Deployment profile. Anything other than `dev` makes the API refuse to boot while `AUTH_DEV_LOGIN=true`. |
| `AUTH_DEV_LOGIN` | `true` | Interim passwordless login (GL-1-9). See the warning below. |
| `APP_SECRET_KEY` | `change-me` | Signs session cookies. **Secret — generate a random value for any non-local deploy.** |
| `BOOTSTRAP_ADMIN_EMAIL` | `""` (compose default: `admin@example.com`) | If set, the API creates/promotes this admin at startup (idempotent, GL-1-8). |
| `PAT_DEFAULT_TTL_DAYS` | `90` | Default personal-access-token lifetime. |
| `ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID` / `ENTRA_CLIENT_SECRET` / `OIDC_REDIRECT_URI` / `OIDC_SCOPES` | empty / `openid,profile,email` | Entra OIDC (§5) — unused until Phase 3 (GL-3-11/GL-3-12). Client secret is a **secret**. |

> Note: `.env.example` could not be written from the GL-1-11 session
> (`.env*` files were permission-blocked); this table is the source of truth
> until it is mirrored there.

### Secrets

Never commit real secret values. `docker-compose.yml` contains only local-dev
defaults; real values go in an untracked `infra/.env` (or the deploy
environment / a compose `secrets:` block in prod). At minimum override
`APP_SECRET_KEY` and the `STORAGE_*` keys for anything beyond localhost.

### First-admin bootstrap on a fresh deploy

With `BOOTSTRAP_ADMIN_EMAIL` set (compose defaults it to `admin@example.com`),
a fresh deploy is usable immediately:

```bash
docker compose -f infra/docker-compose.yml up -d --build
curl -c cookies.txt -X POST http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"admin@example.com"}'
# admin session cookie is now in cookies.txt; e.g. create users:
curl -b cookies.txt -X POST http://localhost:8000/v1/users \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","display_name":"You","role":"editor"}'
```

Alternatively run `python -m groundline_api.bootstrap you@example.com` (see the
top-level README, "First admin & dev login").

### Warning: dev login is not real auth

`AUTH_DEV_LOGIN=true` means **anyone who can reach the API can log in as any
user by email, no password**. It is the interim auth until Entra OIDC lands in
Phase 3 (GL-3-11/GL-3-12) and is only legal under `APP_ENV=dev` — the API
refuses to start otherwise. Do not expose a dev-login deployment beyond
localhost / a trusted network.
