# M8 — Reachability & identity — spec

Owner direction: the landing UI shows how to reach the control UI on the local network and
on a hosted/public network; the AI Co-founder sets up the company's email identity and can
host the control UI (e.g. Vercel) and obtain a free domain (e.g. via a GitHub-repo-based
free domain service) — autonomously, after the Founder's one-time setup.

## 0. Boundaries (non-negotiable)
- No automated sign-up on services that require CAPTCHA, phone verification or acceptance
  of terms (Gmail, Vercel, Cloudflare, registrars, free-domain dashboards). The Founder
  connects each provider once (API token or OAuth) in **Connected accounts**; the AI
  Co-founder then operates it within guardrails (M6 tier `identity`/`external_commitment`).
- Public exposure requires M7 accounts (password auth; 2FA recommended) — the system
  refuses to enable a public URL while `auth_mode=local` or the owner has a weak password.
- Paid domains/services → human approval (real money).

## 1. Connected accounts vault
`connected_accounts`: `provider` (`cloudflare`, `vercel`, `github`, `agentmail`, `resend`,
`tailscale`, `porkbun`, `namecheap` …), `label`, `credential_id` (M3b encrypted store),
`scopes`, `status` (`connected`,`invalid`,`revoked`), `last_checked_at`, `metadata`
(account name/id, no secrets). Owner-only CRUD + "test connection". Agents never get raw
credentials: provider actions run inside the control plane via **provider adapters**
(`app/integrations/<provider>.py`) exposed to agents as guarded actions.

## 2. Reachability
### 2.1 Local network
- Detect LAN addresses (non-loopback IPv4/IPv6) and hostname; mDNS name `<host>.local`.
- Server binds `127.0.0.1` by default; enabling LAN access (Settings → Reachability) rebinds
  to `0.0.0.0` (requires password auth). Show URLs + QR code.
### 2.2 Public URL (tunnel)
- **Cloudflare Tunnel** (preferred, free): quick tunnel (`cloudflared tunnel --url`, random
  `*.trycloudflare.com`, no account, ephemeral — good for testing) or named tunnel with a
  connected Cloudflare account + domain (stable). `cloudflared` binary detection + install
  instructions (no silent downloads; offer a one-click download with checksum verification
  if the owner confirms).
- **Tailscale Funnel** alternative when Tailscale is installed and connected.
- Tunnel managed as a supervised child process by the API/worker; health-checked; URL shown
  on the landing screen; automatic restart; disabled by kill switch? (no — reachability stays
  up so the Founder can resume).
### 2.3 Domains
- **Free subdomain via GitHub-PR registries** (e.g. `is-a.dev`-style services): the Ops
  agent/AI Co-founder prepares the registration file per the registry's rules, opens a PR
  with the connected GitHub account (tier `external_commitment` → human approval of the
  exact PR content by default, because it publishes under the Founder's GitHub identity
  and accepts the registry's terms), tracks PR status, then configures DNS → tunnel when
  merged. Registry adapters are pluggable; each documents its rules/eligibility honestly
  (many free registries only accept personal/open-source projects and are human-reviewed).
- **Free domain providers with dashboards** (e.g. DigitalPlat FreeDomain): assisted flow —
  the system generates exact steps + DNS records; the Founder completes registration; then
  the agent verifies DNS and wires the tunnel.
- **Paid domain via registrar API** (Cloudflare Registrar/Porkbun/Namecheap): availability
  search + price, purchase requires human approval (`spend` + `external_commitment`).
- DNS management through the connected DNS provider (Cloudflare first): records created by
  the control plane, verified, TLS via the tunnel/provider.
### 2.4 Hosted control UI (Vercel, optional)
- Deploy `web/dist` to a connected Vercel account via the Vercel API (project create/update,
  deployment, alias), configured with a rewrite proxy `/api/*` → the public tunnel URL so
  cookies stay first-party. Tier `identity` → AI Co-founder approval (no money). Default
  and simpler path remains "UI served by the device through the tunnel".

## 3. Company email identity
- Providers with APIs built for programmatic inboxes: **AgentMail** (create inboxes per
  agent/company via API), or **custom domain**: Cloudflare Email Routing (receive) +
  **Resend** (send) on the company domain. Gmail/Outlook only via OAuth connect of an
  existing account (no account creation).
- Mailboxes: `company@`, `cofounder@`, per-agent aliases (`<agent>@`) — created by the AI
  Co-founder (tier `identity`), each mapped to an agent; inbound mail becomes untrusted
  input events routed to the mailbox owner (injection containment, M6); outbound mail goes
  through DLP + maker-checker + AI-disclosure footer (M6) and rate limits.
- Used by M7 for invitations and notifications.

## 4. Landing / "How to reach" screen
After login (and as a section in Settings): device name, local URL(s) + QR, public URL
(tunnel/domain) with status, hosted UI URL (Vercel) if any, email identity, connected
accounts health, and a guided checklist of the Founder's one-time steps with what the AI
Co-founder will do automatically after each.

## 5. AI Co-founder automation
Ops runbook skill `reachability-ops` (company-authored): once accounts are connected the
AI Co-founder (or Ops agent) — starts/monitors the tunnel, proposes and (after approvals)
registers domains, configures DNS, deploys the hosted UI, creates mailboxes, and keeps them
healthy (`reachability-health` schedule). All actions via guarded provider actions.

## 6. Tests
Adapters with recorded HTTP fixtures (no network in CI); guardrail tiers enforced for each
provider action; public exposure refused under local auth/weak password; tunnel supervisor
restart/health with a fake binary; DNS record plan/verify logic; Vercel rewrite config
generation; email DLP + disclosure footer; inbound mail tagged untrusted; credentials never
returned or logged. Live checks (manual, documented): LAN URL from another device, quick
tunnel URL reachable.
