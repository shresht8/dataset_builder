# Frontend (`web/`)

React 19 + TypeScript (strict, `tsc -b`) on Vite 6. Routing with
`react-router-dom` 7; the grid uses `@tanstack/react-table`. No state library,
no CSS framework: component state with hooks, styles in `src/index.css`
(plus `src/auth/auth.css`). Lint is `oxlint`. There are no frontend unit tests
yet; check UI changes in the browser.

```bash
make web-install   # npm install
make web-dev       # http://localhost:5173, proxies /v1 → http://localhost:8000
make web-build     # tsc -b && vite build (this is the type check)
make web-lint      # oxlint
```

The dev proxy (`vite.config.ts`) keeps the API same-origin so the session
cookie round-trips. Start the backend first (`make run` or the compose `api`
service).

## Structure (`web/src`)

| Path | What it does |
|---|---|
| `main.tsx`, `App.tsx` | Mount; `AuthProvider` → `AuthGate` (login form or `AppShell` + routes). |
| `api/client.ts` | The only place that calls the API. `apiFetch` (JSON, `credentials: 'include'`, throws `ApiError{status, message, body}` using the API's `detail`), `apiFetchForm` for multipart import. One exported function per endpoint. |
| `api/types.ts` | TS mirrors of the backend Pydantic schemas (`User`, `Dataset`, `Column`, `Row`, `RowComment`, `RowEdit`, `VersionRead`, import payloads, tokens). Keep in sync by hand when backend schemas change. |
| `auth/AuthContext.tsx` | `useAuth()` → `{user, loading, login, logout}`; loads `/auth/me` on start. |
| `auth/LoginForm.tsx`, `auth/authConfig.ts` | Reads `/auth/config`; shows "Sign in with Microsoft" (full-page redirect to `/v1/auth/sso/login`) and/or the dev-login email form. |
| `shell/AppShell.tsx` | Header: user, role, link to API tokens, sign out. |
| `datasets/DatasetListPage.tsx` | `/`: list datasets, link to each. |
| `datasets/DatasetPage.tsx` | `/datasets/:datasetId`: the annotation grid and everything around it (largest file). |
| `tokens/TokensPage.tsx` | `/tokens`: create (raw token shown once), list, revoke PATs. |

### Grid and panels (`datasets/`)

| File | Role |
|---|---|
| `DatasetPage.tsx` | Loads schema + rows, renders the table, owns active/editing cell state, keyboard handling, filters, row add/delete, dataset delete, and opens the panels below. Role flags: `canAnnotate` (annotator+), `canImport` (editor+: import, versions cut, deletes, key changes), `isAdmin` (user list for assignee names). |
| `Cell.tsx` | `CellDisplay` / `CellEditor` per column type. Editors only produce schema-valid values (select from options, number input, checkbox, date input, multi-select picker). |
| `DetailDrawer.tsx` | Side panel for `long_text` (saves on blur) and `json` (pretty text, Format/Save, client-side parse, API JSON Schema errors inline). |
| `Workflow.tsx` | `StatusControl`, `AssigneeControl` per row. |
| `FilterBar.tsx`, `views.ts` | Status / assignee / search filters and saved views. Built-in views: All rows, My queue (default), Needs review, Missing required. Custom views live in `localStorage` per dataset. |
| `CommentsPanel.tsx` | Row comments + read-only edit history. |
| `ConflictDialog.tsx` | Shown on a 409 from PATCH: take the server's row or re-apply your change on the fresh `rev`. Never silently drops an edit. |
| `ImportWizard.tsx` | Upload → map columns → review errors with inline fix/skip → commit. Tracks handled row indices across retry rounds because the API has no import session. |
| `VersionsPanel.tsx` | List versions; editors cut a new one (notes, include unapproved). No edit/delete. |
| `DeleteDatasetDialog.tsx`, `ConfirmDialog.tsx` | Typed-name confirmation for dataset delete; generic confirm (never `window.confirm`). |
| `dates.ts`, `json.ts` | `YYYY-MM-DD` helpers (never `new Date("YYYY-MM-DD")`, which shifts a day west of UTC); JSON summarise/format/parse with line/column errors. |

### Keyboard model (grid)

Arrow keys and Tab/Shift+Tab move the active cell. Enter opens the drawer
for `long_text`/`json`, toggles a `boolean`, otherwise starts editing; Space
toggles a `boolean`. While editing: Enter commits, Tab commits and moves,
Escape cancels. Panels and dialogs close on Escape and return focus to the
grid. Status/assignee controls are plain native controls outside the arrow-key
navigation.

## Conventions

- New API calls go in `api/client.ts` with types in `api/types.ts`; components
  never call `fetch` directly (exception: `authConfig.ts`).
- Gate edit affordances by role in the UI, but treat the API as the real
  check; show the API's error message (`ApiError.message`) instead of
  guessing.
- Row PATCH must send the row's current `rev` and handle 409 with
  `ConflictDialog`.
- Dialogs: app-styled components, Escape closes, focus returns to the
  originating element.

## Known gaps

- Datasets are created and their schemas edited through the API/CLI; the web
  app has no create-dataset or schema editor yet.
- `GET /users` is admin-only, so non-admins see other assignees as
  `User <id prefix>` and can only "Assign to me"; admins get a full picker.
