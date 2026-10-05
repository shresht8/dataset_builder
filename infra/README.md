# Local infrastructure

Local development services (not for production). Defined in
[docker-compose.yml](docker-compose.yml): Postgres 16, RustFS (S3-compatible
object storage), and the containerised API (GL-1-11).

## Bring-up / teardown

```bash
make db-up     # docker compose -f infra/docker-compose.yml up -d
make db-down   # docker compose -f infra/docker-compose.yml down  (volumes persist)
```

Data lives in named volumes (`pgdata`, `storagedata`) and survives `make db-down`.
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

## RustFS (object storage for version snapshots, design §6)

- S3 API: `http://localhost:9000`
- Console: `http://localhost:9001/rustfs/console/` (note the path — the
  console root `/` returns `403`, not `200`)
- Root credentials: `minioadmin` / `minioadmin` (same values as
  `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` below — the app talks to
  storage using this credential in local dev; there is no separate IAM
  policy layer locally). These are dev-only placeholders, kept as-is on
  purpose: changing them would also require changing `config.py`'s defaults
  and every developer's local `.env`.

### Why RustFS, not MinIO (2026-09-27)

This service was originally MinIO. On 2026-09-27 `minio/minio` stopped being
pullable — `docker pull minio/minio` fails with `pull access denied ...
repository does not exist or may require 'docker login'`, including for
previously-working pinned tags, and `quay.io/minio/minio` has no manifest
either. The only working MinIO container on the development machine was
running from an image cached locally from 2025-09-07, so a fresh `make db-up`
on any other machine would fail at the `docker pull` step. RustFS
(`rustfs/rustfs:1.0.0`, pinned, not `latest`) is an S3-compatible object
store that pulls anonymously and was verified against the app's actual
access pattern with boto3: head/create bucket, byte-identical put/get, list
with prefix/delimiter/pagination, `NoSuchKey` on a missing object,
`If-None-Match: *` correctly refused with `PreconditionFailed`, a 12 MiB
multipart upload, a presigned GET, a wrong-secret request rejected, and
batch delete — 14/14 checks passed, and data survived a container restart.

The service is named generically (`storage`, not `rustfs`), matching the
`STORAGE_*` env var names, so a future swap doesn't require renaming the
service again.

### Bucket provisioning (GL-3-1)

`make db-up` / `docker compose -f infra/docker-compose.yml up -d` creates the
snapshot bucket automatically via a one-shot `storage-init` service: it waits
for `storage`'s healthcheck, then creates `STORAGE_BUCKET` (default
`groundline`) if it doesn't already exist, and exits 0. Re-running it is a
no-op (`head_bucket` first, `create_bucket` only if missing) — safe on every
`db-up`. The `api` service `depends_on: storage-init: condition:
service_completed_successfully`, so the bucket exists before the API is
usable for storage.

`storage-init` reuses the `api` image (built from [Dockerfile](Dockerfile))
and runs a short inline Python script with `boto3`, which is already a
backend dependency (`backend/pyproject.toml`, "S3-compatible snapshot
storage (§6)"). This is deliberately **not** an `mc`-style client image:
`minio/mc` has been removed from Docker Hub and its `quay.io/minio/mc`
replacement requires registry login, so neither pulls anonymously, and
RustFS ships no equivalent CLI image either. Reusing the already-built api
image avoids adding any new external image dependency, and the same
reasoning holds regardless of which S3-compatible server backs `storage`.

**App code never creates buckets** — `services/storage.py` (GL-3-2) only
reads and writes objects in a bucket that already exists.

### Data directory and permissions

RustFS runs its process as UID/GID `10001` inside the container (not root).
The compose file mounts the named volume `storagedata` at `/data`, which
Docker creates with permissions the container can write to — no action
needed for the default setup. If you instead bind-mount a host directory at
`/data` (not done here, but worth knowing if you customize this), you must
`chown 10001:10001` that host directory first, or RustFS fails to start.

The previous MinIO setup used a different named volume, `miniodata`, which
this change does not reuse — nothing here assumes RustFS can read MinIO's
on-disk format. The old `infra_miniodata` Docker volume still exists after
this change (compose never deletes volumes it stops managing) but is now
unused; it only ever held an empty bucket. Removing it is your call, not
automated:

```bash
docker volume rm infra_miniodata
```

### Bucket layout and access policy

```
datasets/{name}/v{n}/manifest.json
datasets/{name}/v{n}/schema.json
datasets/{name}/v{n}/rows.jsonl
```

`dataset_versions.snapshot_uri` = `s3://{bucket}/datasets/{name}/v{n}/`.
`{name}` is the dataset name (unique, key-safe: `^[a-z0-9][a-z0-9_-]{0,99}$`,
GL-3-15); `{n}` is the integer version. Versions are immutable, so these keys
are never overwritten once written (design §6).

Access policy: the bucket is **private** — no public-read policy is applied.
In local dev the app authenticates with the same root-equivalent credential
via `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY`. There's a single credential
and no per-dataset ACL; anyone who can authenticate to the API with read
access to a dataset can read its version objects through the API, which is
the only path — nothing here makes the bucket contents reachable directly
from a browser.

### Production equivalent

Unchanged by the RustFS switch: any S3-compatible endpoint works because the
app only uses `STORAGE_ENDPOINT_URL` / `STORAGE_BUCKET` /
`STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` (`backend/groundline_api/config.py`)
— e.g. AWS S3 (omit `STORAGE_ENDPOINT_URL` or point it at the regional
endpoint), a self-hosted RustFS/MinIO cluster, or another S3-compatible
provider. For production:

- Provision the bucket out of band (Terraform, the provider console, or a
  one-off script — whatever the deploy environment supports), same as the
  local `storage-init` service does. The app is never granted
  bucket-creation permission, only read/write/list/delete on objects under
  `datasets/*` in the one bucket named by `STORAGE_BUCKET`.
- Use a scoped IAM user/access key (not a root/admin key), restricted to that
  bucket and prefix.
- Set `STORAGE_ENDPOINT_URL`, `STORAGE_BUCKET`, `STORAGE_ACCESS_KEY`,
  `STORAGE_SECRET_KEY` via the deploy environment's secret manager or a
  compose `secrets:` block — never commit real values. `.env.example`
  documents the variable names with local-dev placeholders only.

## Containerised API (GL-1-11)

The `api` service builds [Dockerfile](Dockerfile) with the **repo root** as
build context (it installs `shared/` then `backend/`). On start it runs
`alembic upgrade head` (idempotent — a no-op on an already-migrated database;
watch `docker logs infra-api-1` for the `[entrypoint]` lines), then serves
uvicorn on `http://localhost:8000`.

```bash
# full stack (postgres + storage + api)
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
| `STORAGE_ENDPOINT_URL` | `http://localhost:9000` | S3-compatible endpoint (§6). Inside compose: `http://storage:9000`. |
| `STORAGE_BUCKET` | `groundline` | Snapshot bucket name. |
| `STORAGE_ACCESS_KEY` | `minioadmin` | Object-storage access key. **Secret in prod.** |
| `STORAGE_SECRET_KEY` | `minioadmin` | Object-storage secret key. **Secret in prod.** |
| `APP_ENV` | `dev` | Deployment profile. Anything other than `dev` makes the API refuse to boot while `AUTH_DEV_LOGIN=true`. |
| `AUTH_DEV_LOGIN` | `true` | Interim passwordless login (GL-1-9). See the warning below. |
| `APP_SECRET_KEY` | `change-me` | Signs session cookies. **Secret — generate a random value for any non-local deploy.** |
| `BOOTSTRAP_ADMIN_EMAIL` | `""` (compose default: `admin@example.com`) | If set, the API creates/promotes this admin at startup (idempotent, GL-1-8). |
| `PAT_DEFAULT_TTL_DAYS` | `90` | Default personal-access-token lifetime. |
| `AUTH_SSO_ENABLED` | `false` | Turns on Entra/OIDC sign-in (GL-3-11). Dev login and PATs keep working alongside it. |
| `OIDC_DISCOVERY_URL` | empty → derived from `ENTRA_TENANT_ID` (compose default: the mock, **mock-only**) | OIDC discovery document. Leave empty for real Entra. |
| `ENTRA_TENANT_ID` | empty | Real Entra tenant (used to derive the discovery URL). |
| `ENTRA_CLIENT_ID` / `ENTRA_CLIENT_SECRET` | empty (compose default: `groundline-local` / `mock-secret`, **mock-only**) | App registration client credentials. Client secret is a **secret**. |
| `OIDC_REDIRECT_URI` | `http://localhost:5173/v1/auth/sso/callback` | Must go through the **web origin** (Vite dev proxy / the web server), so the post-login redirect to `/` lands in the web app. |
| `OIDC_SCOPES` | `openid,profile,email` | Requested scopes. The `groups` claim comes from the app registration's token configuration, not a scope. |
| `ROLE_MAPPING` | `{"default":"viewer"}` (compose: mock fixture GUIDs, **mock-only**) | JSON: Entra group **object ID** → role, plus `default`. Highest-privilege match wins. |

> Note: `.env.example` could not be written from the GL-1-11 session
> (`.env*` files were permission-blocked); this table is the source of truth
> until it is mirrored there.

### Local SSO with the mock IdP (GL-3-12)

No Entra tenant is needed for local development: the `sso-mock` profile runs
[navikt/mock-oauth2-server](https://github.com/navikt/mock-oauth2-server)
(pinned `3.0.1`) as an Entra stand-in that issues Entra-shaped ID tokens
(`oid`, `tid`, `email`/`preferred_username`, `name`, `groups` as GUIDs).
Everything about it is **mock-only**.

```bash
# start the mock and recreate the api with SSO on (dev login stays on too)
AUTH_SSO_ENABLED=true docker compose -f infra/docker-compose.yml --profile sso-mock up -d
npm --prefix web run dev          # http://localhost:5173
```

On the login page choose "Sign in with Microsoft" (GL-3-11). The mock's login
page asks for a **username** and optional **claims**. Sign in as one of the
fixture users in [mock-oidc/fixture-users.json](mock-oidc/fixture-users.json):
type the username (e.g. `mock-editor`) and paste that user's `claims` object.

| Username | Groups (fake GUIDs) | Role via compose `ROLE_MAPPING` |
|---|---|---|
| `mock-admin` | `a1a1a1a1-…-000000000001` | admin |
| `mock-editor` | `e2e2e2e2-…-000000000002` + an unmapped group | editor |
| `mock-annotator` | `a3a3a3a3-…-000000000003` | annotator |
| `mock-viewer` | an unmapped group only; **no `email`** (tests the `preferred_username` fallback) | viewer (default) |

**Hostname split.** The browser follows the authorize redirect, while the
api container fetches discovery, token and JWKS. This mock derives its
`iss` from the request Host, so both sides must use the same host:port. The
mock is therefore reached as `http://mock-oidc.localhost:8090` everywhere:
browsers and curl resolve any `*.localhost` name to 127.0.0.1 (port 8090 is
published), and inside compose that name is a network alias of the mock,
which also listens on 8090. No hosts-file edit is needed. Verified
2026-09-27: discovery from the host and from `infra-api-1` returns the same
issuer `http://mock-oidc.localhost:8090/entra` and endpoints.

Caveat: **Windows' own resolver does not resolve `*.localhost`** (browsers
and curl do it themselves). So a *host-run* API (`make run`) cannot reach the
mock unless you add `127.0.0.1 mock-oidc.localhost` to the hosts file. Use
the containerised api (the default above) for SSO.

For the switch to a real tenant, see [ENTRA-SETUP.md](ENTRA-SETUP.md).

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
user by email, no password**. It stays on alongside SSO until Entra go-live
(GL-3-14) switches it off, and is only legal under `APP_ENV=dev` — the API
refuses to start otherwise. Do not expose a dev-login deployment beyond
localhost / a trusted network.
