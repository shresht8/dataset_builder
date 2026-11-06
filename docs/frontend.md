# Frontend (`web/`)

React 19 + TypeScript (strict, `tsc -b`) on Vite 6, react-router-dom 7,
`@tanstack/react-table`. No state library and no CSS framework: component
state with hooks, styles in `src/index.css` and `src/auth/auth.css`. Lint is
`oxlint`. There are no frontend unit tests; check UI changes in a browser.

```bash
make web-install   # npm install
make web-dev       # http://localhost:5173, proxies /v1 to http://localhost:8000
make web-build     # tsc -b && vite build (this is the type check)
make web-lint      # oxlint
```

The dev proxy (`web/vite.config.ts`) keeps the API same-origin so the session
cookie round-trips. Start the backend first.

This doc is a map and a list of rules. It does not restate what the code says;
read the file for details such as exact props, flags, view lists and keys.

## Where things live (`web/src`)

| Path | What it is |
|---|---|
| `main.tsx`, `App.tsx` | Entry, `AuthProvider`, auth gate, routes. |
| `api/client.ts` | The only module that calls the API: one exported function per endpoint, `ApiError{status, message, body}`. |
| `api/types.ts` | Hand-written TS mirrors of the backend Pydantic schemas. |
| `auth/` | `useAuth()` context, login form (dev login and/or SSO, chosen from `GET /auth/config`). |
| `shell/AppShell.tsx` | Page frame. |
| `datasets/DatasetListPage.tsx` | Route `/`. |
| `datasets/DatasetPage.tsx` | Route `/datasets/:datasetId`. The grid and everything around it: cell and keyboard state, filters, row add/delete, role gating. The largest file. |
| `datasets/*.tsx` (other) | Panels and dialogs opened from `DatasetPage`: cell renderers/editors, detail drawer, status/assignee controls, filter bar, comments + history, conflict dialog, import wizard, versions panel, confirm dialogs. |
| `datasets/views.ts` | Built-in and saved filter views. Saved views are in `localStorage`, per dataset. |
| `datasets/dates.ts`, `json.ts` | Pure helpers for `date` and `json` values. |
| `tokens/TokensPage.tsx` | Route `/tokens`: personal access tokens. |

Find a screen's behaviour by opening its file; filenames match the component.
The set of panels changes often, so list `web/src/datasets/` rather than
trusting this table for completeness.

## Rules

- Call the API only through `api/client.ts` and type it in `api/types.ts`.
  Components do not call `fetch` (the one exception is `auth/authConfig.ts`).
- When a backend schema changes, update `api/types.ts` in the same change; no
  generator keeps them in sync.
- Row PATCH sends the row's current `rev` and handles 409 through the conflict
  dialog. Never drop a conflicting edit silently.
- Role gating in the UI is a convenience. The API is the real check; show
  `ApiError.message` instead of guessing a reason. Role flags are computed in
  `DatasetPage.tsx` from the user's role rank.
- `date` values are `YYYY-MM-DD` strings. Never `new Date("YYYY-MM-DD")` (it
  shifts a day west of UTC); use `datasets/dates.ts`.
- Confirmations use the app's own dialog components, never `window.confirm`.
  Panels and dialogs close on Escape, except the conflict dialog, which must
  be resolved explicitly.
- Cell editors must only produce values the schema accepts.
- The grid handles its own keyboard model in `DatasetPage.tsx`
  (`handleTableKeyDown`). Change keys there, and keep `boolean`, `long_text`,
  `json` and `multi_select` cells in mind.

## Known gaps

- No create-dataset screen and no schema editor; use the API or the CLI.
- `GET /users` is admin-only, so non-admins cannot see assignee names and can
  only assign to themselves.
