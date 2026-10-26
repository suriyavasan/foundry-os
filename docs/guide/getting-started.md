# Getting started

This page takes you from nothing to your first note being handled by your AI Co-founder.

> **Heads up:** the one-command installers (Homebrew, the install script, Docker) arrive with the first release, v0.1.0. Until then you run Foundry AI OS from source, which needs a terminal and a few developer tools. The steps after "Open it" are the same either way.

## 1. Install it

Pick one. Full details and exact commands are in the [README](../../README.md#install).

| Option | Works on | Command to start |
|---|---|---|
| Homebrew (coming in v0.1.0) | macOS, Linux | `brew install suriyavasan/tap/foundry-os`, then `foundryos` |
| One-line installer (coming in v0.1.0) | macOS, Linux | `curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh \| sh` |
| Docker (coming in v0.1.0) | Any system, including Windows | `docker run -d --name foundry-os -p 8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest` |
| From source (works today) | macOS (tested), Linux | see [docs/deploy.md](../deploy.md) |

From source, in short:

```bash
cd backend
uv sync
uv run companyos doctor     # lists anything missing, with the fix
uv run companyos up         # starts it at http://127.0.0.1:8700
```

The `doctor` command is your friend. Run it any time something seems off.

## 2. Open it

Go to <http://127.0.0.1:8700> (or <http://localhost:8700> with Docker) in your browser. On a computer with a screen, `foundryos` opens it for you.

**No screen? (a server, a VPS, a Mac you reach over SSH.)** Nothing opens a browser there. Set it up in the terminal instead:

```bash
foundryos setup
```

It does steps 3 to 6 below in your terminal (account, optional two-factor sign-in, company and idea, your AI, how work gets done) and offers a free public address and the background service at the end. It is safe to run again: finished steps are skipped. On Linux, answer yes to "keep it running after you log out" (this is *linger*). To use the web app from your own computer afterwards, forward the port (`ssh -N -L 8700:127.0.0.1:8700 user@host`, then open <http://localhost:8700>) or use the public address. Details and a script-friendly form are in [Installing: headless server](../install.md#headless-server--ssh) and [the command line](cli.md#set-up-from-the-terminal).

## 3. Create your account

Choose a username and a strong password (at least 12 characters, not a common one). This makes you the owner of the company.

If you open the page from a different device than the one running Foundry AI OS, you also need a **setup token**. It proves you control the machine. On that machine, run `foundryos auth setup-token` (`companyos auth setup-token` in a source checkout, see [the command line](cli.md)). From the machine itself no token is needed. `foundryos setup` asks for the token itself when it needs one.

## 4. Connect your AI

Foundry AI OS needs an AI to think with. You bring your own: a cloud key or a free local model. It tests the connection and does not let you continue until the AI works. See [Connect your AI](connect-your-ai.md).

## 5. Choose how work gets done

- **API only:** every AI employee runs on the AI you just connected. Simplest. Start here.
- **API plus coding tools:** your AI runs the management side (Co-founder, HR, planning, reviews). Employees do their work through a coding tool you have, such as Claude Code, Codex or OpenCode.

You can skip this step. You can change it later in Settings.

## 6. Write your first note

Find the **Command Center** and type something in plain words:

> Build a landing page for our first product. Keep it simple and friendly.

The AI Co-founder reads your note, decides what it means (a goal, a task, a requirement, an idea), creates the work and replies to you with what it did. Every note gets an answer.

You can also type a slash command such as `/status`. See [How your company works](how-your-company-works.md#command-center-and-slash-commands).

## 7. Watch, and stay in charge

- The **organization** view shows your team as circles. A ring shows what each one is doing right now.
- **Approvals** shows anything waiting for your yes or no. See [Guardrails and money](guardrails-and-money.md).
- **Finance** shows what has been spent, and whether each number is verified, estimated or unknown.

## What to expect

Be realistic. A small local model takes one to six minutes per task and follows long plans only partly. In our test, one note led to a goal, a project, two hires and several finished documents, but the project did not finish in 30 minutes. A stronger model, such as a cloud model, is the biggest improvement. We have not yet run it with a cloud model, so we cannot say how much better it is.

## Next

- [Connect your AI](connect-your-ai.md)
- [How your company works](how-your-company-works.md)
- [Troubleshooting](troubleshooting.md)
