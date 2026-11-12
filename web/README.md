# NovaCRM onboarding dashboard (web/)

React + Vite + TypeScript single-page app for the onboarding console. It replaces the plain-HTML dashboard in
`backend/app/static/`. It is served under `/ui/` by FastAPI and talks to the same-origin API using the session cookie.

## Commands

```
npm install
npm run dev        # http://localhost:5173/ui/  proxies the API to http://127.0.0.1:8000 (make api)
npm run dev:mock   # same, but the API is answered in the browser by MSW (no backend needed; sign in admin/admin)
npm run lint       # eslint (0 warnings) + prettier --check
npm run test       # vitest + Testing Library, API mocked with MSW, no network
npm run build      # tsc -b && vite build -> web/dist (base /ui/)
```

## Stack

React 19, React Router 7, TanStack Query 5, Tailwind CSS 4, Radix UI primitives (own components in
`src/components/ui`), cmdk (searchable combobox), Recharts 3, lucide-react, zod 4, Vitest 4, MSW 2. Versions are
pinned exactly in `package.json`.

## Layout

```
src/
  main.tsx, App.tsx          entry, providers, routes (basename /ui)
  index.css                  Tailwind theme tokens (light theme only)
  lib/
    api.ts                   fetch wrapper: JSON, same-origin, 401 -> /ui/login?next=..., zod-validated responses
    schemas.ts               zod schemas for every endpoint (existing + /aes, /migration/*, /agents/status)
    queries.ts               TanStack Query hooks; runs/detail/escalations poll every 3 s, metrics 15 s, agents 10 s
    run-state.ts             status labels, the 7-step pipeline derivation (port of the old derive())
    activity.ts              plain-language audit labels, transcript parsing
    chart-tooltip.ts         what every chart tooltip shows (pure, tested)
    phone.ts, migration.ts, format.ts, prefs.ts
  components/
    ui/                      Button, Badge, Tooltip, Select, Combobox, DropdownMenu, Dialog/ConfirmDialog, Sheet,
                             Tabs, Toast, Pagination (5/10/25/50, remembered per list), Table/SortTh, Skeleton, ...
    layout/                  AppShell, Sidebar, Topbar (title, breadcrumb/back, live indicator, user menu)
    charts/                  Recharts wrappers with custom tooltip cards
    run/                     Stepper, NowBanner, DecisionPanel, Activity/Progress/Migration/AI-and-tools tabs
  pages/                     Login, Agents, Overview, Runs, RunDetail, Escalations, AccountExecutives, Migration,
                             AiUsage, ToolCalls
  mocks/                     in-memory data + MSW handlers implementing the API contract (dev:mock and tests only)
mock-public/                 MSW service worker; used only in mock mode, never copied to dist
```

Polling uses TanStack Query's `refetchInterval`; it pauses while the tab is hidden and refetches on focus.
All data is rendered as text. `dangerouslySetInnerHTML` is banned by an ESLint rule (emails and transcripts are
untrusted).

## Routes

`/ui/login`, `/ui/agents`, `/ui/overview` (default), `/ui/runs`, `/ui/runs/:id`, `/ui/escalations`,
`/ui/escalations/:runId` (side panel), `/ui/aes`, `/ui/migration`, `/ui/ai`, `/ui/tools`. Old hash links
(`/ui#/runs/abc`) are redirected to the matching path.

## API contract notes

Built against `backend/app/main.py` and `backend/app/auth.py` as of 12 Nov 2026, plus these endpoints that the
backend is adding (mocked in `src/mocks/handlers.ts` until they exist):

- `GET /aes`, `POST /aes` (201 / 409 duplicate / 422 `{detail}`), `PATCH /aes/{email}`
- `GET /migration/summary`, `GET /migration/data?side&limit&offset&only_problems`, `POST /migration/demo/fix`,
  `POST /migration/demo/reset`. Row `problems` codes the UI knows: `duplicate`, `missing_required`,
  `missing_in_dest` (others are shown as plain text).
- `GET /agents/status`

The run detail's `tool_calls` have no `id`, so the run page's "AI and tools" tab lists tool calls from
`GET /tool-calls?run_id=...` and loads input/output on demand from `GET /tool-calls/{id}`.

## Integration (to do after the backend work lands)

1. **Serve `web/dist` at `/ui`** in `backend/app/main.py`:
   - Mount `web/dist/assets` at `/ui/assets` (`StaticFiles`). Vite emits hashed files there; send
     `Cache-Control: public, max-age=31536000, immutable` for them.
   - SPA fallback: `GET /ui` and `GET /ui/{path:path}` (except `/ui/config` and `/ui/assets/*`) return
     `web/dist/index.html` with `Cache-Control: no-cache`.
   - Location of the build: an env var or setting (for example `WEB_DIST=/app/web/dist`), defaulting to
     `<repo>/web/dist`.
2. **Auth guard (`backend/app/auth.py`)**:
   - `/ui/login` must stay public and return the SPA `index.html` (the router's `login_page` callback), keeping the
     "already signed in -> redirect to /ui" behaviour.
   - `/ui/assets/*` stays public (already in `PUBLIC_PREFIXES`). The favicon is bundled into `/ui/assets/`.
   - Unauthenticated `GET` of any `/ui/...` page path (not just `/ui`) should 303 to
     `/ui/login?next=<path>`. `safe_next` already accepts `/ui/...` paths.
   - `/agents/status` is a normal protected API route (cookie required, no special case).
3. **Dockerfile**: add a Node stage, `FROM node:22-alpine AS web`, `WORKDIR /web`, copy `web/package.json` and
   `web/package-lock.json`, `npm ci`, copy `web/`, `npm run build`. Then in the Python stage
   `COPY --from=web /web/dist /app/web/dist`.
4. **Remove `backend/app/static/`** and the `STATIC` constant/mount. Keep `/ui/config`.
5. **Tests**: update backend tests that load `static/index.html`, `static/login.html` or `/ui/assets/app.js`
   (search `tests/` for `static` and `/ui/assets`) to assert against the SPA fallback instead (for example that
   `/ui/runs/abc` returns `index.html` when signed in, and redirects to login when not). Build `web/dist` before
   those tests or point them at a fixture `index.html`.
6. **CORS**: `allow_origins=["http://localhost:5173"]` in `main.py` is not needed for `npm run dev`, which uses the
   Vite proxy (same origin). It can stay or go.
7. **Makefile** (optional): `make web` -> `cd web && npm ci && npm run build`, `make web-dev` -> `npm run dev`.
