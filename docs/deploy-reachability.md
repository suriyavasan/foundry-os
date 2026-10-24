# Reaching Company OS from other devices and the internet (operator guide, M8)

This is for the person running the install. API details: `docs/api.md` (Milestone 8). Design: `docs/specs/m8-reachability-and-identity.md`.

## The rules that do not bend

1. **Public exposure needs password sign-in.** The server refuses to bind to the network, run a tunnel, or deploy the hosted UI unless
   `COMPANY_OS_AUTH_MODE=password` (the default) **and** the owner account has a password set under the password policy (or 2FA). The
   reason is part of `GET /api/reachability` (`public_exposure_blocked_code`: `auth_local`, `no_password`, `weak_password`). Turn on 2FA too: the
   status says `two_factor_recommended` while it is off.
2. **No automated sign-ups.** Company OS never creates an account at Cloudflare, Vercel, GitHub, a registrar, a free-domain site or an e-mail
   provider: those need CAPTCHA, phone checks or terms you must accept yourself. You create the account, make an API token, store it in
   *Connected accounts*; after that the AI Co-founder operates it inside the guardrails.
3. **Agents never see a token.** Provider calls happen in the control plane. Agents ask for an action; the tier decides who must approve it:
   DNS, domain registration and domain purchase need *you*; mailbox creation, hosting and tunnel changes need the AI Co-founder (or you).
4. **Money needs you.** A paid domain is bought only after an approval you grant for that domain **and a price ceiling**; if the registrar's price is
   higher at purchase time nothing is bought. No rule you write can lower this (you can still forbid it with a `deny` rule).
5. **No silent downloads.** `cloudflared` is installed only after you confirm, from the official GitHub release, after its SHA-256 matches the checksum
   GitHub publishes. If no published checksum can be found, or two sources disagree, the install is refused.

## 1. Local network (LAN)

`Settings > Reachability` shows your device name, `<name>.local` (mDNS: announced by macOS automatically; on Linux only reported when avahi is installed)
and the LAN addresses. The server binds `127.0.0.1` by default.

To allow other devices on your network:

1. Set a strong password in *Account* (and turn on 2FA).
2. Switch **Local network access** on. This **saves a preference**; a running process cannot rebind its own socket, so the status says
   `restart_required`.
3. Restart with the launcher, which reads the preference:

   ```bash
   cd backend
   uv run python -m app.serve --port 8700        # binds 0.0.0.0 when the preference is on and the gate is open
   ```

   `--host` overrides the preference but is held to the same gate. If you start uvicorn yourself, also export `COMPANY_OS_BIND_HOST=0.0.0.0` (and
   `COMPANY_OS_PORT`) so the app knows how it is bound. M9's `companyos up` will do this for you.
4. Open `http://<name>.local:8700` or `http://<lan-ip>:8700` from another device (the UI shows a QR code). This is plain `http` on your network.

To go back: switch it off and restart.

## 2. A public URL (tunnel)

Open *Reachability > Public URL*. The device keeps one managed tunnel process, restarts it when it dies (1 s, 2 s, 4 s … up to 60 s; it stops trying and
shows an error after 8 failures in a row), checks `<url>/api/health` every 30 s and restarts it if the URL stops answering. A tunnel that was running is
resumed when the server starts (`COMPANY_OS_TUNNEL_AUTORESUME=false` disables that). Logs: `GET /api/reachability/tunnel/logs` (secrets masked).

### Cloudflare quick tunnel (no account, for trying it out)

Needs `cloudflared`. If it is missing the status shows install instructions and, if you want, a one-click download:

- `GET /api/reachability/tunnel/install-plan` shows the exact URL and SHA-256 that would be used (a metadata lookup; nothing is downloaded).
- `POST /api/reachability/tunnel/install {"confirm": true, "sha256": "<the one you read>"}` downloads, verifies and installs into `data/bin/cloudflared`.
- Or `brew install cloudflared` / your package manager. `COMPANY_OS_CLOUDFLARED_PATH` points at a specific binary.

Start it: you get a random `https://<words>.trycloudflare.com` URL that changes every time and is not for anything you want to keep.

### Cloudflare named tunnel (stable, on your own domain)

Needs: `cloudflared`, a domain whose DNS is on your Cloudflare account, and a connected Cloudflare account. Create an API token with
**Zone:Read, DNS:Edit** (on that zone) and **Account > Cloudflare Tunnel:Edit**; connect it (*Connected accounts > Cloudflare*), set `account_id` if the
token sees several accounts. Start with provider *named* and a hostname such as `app.example.com`: Company OS creates (or reuses) a tunnel named
`company-os-app-example-com`, sets its ingress to this server, creates the proxied `CNAME app.example.com -> <tunnel-id>.cfargotunnel.com`, and runs
`cloudflared tunnel run` with the tunnel token passed in the `TUNNEL_TOKEN` environment variable (not on the command line).

### Tailscale Funnel

Needs Tailscale installed and signed in on this device, Funnel allowed for the device in your tailnet policy (the `funnel` node attribute), and a
*Tailscale* connected account (it only marks your consent; there is no secret). Company OS runs `tailscale funnel <port>` in the foreground and reads the
`https://<machine>.<tailnet>.ts.net` URL from its output. If Funnel is not allowed the CLI's own message is shown. *Not verified against a tailnet with Funnel enabled.*

## 3. Connected accounts

Store the secret first (`POST /api/companies/{cid}/credentials`), then connect (`POST /connected-accounts`); the UI does both. Connecting runs a connection test.

| Provider | Token / fields | Needed for |
|---|---|---|
| Cloudflare | API token: Zone:Read, DNS:Edit, Cloudflare Tunnel:Edit (Email Routing Rules:Edit for mailbox routing); metadata `account_id`, `email_worker`, `forward_to`; optional secret `webhook_secret` | DNS, named tunnel, inbound mail routing |
| Vercel | access token; metadata `team_id` | hosted control UI |
| GitHub | fine-grained token (Contents + Pull requests: write; it forks the registry) or classic `public_repo` | free-subdomain registry pull request |
| AgentMail | API key; secret `webhook_secret` (the Svix signing secret of your webhook) | inboxes, send, receive |
| Resend | API key (full access to manage the domain); secret `webhook_secret` | send (and receive) on your domain |
| Tailscale | none (local CLI) | Funnel |
| Porkbun | `api_key` + secret `secret_api_key`; enable API access on the domain/account | domain search/purchase |
| Namecheap | API key; metadata `api_user`, `username`, `client_ip` (whitelisted) and `registrant_<Field>` for purchases | domain search/purchase (*unverified against the real API*) |
| Gmail / Outlook | OAuth only — **not configured**: no OAuth client ships with Company OS | — |

Revoking an account revokes its stored secrets (unless something else uses them) and agents lose the provider immediately.

## 4. A domain

Pick one in *Reachability > Domain*:

- **Free subdomain by pull request — is-a.dev.** Company OS prepares `domains/<name>.json` exactly as the registry documents it
  (`{"owner": {"username": "<you>"}, "records": {"CNAME": "<target>"}}`), shows you the file, branch, PR title and body, and waits for **your** approval of that
  exact content before it forks the registry under your GitHub account and opens the PR. Rules to know (from the registry's own documentation, which changes):
  the site must not be blank; it is meant for personal/portfolio or non-commercial software projects (a company site may be rejected); humans review
  every request, may ask for changes (the PR template asks for a website link and a screenshot), and their README says not to use AI to generate the
  request, so read the file yourself. The CNAME target must be a stable hostname: the hosted UI on Vercel (`cname.vercel-dns.com`) or a named tunnel hostname,
  never a quick tunnel. After the merge, *Verify* checks DNS and TLS.
- **Free domain with a dashboard (DigitalPlat FreeDomain).** Assisted: Company OS gives the exact steps, you register in the provider's dashboard
  (point the nameservers at a Cloudflare zone you control to get a stable tunnel hostname), then *Verify*.
- **Paid.** *Search* shows the registrar's quote (an estimate, `cost_kind: estimated`). Requesting a purchase creates an approval bound to the domain and your
  price ceiling. After you approve, the price is re-quoted; the purchase happens only at or below the ceiling and the registrar-reported charge is recorded
  as verified spend in Finance. One-year purchases only. Then move the nameservers to Cloudflare and start a named tunnel.

## 5. Hosted control UI (Vercel, optional)

The default stays "this device serves the UI through the tunnel". To also host it on Vercel: connect Vercel, build the UI (`cd web && pnpm build`;
`COMPANY_OS_WEB_DIST_DIR` overrides `web/dist`), have a public URL, press *Deploy*. The deployment contains the build plus a generated `vercel.json`:

```json
{ "rewrites": [ { "source": "/api/:path*", "destination": "https://<your public url>/api/:path*" },
                { "source": "/((?!api/).*)", "destination": "/index.html" } ] }
```

so the browser only talks to the Vercel origin and cookies stay first-party. Re-deploy after the public URL changes (a quick tunnel changes it every
time — use a named tunnel or a domain for a hosted UI). Any `vercel.json` inside the build is ignored.

## 6. Company e-mail

Pick a provider in *Settings > Email*, then create mailboxes (`company@`, `cofounder@`, one per agent).

- **AgentMail:** connect the API key. For receiving, create a webhook in AgentMail for `message.received` pointing at
  `https://<public url>/api/webhooks/email/<company id>/agentmail`, and store its signing secret (`whsec_…`) as the account's `webhook_secret`.
- **Resend on your domain:** connect the Resend key, get a domain first, create a mailbox (the sending domain is added at Resend and the mailbox stays
  `pending` until you add the DNS records Resend lists — DNS changes through Cloudflare need your approval — then *refresh*). Receiving: Resend's inbound webhook
  to `…/webhooks/email/<company id>/resend` (Svix-signed, `webhook_secret`). *Shapes of Resend's inbound payload follow its documentation and are unverified.*
- **Cloudflare Email Routing + a Worker (alternative inbound):** route the address to an Email Worker that posts the message to
  `…/webhooks/email/<company id>/worker` with `X-Company-OS-Signature: sha256=<hex HMAC-SHA256 of the body>` using the Cloudflare account's `webhook_secret`:

  ```js
  import PostalMime from "postal-mime";
  export default {
    async email(message, env) {
      const mail = await PostalMime.parse(message.raw);
      const body = JSON.stringify({ message_id: mail.messageId, from: message.from, to: [message.to], subject: mail.subject, text: mail.text });
      const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.WEBHOOK_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
      const sig = [...new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body)))].map(b => b.toString(16).padStart(2, "0")).join("");
      await fetch(env.COMPANY_OS_URL + "/api/webhooks/email/" + env.COMPANY_ID + "/worker", { method: "POST", headers: { "content-type": "application/json", "x-company-os-signature": "sha256=" + sig }, body });
    },
  };
  ```

  *(Not run against Cloudflare.)*

How mail is handled: inbound mail is untrusted data. It is stored verbatim, flagged when it looks like an injection (flagged, never auto-blocked), and the
mailbox's owner agent is woken with a neutral note; it reads the content only through `email.read`, inside `<untrusted source="email">` blocks. Outbound
mail from an agent goes through the guardrail engine: outbound DLP (secrets, your own tokens, stray addresses and phone numbers), peer review (maker-checker)
by default, the per-agent and per-company daily limits, and the AI-disclosure footer (on by default; you can turn it off, agents cannot). An agent can send
only from a mailbox it owns. A webhook with no configured secret or a bad signature is always `401`.

Invitations and notifications: with a working identity and a mailbox, invitations are e-mailed from `company@` (the link exists only in the message) and
people can opt in, per kind, to notification e-mails (`PUT /api/notifications/preferences`); without one, invitations show a copyable link as before. Notification
e-mails are sent by the API process (and the embedded worker), not by a separate `companyos-worker` process.

## 7. Checking it yourself (manual, not automated)

- From another device on the same network: open the LAN URL or `<name>.local` URL and sign in.
- Start a quick tunnel and open the `trycloudflare.com` URL from a phone on mobile data.
- After approving a registry PR, look at it on GitHub; after a purchase, look at the registrar's order page and at *Finance > Expenses*.
- Kill the `cloudflared` process: the status shows `restarts` going up and a new URL for a quick tunnel.

## 8. Privacy notes

DNS verification asks Cloudflare's public DNS-over-HTTPS resolver (`cloudflare-dns.com`) for your domain's records; availability searches send the name
to your registrar; the cloudflared install talks to `api.github.com` and `github.com`. Nothing else leaves the machine unless you connect a provider.
