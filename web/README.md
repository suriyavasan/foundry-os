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

## Onboarding, AI gate and animation (M10)

- `src/features/onboarding/` is the first-run flow: six full-screen steps (Welcome, Account, Company, Connect your AI, How work gets done, Meet your team). The step order and what is allowed (no way back behind a created account or company) is the pure state machine in `src/lib/onboarding.ts`. The same two AI steps are the **AI gate** (`AiGate`, mounted by `CompanyLayout` when `GET /ai-setup` says `ready: false`) and the Settings -> **AI** panel (`?panel=ai`). A backend that answers 404 there (older than M10) never gates and the AI views say "not built yet".
- The onboarding is a separate lazy chunk (`AuthGate` and `CompanyLayout` import it on demand), so the canvas bundle does not carry it or the `motion` library.
- **Effects** (`src/features/onboarding/fx/`): spotlight, background beams, text-generate reveal, multi-step loader, moving-border button, placeholders-and-vanish input, card hover. They are in the style of the effects popularised by Aceternity UI, **written from scratch** on `motion` (MIT) and the design tokens. No Aceternity source was copied: the licence of its free components is not clearly stated, so we did not rely on it. Every effect has a still, complete fallback under `prefers-reduced-motion` (`src/lib/motion.ts`), and decorative layers are `aria-hidden`.
- The multi-step loader shows only what the server reported (`checks[]` of the provider test): while the request is in flight the first row spins and nothing else is claimed; there are no timers standing in for progress.
- Secrets: the provider and CLI keys are write-only. They go to `/credentials` once, the field is emptied as soon as the server has them, providers and CLIs reference the credential id, and error text is scrubbed of the key before it is shown.
- Command Center (`src/features/commandcenter/`) and Reflections/Standards (`src/features/reflections/`) replace the Founder's-notes widget on the canvas; the notes panels (`?panel=notes`, `?panel=note:<id>`) are unchanged.
