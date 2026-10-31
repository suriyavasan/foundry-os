# M19 — Talk to your company from Telegram, Discord and WhatsApp

Status: backend implemented, verified against fake platform servers only (2026-10-30); web screens pending. Rules: `AGENTS.md`. Depends on M18 A2 (founder questions) for question delivery.

## Goal

The founder (and other admins) can talk to the company — the Co-founder and through it every agent — from a chat
app, the same way as the web Command Center: send instructions, get replies, answer agents' questions, decide
approvals with a tap, and receive critical alerts. Set up in a new, skippable onboarding step and in Settings.

## What Hermes / OpenClaw offer, and what we ship

Hermes' gateway (upstream, dropped from our fork) and OpenClaw support: Telegram, Discord, Slack, WhatsApp, Signal,
Matrix, Email, SMS (Twilio), Microsoft Teams, Google Chat, Mattermost, iMessage, LINE, and more. Both authorize
senders with allowlists and **DM pairing** (unknown sender → one-time code → owner approves; codes expire in 1 h).
Both connect **WhatsApp through Baileys** (an unofficial WhatsApp Web client paired by QR).

We take their model (one chat = one conversation, default-deny, pairing) and ship, in this milestone:

| Channel | Connection | Needs | Public URL |
| --- | --- | --- | --- |
| Telegram | Official Bot API, long polling (`getUpdates`) over httpx | bot token from @BotFather | no |
| Discord | Official bot over the Gateway (`discord.py`), DMs only | bot token (Developer Portal), no privileged intents | no |
| WhatsApp | **Official WhatsApp Business Cloud API** (Meta Graph API + webhook) | phone number id, access token (System User), app secret, verify token | **yes** (M13 reachability) |

**Legal line (enforced by not building it):** no Baileys / WhatsApp Web automation or any unofficial client
(WhatsApp's terms forbid unauthorized/automated access; accounts get banned); no Discord self-bots or user
tokens; only each platform's official bot/business API. WhatsApp free-form replies are only allowed within 24 h of
the user's last message (Meta's customer-service window); outside it we do not send and say so (no template
messages in this milestone). Slack (Socket Mode), Signal, Matrix, Email, SMS are listed as "coming later" in the
UI (`available: false`), not faked.

## Data (migration `0019_chat_channels`, portable)

* `chat_channels` (tenant): `id, company_id, kind (telegram|discord|whatsapp), name, status
  (connecting|active|error|disabled), credential_id → stored secret bundle (credentials service; never returned),
  config JSON (non-secret: bot username/id, phone_number_id, display phone, waba id), poll_cursor (Telegram update
  offset, string), lease_owner, lease_expires_at, last_error, last_event_at, created_by_user_id, timestamps`.
  At most one channel per kind per company (unique `company_id, kind`).
* `chat_links` (tenant): `id, company_id, channel_id, user_id (a human member), external_user_id,
  external_chat_id, display_name, notify (JSON: {replies, questions, approvals, alerts, digest} bools; defaults
  true,true,true,true,false), last_inbound_at, linked_at, revoked_at`. Unique active `(channel_id,
  external_user_id)`.
* `chat_link_codes` (tenant): `id, company_id, channel_id, user_id, code_hash (sha256), expires_at (10 min),
  used_at`. Codes: 8 chars from an unambiguous alphabet, shown once.
* `chat_messages` (tenant): inbound/outbound log for idempotency and routing: `id, company_id, channel_id,
  link_id?, direction (in|out), external_message_id, ref_type? (note|command|question|approval|alert|digest),
  ref_id?, status (received|sent|failed|skipped), error?, created_at`. Unique `(channel_id, direction,
  external_message_id)` when external id present (inbound dedupe). Message **text is not stored** here (the note /
  exchange already stores what the founder said); outbound keeps only ref + status.

## Linking (default deny)

1. A logged-in admin/owner opens Settings → Channels (or the onboarding step), connects a channel, then clicks
   "Link my account": the server issues a code (10 min) and, for Telegram, a deep link `https://t.me/<bot>?start=<code>`;
   Discord: "DM the bot `link <code>`"; WhatsApp: `https://wa.me/<number>?text=link%20<code>`.
2. The bot receives the code from a chat → verifies hash, unexpired, unused, same channel → creates the link
   (external user id + chat id ↔ user), replies "Linked to <company> as <user name>". Audited `chat.linked`.
3. Any message from an unlinked sender gets one fixed reply per sender per hour: "This is a private Foundry bot.
   Link your account in Foundry → Settings → Channels." Nothing else is processed or stored beyond a dedupe row.
4. Unlink from Settings or by sending `/unlink`. Revoking a member's company access revokes their links.
5. Group chats / servers are ignored in this milestone (DMs only); messages in groups get no reply.

## Inbound (linked sender)

* The linked user's own principal and company role apply (a `CompanyContext` for that human). Same rules as the web.
* Plain text → `command_center.submit(db, ctx, text)` (slash commands work as in the web). The resulting
  CommandExchange/note remembers it came from this chat (`chat_messages` ref), so the answer is delivered back here.
* Built-in chat commands handled by the channel layer: `/start <code>` (Telegram link), `link <code>`, `/unlink`,
  `/help`, `/status` (company status summary from the existing overview service), `/questions` (open founder
  questions), `/approvals` (pending human approvals).
* Buttons:
  * Founder question (M18): one button per option + "Use default"; free-text answer by replying to the question
    message (Telegram reply_to / Discord reference / WhatsApp context id).
  * Human approval: Approve / Decline buttons → `approvals.decide` as that human (same permission checks as web).
    Purchase checkout stays web-only (the button says "Open in Foundry").
  * Button payloads carry only an opaque id `q:<id>:<n>` / `a:<id>:approve|decline`, re-validated server-side
    (tenant, link, status, permission); never trusted.
* Unsupported content (voice, files, stickers) → "Only text is supported here for now." (not silently dropped).
* Inbound size cap 4000 chars; per-link rate limit 20 messages / 5 min ("Slow down" reply).

## Outbound

After the DB commit (copy the `mail_dispatch` after_commit + thread pool pattern), a `ChatDispatcher` sends:
* replies to notes/commands that came from that chat (`note.replied`, `command.updated` answered/failed);
* `founder_question` to every active link of owners/admins with `notify.questions`;
* human approvals pending (with buttons) with `notify.approvals`;
* critical alerts (`autonomy_stalled`, budget exceeded, circuit breaker, emergency stop) with `notify.alerts`;
* optional daily digest with `notify.digest`.
Each send writes a `chat_messages` row (sent/failed/skipped + error). Failures retry 3× with backoff; a 401/403
from the platform marks the channel `error` with a clear `last_error` and an in-app notification. Messages are
plain text (escape for the platform), chunked (Telegram 4096, Discord 2000, WhatsApp 4096). Content never includes
secrets (reuse `redaction`), and agent-written text is labelled with the agent's name.
WhatsApp: if `last_inbound_at` of the link is older than 24 h → status `skipped` reason `outside_24h_window`,
and the UI shows "Send any message to the WhatsApp number to receive updates again."

## Transport / processes

* All channel I/O runs in the engine process (where the scheduler runs; `companyos up` / embedded worker), as a
  `ChannelSupervisor` with one thread per active channel, guarded by the channel lease (`lease_owner`,
  `lease_expires_at`, renewed every 15 s, expires after 60 s) so only one process polls a bot.
* Telegram: `getUpdates?timeout=25&offset=<cursor+1>&allowed_updates=["message","callback_query"]`; persists the
  cursor after handling; `deleteWebhook` on connect; backoff on errors; `getMe` validates the token on connect.
* Discord: `discord.py` client (intents: `dm_messages` only; no `message_content` privileged intent — DMs carry
  content), buttons via `discord.ui.View` with custom_id payloads; validates the token by logging in.
* WhatsApp: `GET /api/webhooks/whatsapp/{channel_id}` verification (`hub.mode=subscribe`, constant-time
  `hub.verify_token` compare, plain-text `hub.challenge`), `POST` verifies `X-Hub-Signature-256` HMAC-SHA256 of
  the **raw body** with the app secret (constant-time) before parsing; 403 otherwise; returns 200 quickly and
  processes after. Sends via `POST https://graph.facebook.com/v21.0/<phone_number_id>/messages` (interactive
  reply buttons, max 3 → options beyond 3 use a numbered list "reply 1–6"). Connect validates by fetching the
  phone number object. Requires a public URL (409 `public_url_required` otherwise, with the M13 hint).
* Network calls go through an injectable transport so tests use fakes (no real platform calls in tests).

## API (owner/admin; tenant-scoped; secrets write-only)

* `GET /companies/{cid}/channels` → `{items: [ChannelOut], available: [{kind, available, reason?, needs_public_url}]}`
  (ChannelOut: id, kind, name, status, config (non-secret), last_error, last_event_at, links: [LinkOut]).
* `POST /companies/{cid}/channels {kind, token?|whatsapp{phone_number_id, access_token, app_secret}}` → validates
  credentials against the platform, stores secrets, returns ChannelOut (+ for WhatsApp the webhook URL and the
  generated verify token **shown once**). 422 invalid token with the platform's message (no token echoed).
* `PATCH /channels/{id} {enabled?, name?}`, `DELETE /channels/{id}` (stops polling, revokes links, deletes secret).
* `POST /channels/{id}/link-code` → `{code, expires_at, deep_link?, instructions}` for the calling user.
* `PATCH /channel-links/{id} {notify}` (own link; admins may revoke any), `DELETE /channel-links/{id}`.
* `POST /api/webhooks/whatsapp/{channel_id}` + `GET` (public, signature-verified; no auth cookie).
* Everything audited (`chat.channel_connected|updated|removed`, `chat.linked|unlinked`, `chat.approval_decided`
  via the approvals audit with `via=<kind>`), no secrets in audit/log/events.

## Web

* Onboarding: new skippable step **"Chat apps"** (`'channels'`) after `repo`, before `team`: cards for Telegram,
  Discord, WhatsApp (and greyed "coming later" ones); each card: short how-to (BotFather `/newbot`; Discord
  Developer Portal → Bot → token, invite link with `bot` + `applications.commands` scope; Meta → WhatsApp → API
  setup), token field(s), Connect, then "Link my account" with the code/deep link and live "Waiting for your
  message…" → "Linked as …" (poll the channel). "Skip for now". Update `SETUP_STEPS` and tests.
* Settings → **Chat apps** section with the same cards plus per-link notification toggles, unlink, remove channel,
  status/last error. All states: loading, empty, error, not-available (older backend → hide).

## Tests

Linking (code expiry/reuse/wrong channel, unlinked sender gets only the fixed reply), tenancy (codes, links,
channels, webhooks of another company), role (member without admin can't submit notes — gets the permission
message), inbound → note → reply delivered to the same chat, approval buttons re-validated (stale, already decided,
other company, non-approver), question buttons/default/free-text reply, WhatsApp signature (bad sig 403, verify
token handshake), 24 h window skip, Telegram cursor persistence and dedupe, rate limits, secrets never in API
responses/audit/logs, lease (two supervisors → one poller), onboarding step list.

## Not verified live (to state at the end)
Real Telegram/Discord/WhatsApp bots were not exercised unless the founder supplies tokens; tests use fake transports.
