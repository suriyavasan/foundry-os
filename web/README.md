# Company OS web

Vite + React 19 + TypeScript (strict) SPA. Server state: TanStack Query. Routing: React Router 7.
Styling: Tailwind CSS v4 on CSS-variable tokens (`src/styles/tokens.css`; dark default, light toggle).

```bash
pnpm install
pnpm dev          # http://localhost:5173, proxies /api -> http://localhost:8700
pnpm test         # vitest
pnpm typecheck && pnpm lint && pnpm build
pnpm gen:api      # regenerate src/api/schema.d.ts from a running backend
```

Set `API_PROXY_TARGET` to point the dev proxy at a different backend URL.

## Layout

- `src/api/` fetch client (`apiFetch`, typed `ApiError`), hand-written `types.ts` mirroring `docs/api.md`, query/mutation hooks per resource.
- `src/components/ui/` design-system primitives; `src/components/layout/` theme provider and company switcher; `src/components/avatar/` employee avatars.
- `src/features/<area>/` one folder per canvas area (widgets in `features/canvas/widgets`, drawer panels in `features/panels`).
- `src/lib/` pure helpers (money, org tree, lifecycle actions, formatting) with unit tests.

## Rules

- No hard-coded colours; use tokens. No fake data: unbuilt subsystems render `NotAvailable` with the milestone from the API.
- Money is integer micro-USD end to end; only convert at the form boundary (`src/lib/money.ts`).
- Every data view handles loading, empty, error and not-available states. No inert buttons.
