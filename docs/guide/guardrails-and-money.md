# Guardrails and money

Autonomy is only safe if the limits are real. In Foundry AI OS the limits are **program code**, not a polite request in an AI's instructions. An AI that is confused, tricked or just wrong still cannot go past them.

## What agents can never do

- Change their own permissions, budgets, policies or credentials.
- Approve their own requests.
- See your secrets (passwords, API keys, tokens).
- Create user accounts, invitations or login tokens.
- Sign up for outside services that need a CAPTCHA, a phone check or accepting terms. You do those yourself, once. See [Reach it from anywhere](reach-it-from-anywhere.md#connect-accounts-once).

## Risk tiers

Every action an agent can take belongs to a tier. Each tier has a rule.

| Tier | Examples | Default |
|---|---|---|
| Read | Look up company state, read files | Allowed |
| Internal write | Create tasks, save files, report progress | Allowed |
| Org change | Hire, assign skills, create schedules | Allowed within headcount and budget limits |
| External read | Search the web | Allowed, rate limited, private addresses blocked |
| External communication | Send an email, post something public | Another agent reviews the content first, then it is allowed |
| External commitment | Accept terms, register accounts or domains | **You approve** |
| Spend | Anything that costs money outside AI usage | **You approve** above a limit that starts at $0 |
| Identity | Create a mailbox, change DNS, deploy the hosted UI | The AI Co-founder approves; anything touching domain ownership also needs you |
| Destructive | Delete or overwrite outside resources | **You approve** |

You can edit these rules in the Guardrails panel (owner and admin only). A rule can also say "deny", which blocks an action for good.

## Approvals

When an action needs a person, it becomes an **approval**: a card that shows exactly what is being asked. Each approval is tied to that one action and can be used once. The agent cannot decide it, and neither can any other agent.

You decide in the Approvals view, with `/approve <id>` in the Command Center, or with `companyos approvals approve ID` in a terminal.

**What you must approve, in summary:** buying a domain or subscription, accepting terms, destructive actions, spending above your limit, and domain or DNS changes. Raising a budget is also yours.

Buying a domain is extra careful: you approve that exact domain **and a price ceiling**. If the price is higher when the purchase happens, nothing is bought.

## Budgets

- Set a company budget (daily and monthly), a budget per agent, and a cap on what any agent can be hired with.
- Before every run the system **reserves** the likely cost. If the budget would be exceeded, the task is **blocked before it starts**. In testing, a task with a tiny budget on a priced model was blocked with zero runs, and ran once the budget was raised.
- Budgets are also checked during a run. A run that runs out is cancelled.
- Alerts appear at 50, 80 and 100 percent.
- Free local models cost $0 and are never blocked by budgets.

### Is the number a charge?

Every cost is labelled:

| Label | Meaning |
|---|---|
| **Verified** | Reported by the provider, or a known $0 (a local model) |
| **Estimated** | Our calculation from token counts and prices. Not a bill |
| **Unknown** | We cannot tell (for example a subscription-based tool) |

An estimate is never shown as a charge. Your provider's own invoice is the real bill.

## Free-first routing

By default Foundry picks a free model when one exists (a local model, or a provider priced at zero) and uses a paid model only when you allow it. Three styles:

- **Free first** (default): free if possible.
- **Pinned**: always the employee's own model, whatever it costs.
- **Quality first**: the best paid model.

## Other protections

- **Rate limits** on how often an agent can act, hire, send email or fetch the web.
- **Outbound checks**: anything leaving the company is scanned for secrets, your own tokens and stray personal details before it goes.
- **Peer review**: outbound messages are checked by a different agent.
- **AI disclosure**: outgoing email carries a footer saying it came from an AI. On by default; only you can turn it off.
- **Untrusted content**: web pages, emails and files from outside are marked as data to read, not instructions to follow. This is containment, not a guarantee. A weak model can still be fooled, which is why the real limits (tiers, approvals, budgets) are code.
- **Stall and runaway detection**: runs that stop making progress, or create too much, are cancelled. A run of failures pauses the company automatically.
- **File jail**: agents can only read and write inside their own work folder.

## Emergency stop

If something looks wrong, stop everything:

- In the Guardrails panel, press **Emergency stop**, or
- in a terminal: `companyos stop --all` (only the owner can do this).

It cancels every running job in every company and stops new work until you lift it with `companyos resume --all`. `/pause` in the Command Center pauses just one company.

## Known gaps

We list what is not covered in [docs/security.md](../security.md), for example: backups are not encrypted by the app, and the server does not filter provider addresses you type in yourself. Read that file before exposing your install to the internet.
