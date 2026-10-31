# M20 — Chat-first canvas: the Command Center as a chat, a compact org map with faces

Status: in progress (2026-10-30). Rules: `AGENTS.md` (web conventions: TanStack Query hooks in `src/api/`, design
tokens only, every view handles loading/empty/error/not-available, no inert buttons).

## Why
The Command Center reads like a terminal log (mono font, a status badge per line, progress lines, chips, links,
timestamps all expanded) and sits below a large organization tree. The founder wants the conversation first, the
org chart small, and agents that look like people.

## 1. Canvas layout (`features/canvas/CanvasPage.tsx`)
* Row 1 (the first fold), `xl` 12 columns: **Command Center chat** `xl:col-span-8` + **Organization** compact
  `xl:col-span-4`, both `tall`. `md`: chat full width, org full width below. Phones: chat, then org.
* Row 2: Live activity (8) + Goals & plan (4). Row 3+: Reflections (4), Work, Approvals, Spend, Wallet, Artifacts,
  Routines, Skills (4 each), CLI usage (8). Update the layout doc comment and tests.
* On load, the chat is in view without scrolling at 1280×800 and 1440×900 (chat height ≈ `min(640px, 70vh)`).

## 2. Command Center as a chat (`features/commandcenter/*`, `features/canvas/widgets/CommandCenterWidget.tsx`)
* Chronological, **newest at the bottom**, auto-scroll to bottom on new messages unless the user has scrolled up
  (then show a "New messages ↓" pill). Composer pinned at the bottom (`CommandInput`, single line growing to 6
  lines, Enter sends, Shift+Enter newline, `/` opens the existing command menu). Load older on scroll to top
  (existing infinite feed).
* Message bubbles, proportional font (not mono) for free text:
  * Founder/human messages: right-aligned bubble (`bg-accent-subtle`, rounded, max-width 80%). Slash commands keep
    mono for the command text only, in a slimmer bubble.
  * Co-founder replies: left-aligned with the Co-founder's face avatar (M20 §4) and name on the first message of a
    group; reply text rendered with the existing Markdown component, compact spacing.
  * Command output (`OutputView`): inside a left bubble as a compact card.
* De-clutter rules:
  * No status badge per message. Status shows as a small line under the human bubble only when not answered:
    "Sending…", "Waiting for the Co-founder", "Failed · Retry". Answered → nothing (time on hover/title).
  * "Working" = a typing-indicator bubble (three dots) with **one** latest progress line, and a "Show steps" toggle
    that expands the existing `ProgressLines`.
  * Action chips collapse to one quiet line "Changed 3 things" that expands on click. "Open the note" moves into
    a small ⋯ menu on the bubble (with "Copy text").
  * Group consecutive messages; a day separator ("Today", "Yesterday", date) instead of a timestamp per message.
  * Stale/failed: one inline line with Retry, no extra paragraph.
* Founder questions (M18 A2) appear in the stream as left bubbles from the **asking agent** (its face + name +
  "asks"), with option buttons, a "Use default (<default>)" button and "Reply…" (free text answered via the
  questions API). Answered/defaulted questions collapse to a one-line receipt. If the backend lacks the questions
  API (404) → no question bubbles, no error.
* Empty state: a short greeting from the Co-founder bubble + 3 suggestion chips ("What's the plan this week?",
  "/status", "Hire a designer").
* The full panel (`CommandCenterPanel`) uses the same chat components at full height.
* Accessibility: the message list is a `role="log"` with `aria-live="polite"` for new replies; bubbles have
  accessible names ("You", "AI Co-founder", agent name); keyboard: composer focus with `/` from the canvas stays.

## 3. Organization: compact, Map or List (`features/canvas/widgets/OrgWidget.tsx`, new `OrgMap.tsx`, `OrgList.tsx`)
* Header toggle **Map | List** (segmented control, persisted in `lib/storage.ts` key `company-os.org-view`,
  default `map`). The previous tree (incl. the horizontal/vertical toggle) stays only in the full Organization
  panel as a third view "Tree".
* **Map** (SVG, fits the widget, no scrolling at ≤ 30 people; pan/zoom not needed in the widget):
  * Radial layout: the human founder and the Co-founder in the centre (founder small, attached to the Co-founder),
    managers/leads on the first ring, their reports on an outer ring clustered by manager (angles allocated by
    subtree size). Pure function in `lib/orgMap.ts` (`layoutOrgMap(nodes, size) -> {nodes:[{id,x,y,r}], edges}`),
    unit-tested (no overlaps for ≤ 40 nodes, deterministic).
  * **Circles and lines**: each person is a coloured circle with an illustrated face (§4); reporting lines are thin
    straight lines (`stroke: var(--line-strong)`). Collaboration lines: thin dashed lines between agents that share
    an in-progress meeting (M21) or a dependency between in-progress tasks — at most 5 extra lines per circle,
    animated subtly when live (respect `prefers-reduced-motion`).
  * Live state ring as today (`ringStyleFor`): working pulse, idle, paused dimmed, needs-attention.
  * Name label under circles ≥ 36 px; smaller circles show the name in the hover card (existing `AgentHoverCard`).
    Click → agent panel; humans → member hover card. Keyboard: circles are focusable buttons in a logical order
    (centre → rings), Enter opens.
  * Circle sizes: Co-founder 56, managers 44, others 34; shrinks one step when > 20 people.
* **List**: dense rows grouped by manager (indented one level per manager), each row: face (26), name, role,
  live state text ("Working on <task>", "Idle", "Paused"), today's spend (if available from the existing agent
  data, else nothing). Click opens the agent panel. Virtualization not needed (≤ 200 rows).
* Widget height matches the chat row; empty/loading/error states unchanged.

## 4. Illustrated faces (`components/avatar/`)
* Faces are generated locally (no network, no external image service) with DiceBear (`@dicebear/core` +
  `@dicebear/lorelei` for agents, `@dicebear/notionists` for humans). Both styles are **CC0** (lorelei by Lisa
  Wischofsky, notionists by Zoish; dicebear.com/licenses, checked 2026-10-30) and the library is MIT. Record this in
  `docs/architecture.md` decision log and a `web/THIRD_PARTY.md` (create if missing) entry.
* Seed = agent id (or user id) → stable face. The face SVG has a transparent background; the circle background is
  one of 8 design tokens `--avatar-bg-1..8` (add to `styles/tokens.css` for light and dark themes, colourful but
  meeting text contrast for the label rendered outside the circle) picked by a hash of the id.
* The role-family glyph becomes a small badge at the bottom-right of the circle (sizes ≥ md), so the employee type is
  still visible; the founder gets the founder glyph badge.
* `Avatar`/`AgentAvatar` keep their API (add an optional `seed`/`face` prop); every existing usage (chips, hover
  cards, tables, panels) gets faces automatically. Memoize generated SVG per (style, seed, size) in a module-level
  `Map`; render as an inline `<img src="data:image/svg+xml;utf8,…">` with `alt=""` (decorative; parents provide the
  name) — no `dangerouslySetInnerHTML`.
* Versions: use a matching major of `@dicebear/core` and the style packages (style packages are 9.x as of
  2026-10-30 while core is 10.x — pin core to the major the style packages declare as peer).
* Bundle: import only the two style packages; keep the main chunk growth < 60 kB gzip (check `pnpm build` output).

## Tests
Layout order (chat first), chat ordering/grouping/day separators/auto-scroll pill, status lines only when not
answered, working indicator collapsed by default, question bubbles (options, default, reply; 404 hides), Map/List
toggle persistence, `layoutOrgMap` determinism and no overlaps, list grouping, avatar determinism (same seed → same
src, token background), existing command center behaviours (retry, viewer read-only, not-available) still pass.
