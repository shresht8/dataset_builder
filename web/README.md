# Groundline web app

React + TypeScript (Vite). See [docs/groundline-dataset-builder.md](../docs/groundline-dataset-builder.md) §4
for the annotation UI design and the root [README](../README.md) for how this
fits with the rest of the repo.

## Commands

```bash
npm install
npm run dev      # http://localhost:5173, proxies /v1 to http://localhost:8000
npm run build    # production build (tsc -b && vite build)
npm run lint     # oxlint
```

## Layout

- `src/api/` — typed fetch client (`client.ts`) and request/response types
  (`types.ts`) mirroring the backend schemas and the six column types (§3).
- `src/auth/` — session state (`AuthContext`) and the dev-login form
  (`LoginForm`), kept isolated so Phase 3 can swap it for an Entra OIDC
  redirect (GL-3-11) without touching the rest of the app.
- `src/shell/` — post-login chrome (current user/role, sign out).
- `src/datasets/` — dataset list and the per-dataset route. The typed grid
  (keyboard nav, cell editors, detail drawer) is built in GL-2-6 using
  `@tanstack/react-table`.

## Auth

Interim auth is the dev login from GL-1-9: `POST /v1/auth/login {email}` sets
a signed session cookie, `GET /v1/auth/me` returns the current user and role.
The Vite dev server proxies `/v1` so the cookie is same-origin.
