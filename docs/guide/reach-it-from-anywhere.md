# Reach it from anywhere

By default Foundry AI OS listens only on the computer it runs on. This page shows how to open it from your phone, from another place, or on your own web address. Each step is optional.

For the technical detail, see [docs/deploy-reachability.md](../deploy-reachability.md).

## The rules first

1. **A strong password comes first.** The server refuses to open itself to the network, start a tunnel or host the screen online until password sign-in is on and your password is strong. Turn on two-factor sign-in too.
2. **No automated sign-ups.** Foundry never creates an account for you at Cloudflare, Vercel, GitHub, a domain seller or an email service. Those need a CAPTCHA, a phone check or terms only you can accept.
3. **Agents never see a token.** The program makes the outside calls. Agents only ask.
4. **Money needs you.** A paid domain is bought only after you approve that exact domain and a price ceiling.
5. **No silent downloads.** The tunnel helper is installed only after you confirm, and only if its checksum matches the one the publisher posts.

## Level 1: your home or office network

Use this to open Foundry from your phone or laptop on the same Wi-Fi.

1. Set a strong password under *Account*, and turn on two-factor sign-in.
2. In *Settings > Reachability*, switch **Local network access** on. This saves a preference. The server must be restarted for it to take effect, and the screen says so.
3. Restart Foundry (from source: stop `companyos up` and start it again).
4. On the other device open `http://<your-computer-name>.local:8700` or the network address shown in the screen. A QR code is shown too.

This is plain `http` on your own network. Do not use it on public Wi-Fi. To undo, switch it off and restart.

## Level 2: a public web address (tunnel)

A **tunnel** gives your computer a public address on the internet without opening your router. Open *Reachability > Public URL*.

| Kind | Needs | Good for |
|---|---|---|
| **Cloudflare quick tunnel** | The `cloudflared` helper. No account | A free address for your phone. It is random and changes every time the tunnel restarts |
| **Cloudflare named tunnel** | A domain on your Cloudflare account and a Cloudflare token | A stable address on your own domain |
| **Tailscale Funnel** | Tailscale installed and signed in, with Funnel allowed | A stable `*.ts.net` address. *Not verified by us against a real Funnel setup* |

Foundry keeps the tunnel running, restarts it if it drops, and checks it every 30 seconds.

Tunnel and TLS were **not** tested against the real internet in our verification. Try the quick tunnel from a phone on mobile data before relying on it.

### From the terminal (no browser needed)

```bash
foundryos reach public start       # the free quick tunnel; asks before downloading cloudflared
foundryos reach --watch            # prints the address now and every time it changes
foundryos reach public stop        # turn it off (it stays off after a restart)
```

`foundryos setup` offers the same thing once, as its last step. The download of `cloudflared` happens only after you confirm; the command shows the version, the address and the SHA-256 it will check. `named` and `tailscale` work the same way (`reach public start named --hostname app.example.com`).

### The quick tunnel's address changes

A quick tunnel gets a new random address **every time it starts**, which includes every time Foundry restarts. So:

- Do not bookmark it or hand it out as a permanent link. Anything that points at the old address, such as a link you sent or the hosted screen on Vercel, stops working.
- Foundry keeps track for you. Each change is remembered (the last five), written to the activity log and the server log, and shown in *Reachability > Public URL* ("This address changes when Foundry restarts", with when it last changed, and the last address while the tunnel is stopped).
- On a machine with no browser, read the current address from `foundryos status`, from `foundryos reach --watch`, or from the file `<data folder>/public-url.json` (only you can read it; `url` is empty while the tunnel is down).
- For an address that stays, use a **named tunnel** (your own domain on Cloudflare) or **Tailscale Funnel**.

### Passkeys need a stable address

A passkey is tied to the exact address you made it on. Because a quick-tunnel address changes, Foundry **does not offer passkeys there**: the passkey buttons are hidden, the Account screen says "Passkeys need a stable address", and the server refuses to create one. A passkey that somehow exists for a quick-tunnel address would not count as strong sign-in for opening the device to the network. Passkeys do work at `http://localhost:8700` (including through an SSH port-forward, because browsers treat localhost as secure) and on a named tunnel or Tailscale address. For a quick tunnel use your password with two-factor sign-in.

### Live updates through a quick tunnel

Cloudflare's documentation says quick tunnels do not support server-sent events, which is what the web app normally uses for live updates. If the live stream does not connect, the web app falls back to refreshing every 10 seconds, so the screen still works, just less instantly. We have not run a real quick tunnel to see this ourselves. If you want instant updates, forward the port over SSH (`ssh -N -L 8700:127.0.0.1:8700 user@host`, then open `http://localhost:8700`), or use a named tunnel or Tailscale Funnel.

## Level 3: your own domain

Pick one in *Reachability > Domain*:

- **Free subdomain on is-a.dev.** Foundry prepares the request file and shows you the exact content. It opens the request on GitHub only after you approve. Humans review these requests, they are meant for personal and non-commercial projects, and a company site may be turned down.
- **Free domain with a dashboard (DigitalPlat FreeDomain).** Foundry gives you the steps; you register it yourself, then press *Verify*.
- **Paid domain.** Foundry shows a price quote (an estimate). You approve the domain and a price ceiling. The purchase happens only at or below that ceiling, and the charge the seller reports is recorded as verified spend. One-year purchases only.

## Hosted screen on Vercel (optional)

Normally your computer serves the screen through the tunnel. If you want the screen hosted on Vercel too, connect Vercel and press *Deploy*. The browser still talks only to the Vercel address, and the data stays on your computer. Use a named tunnel or a domain for this, because a quick tunnel's address changes. Redeploy if your public address changes (`foundryos reach --watch` tells you).

## Company email

Your company can have its own mailboxes (for the company, the AI Co-founder, or each agent). In *Settings > Email* pick a provider:

- **AgentMail:** connect the key and add a webhook for incoming mail.
- **Resend on your domain:** connect the key. The mailbox stays pending until you add the DNS records Resend lists. *Its incoming-mail format follows its docs and is unverified.*
- **Cloudflare Email Routing:** an alternative for incoming mail, using a small worker script. *Not run against Cloudflare.*

How mail is handled:

- Incoming mail is treated as **data**, never as orders. Anything that looks like a trick is flagged.
- Outgoing mail goes through the guardrails: scanned for secrets, reviewed by another agent by default, rate limited, and signed with an AI-disclosure footer.
- An agent can send only from its own mailbox.
- With a mailbox set up, invitations to your team are emailed. Without one, you copy a link.

## Connect accounts once

This is the one boundary to remember. You make the account and the token. Foundry stores it **encrypted**, and after that the AI Co-founder can use it, inside the guardrails.

| Account | Token | Used for |
|---|---|---|
| Cloudflare | Zone:Read, DNS:Edit, Cloudflare Tunnel:Edit (and Email Routing for mail) | DNS, named tunnel, incoming mail |
| Vercel | Access token | Hosted screen |
| GitHub | Fine-grained token (contents and pull requests: write) | Free subdomain request |
| AgentMail | API key | Mailboxes |
| Resend | API key | Sending mail on your domain |
| Tailscale | None (uses the local app) | Funnel |
| Porkbun | API key and secret | Domain search and purchase |
| Namecheap | API key and details *(unverified)* | Domain search and purchase |
| Gmail, Outlook | **Not available.** No sign-in client ships with Foundry | None |

Revoking an account revokes its stored secrets, and agents lose access at once.

## Privacy note

Checking your domain asks Cloudflare's public DNS service about it. Searching for a domain sends the name to your seller. Installing the tunnel helper contacts GitHub. Nothing else leaves your computer unless you connect a service.
