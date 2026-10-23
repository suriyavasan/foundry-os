# M7 — Accounts, login & team — spec

Owner direction: onboarding asks the human for a username and password; everything else
is autonomous. Other humans can be invited by email and can be "human agents" inside the
agent cluster.

## 1. First-run setup
- Fresh install (no users with credentials) → the web shows **Create your account**:
  username (3–32, `[a-z0-9._-]`), display name, password (≥ 12 chars, zxcvbn-style
  strength check, not in a common-password list), optional email (for invites/recovery).
- To stop someone on the LAN from claiming a fresh install first, setup requires the
  **setup token** printed in the server terminal (reuse M3b-sec's `data/owner.token`
  mechanism) unless the request comes from loopback.
- Then: create company (name + one-line idea is enough; the AI Co-founder fills in the rest
  via notes) → detect local model → done. Connect-accounts step is offered later (M8) and is
  optional.

## 2. Authentication
- Passwords: Argon2id (`argon2-cffi`), per-user salt, parameters recorded for rehash.
- Sessions: server-side table (`id` = 256-bit random, hashed at rest; user, created,
  last_seen, expires (14 days sliding, 30 days absolute), ip, user_agent, revoked_at).
  Cookie `cos_session`: HttpOnly, SameSite=Lax, Secure when served over HTTPS, Path=/.
- CSRF: double-submit token (`cos_csrf` cookie + `X-CSRF-Token` header) on all
  state-changing requests from browsers; bearer-token requests exempt.
- Login rate limiting (per username and per IP, exponential backoff) and lockout notice;
  generic error messages.
- Personal API tokens (for CLI): created in Settings, shown once, stored hashed, scoped
  (read / write / admin), revocable.
- Optional TOTP 2FA (pyotp) for admins — recommended when exposed publicly (M8 requires
  either 2FA or a strong password policy before enabling a public URL).
- Password change, session list with revoke, logout everywhere.
- `local` auth mode becomes dev-only (refuses non-loopback binds); default mode `password`.
- The M3b-sec owner-token cookie remains only as the setup/bootstrap path and CLI fallback.

## 3. Team & roles
- Roles per company: `owner` (one or more), `admin`, `member`, `viewer`.
  `member` = a **human agent**: appears in the org tree (circle avatar with the human glyph
  and initials), has a manager (agent or human) and direct reports, can be assigned tasks,
  reviews, and approvals scoped to them; completes tasks via the UI (comment, attach
  artifact, complete/block). Humans are never dispatched to a runtime; dispatcher skips
  human assignees and notifies them instead.
- Org model: add `org_nodes` view or extend relations so both agents and human members
  have `manager_ref` (`agent:<id>` / `member:<id>`). Agents may report to humans.
- The AI Co-founder treats human members as colleagues: can assign them tasks (rate
  limited, guardrail `internal_write`), sees their capacity, never their credentials.

## 4. Invitations
- Owner/admin invites by email (+ role, manager, title). Invitation token: random, hashed,
  single use, expires in 7 days, bound to email.
- Delivery: via the company email provider (M8) when connected; otherwise the UI shows a
  copyable invite link (no email sent — stated clearly).
- Accept flow: open link → set username/password (or log in if they already have an
  account on this install) → joins company with role → appears in org tree.
- Revoke invitation, remove member (their tasks are reassigned; history kept).

## 5. Notifications
- In-app notification center (bell in top bar): assigned to you, review requested, approval
  needed, AI Co-founder replied to your note, alerts. Read/unread.
- Email notifications (opt-in per type) when an email provider is connected (M8).
- Daily digest from the AI Co-founder (optional).

## 6. API & UI
Auth: `POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/logout`,
`GET /api/auth/session`, `POST /api/auth/password`, `GET/DELETE /api/auth/sessions`,
`POST/GET/DELETE /api/auth/tokens`, `POST /api/auth/2fa/{enroll,verify,disable}`.
Team: `GET/POST /api/companies/{cid}/invitations`, `DELETE …/{id}`,
`POST /api/invitations/{token}/accept`, `GET/PATCH/DELETE /api/companies/{cid}/members/{id}`.
Notifications: `GET /api/notifications`, `POST /api/notifications/read`.
UI: setup wizard, login page, account settings (password, sessions, tokens, 2FA), team
panel (members, invitations), human nodes in the org tree, "My work" panel for members,
notification center.

## 7. Tests
Setup token / loopback rule; password policy; Argon2 verify + rehash; session lifecycle
(sliding/absolute expiry, revoke); CSRF required for cookie auth, not for bearer; login
rate limit; API token scopes; role matrix (viewer/member/admin/owner) on every mutating
route; invitation single-use/expiry/email binding; member in org tree, task assignment to
a human never dispatched; tenancy for members of multiple companies; agents can never
create users, invitations or tokens.
