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
| `datasets/DatasetListPage.tsx` | Route `/`: collections, then unfiled datasets; new collection, new unfiled dataset, import collection. |
| `collections/CollectionPage.tsx` | Route `/collections/:collectionId`: folder tree, content pane, Store tab, export. Dialogs for folders, move and delete in `collections/dialogs.tsx`. |
| `collections/NewDatasetWizard.tsx` | Multi-file queue: details → infer or pick a store entry → create → the import wizard on the same file. A store pick that the import would refuse blocks Create unless confirmed. Until rows are imported, "Back to schema" deletes the empty dataset (and the store entry it added) and returns to the schema step; cancelling offers to delete it. |
| `collections/StorePanel.tsx`, `BundleDialogs.tsx`, `DatasetBreadcrumb.tsx` | Store list and editing; export/import collection dialogs; the path breadcrumb on a dataset page. |
| `schema/SchemaEditor.tsx` | The one column editor, used for store entries and unfiled datasets. |
| `datasets/SchemaPanel.tsx` | Dataset schema: direct edit when unfiled; change / duplicate & edit the store entry when in a collection. |
| `datasets/DatasetPage.tsx` | Route `/datasets/:datasetId`. The grid and everything around it: cell and keyboard state, filters, row add/delete, role gating. The largest file. |
| `datasets/*.tsx` (other) | Panels and dialogs opened from `DatasetPage`: cell renderers/editors, detail drawer, status/assignee controls, filter bar, comments + history, conflict dialog, import wizard, versions panel, confirm dialogs. |
| `datasets/views.ts` | Built-in and saved filter views. Saved views are in `localStorage`, per dataset. |
| `datasets/dates.ts`, `json.ts` | Pure helpers for `date` and `json` values. |
| `tokens/TokensPage.tsx` | Route `/tokens`: personal access tokens. |

Find a screen's behaviour by opening its file; filenames match the component.
The set of panels changes often, so list `web/src/datasets/` rather than
trusting this table for completeness.

## Rules

Rules and anti-patterns for this layer are in [`web/AGENTS.md`](../web/AGENTS.md).

## Known gaps

- `GET /users` is admin-only, so non-admins cannot see assignee names and can
  only assign to themselves.
