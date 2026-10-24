# FAQ

## Is my data private?

Your company's data (notes, tasks, files, keys) is stored in a folder on your own computer. There is no Foundry cloud, no account with us and no tracking. What does leave your computer:

- **What you send to the AI provider you choose.** If you use a cloud AI, the text of the work goes to that provider and is covered by their privacy terms. If you use a local model through Ollama, it never leaves.
- **Anything you connect on purpose**: tunnel, domain, email, hosting.
- **Small lookups**: checking for updates (a request to GitHub, can be turned off), and, if you use a custom domain, a DNS lookup through Cloudflare's public resolver.

Stored keys are encrypted and never shown again. Backups contain the key that unlocks them, so protect those files. See [Updates and backups](updates-backups.md).

## What does it cost?

Foundry AI OS itself is free and open source (MIT). What can cost money:

- **The AI.** A local model through Ollama costs nothing but electricity. A cloud AI charges you per use, billed by that provider, not by us.
- **Domains, email services, hosting.** All optional. Many have free tiers; some purchases cost real money and need your approval.

You set the budgets. Free models are preferred by default.

## Can agents spend my money?

Not beyond what you allow. Spending outside AI usage (purchases, subscriptions, domains) needs your approval by default, tied to the exact amount. AI usage is limited by budgets that are checked before a task starts and during it. Agents cannot change their own budgets or the rules. See [Guardrails and money](guardrails-and-money.md).

Honest limits: the numbers we show for paid AI are *estimates* from token counts, labelled as such. Your provider's invoice is the real charge. Set a hard spending limit in the provider's own dashboard as well. We have not tested this with a cloud AI yet.

## Why do I need an API key?

Because the AI does the thinking, and the AI is not included. A key is how a provider knows to bill your account. If you do not want a key, run a free local model with Ollama. See [Connect your AI](connect-your-ai.md).

## Do I need to be a programmer?

Not once it is installed. Today (before v0.1.0) installing means using a terminal and a few developer tools. The one-command installers are coming in v0.1.0.

## Does it really run a company by itself?

It runs the loop: a note becomes goals, projects, hires, tasks, work and reviews, without you approving each step. In our test with a small local model it got partway: one note led to a goal, a project, two hires and several finished documents, but the project was not done after 30 minutes. A stronger model should do better. We have not tested that yet. It is early software. Treat it as something to watch and guide, not something to leave alone for weeks.

## Which computers does it work on?

Only macOS has been tested. Linux should work and is a target for the first release, but we have not verified it. On Windows, use Docker or WSL. Docker has not been tested either.

## Can I use ChatGPT or Claude subscriptions instead of API keys?

For the main AI connection, no: it needs an API key or a local model. For the optional coding tools (Claude Code, Codex, OpenCode), you can use the tool's own login, but that gives weaker isolation and the tool's terms may limit automated use. The API key route is recommended. See [Connect your AI](connect-your-ai.md#api-key-or-your-own-login).

## Can I let other people in?

Yes. Invite them by email and give them a role. Humans appear in the org chart and can be given tasks. See [Team](team.md).

## Can it sign up for services for me?

No, on purpose. Anything that needs a CAPTCHA, a phone check or accepting terms is yours to do once. After you connect the account, the AI Co-founder can use it inside the guardrails.

## What if an agent does something wrong?

Press the emergency stop, or pause the company. Approvals, budgets and rate limits are there to keep mistakes small. Prompt tricks (a web page telling the AI to ignore its rules) are contained, not eliminated, which is why the hard limits are code. Read [docs/security.md](../security.md) for known gaps.

## Where do the agents come from?

The agent runtime is a trimmed fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research (MIT). See [runtime/FORK.md](../../runtime/FORK.md).

## What was "Company OS"?

The earlier name of this project. Some internal settings still use it (for example `COMPANY_OS_DATA_DIR`).

## How do I report a bug or help?

Open an issue or pull request at <https://github.com/suriyavasan/foundry-os>. Developers should read [AGENTS.md](../../AGENTS.md) first.
