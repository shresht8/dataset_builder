# Web (`web/`) agent notes

Read [../AGENTS.md](../AGENTS.md) first. Code map: [../docs/frontend.md](../docs/frontend.md).

React 19 + TypeScript (strict) on Vite 6, react-router-dom 7, TanStack Table.
No state library, no CSS framework: hooks for state, plain CSS in
`src/index.css` and `src/auth/auth.css`. Lint is `oxlint`.

## Commands (repo root)

- `make web-install`, `make web-dev` (:5173, proxies `/v1` to :8000)
- `make web-build` runs `tsc -b && vite build`; this is the type check.
- `make web-lint`

There are no frontend unit tests. Verify UI changes in a browser (golden path
plus the edge you touched) and run build and lint.

## Rules

- Call the API only through `src/api/client.ts`. Type responses in
  `src/api/types.ts`, and update it in the same change as any backend schema
  change (nothing generates it).
- Row PATCH sends the row's current `rev` and handles 409 through the conflict
  dialog. Never drop a conflicting edit silently.
- Role gating in the UI is a convenience; the API is the real check. Show
  `ApiError.message` rather than guessing a reason.
- `date` values are `YYYY-MM-DD` strings. Use `src/datasets/dates.ts`.
- Confirmations use the app's own dialog components. Panels and dialogs close
  on Escape, except the conflict dialog, which must be resolved explicitly.
- Cell editors must only produce values the schema accepts.
- Grid keyboard handling lives in `handleTableKeyDown` in
  `src/datasets/DatasetPage.tsx`. Change keys there and check `boolean`,
  `long_text`, `json` and `multi_select` cells.
- Match the existing style: CSS classes in `index.css`, no new UI or state
  library for a single use.

## Anti-patterns

- `fetch` in a component (the one exception is `auth/authConfig.ts`).
- `window.confirm`.
- `new Date("YYYY-MM-DD")` (shifts a day west of UTC), or storing dates as
  timestamps.
- Sending a row PATCH without `If-Match`/`rev`, or swallowing a 409.
- Editing `api/types.ts` without the matching backend schema.
