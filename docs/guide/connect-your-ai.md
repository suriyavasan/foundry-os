# Connect your AI

Foundry AI OS does not include an AI. You connect one, and you pay that provider directly (or run a free model on your own computer). The company cannot run until a connection works.

## What "working" means

When you connect an AI, Foundry runs a short test. It checks that the service:

1. can be reached,
2. accepts your key,
3. supports **tool calling** (the AI can use buttons and tools, not just chat),
4. has a large enough memory for a conversation (a "context window" of at least 32,768 tokens, roughly 24,000 words),
5. has the model you chose.

If a check fails, the screen says which one and what to try.

> The multi-step test and the "cannot continue until it works" gate are part of the new setup screens that are still being finished (see the [roadmap](../roadmap.md)). Provider testing from the settings and the command line (`providers test`) exists today.

## Your options

| Choice | Best for | What you need |
|---|---|---|
| **OpenRouter** | Many models behind one key, including some free ones | An OpenRouter API key |
| **Anthropic** | Claude models | An Anthropic API key |
| **OpenAI** | GPT models | An OpenAI API key |
| **OpenAI-compatible** | Any other service that speaks the same language (Groq, Together, DeepSeek, a company gateway) | The service's web address ("base URL") and a key |
| **Ollama (local)** | Free, private, runs on your computer | Ollama installed and a model downloaded |

An API key is a long secret password that lets the program use your account with that provider. You create it in the provider's website. Paste it into Foundry once. After that it is stored encrypted and never shown again, not to you, not to the agents, and not in logs.

## Local models with Ollama

[Ollama](https://ollama.com) runs AI models on your own computer. Nothing you type leaves your machine.

```bash
ollama pull qwen2.5:7b
OLLAMA_CONTEXT_LENGTH=32768 ollama serve
```

**Important tip:** start Ollama with `OLLAMA_CONTEXT_LENGTH=32768`. Without it, Ollama silently cuts long conversations short and your agents forget what they were doing. (If you use the Ollama menu-bar app on a Mac, set it with `launchctl setenv OLLAMA_CONTEXT_LENGTH 32768` and restart the app.)

Then let Foundry find it:

```bash
companyos providers detect --register      # will be `foundryos ...`, see the CLI page
companyos providers test "Ollama 127.0.0.1:11434"
```

The test reports the context size it was actually given and warns if it is under 32K.

Be honest with yourself about quality. We tested with `qwen2.5:7b`. It works, and its cost is a verified $0, but it takes one to six minutes per task and does not follow long multi-step plans well. A bigger local model or a cloud key gives noticeably better planning. We have not verified a cloud model end to end yet.

## Free first, by default

Foundry prefers a free model (a local one) when it can, and uses a paid one only when you allow it. You can pin an employee to a specific model, or ask for the best paid model. See [Guardrails and money](guardrails-and-money.md#free-first-routing).

## Coding tools (optional)

Claude Code, Codex CLI and OpenCode are programs you run in a terminal that write and edit files. Foundry can use them as the "hands" of your employees while your API connection stays the "brain" of the company (Co-founder, HR, leads, reviews).

> Status: this is in progress and not in a release yet. Claude Code and OpenCode were found installed on the developer's machine; Codex CLI was not. Treat it as experimental until you see it in the release notes.

How it works:

- The company side always uses your API connection. Coding tools only do employee work.
- Foundry can show you the exact install command and run it only after you confirm. It never uses `sudo`.
- Signing in to a coding tool is always your step, in your own terminal. Foundry never automates logins.
- Shell commands run only inside a real sandbox. Dangerous modes (such as "skip all permission checks") are never used.

### API key or your own login?

Each coding tool can authenticate in one of two ways. Pick one per tool.

| | **API key (recommended)** | **Your own login** |
|---|---|---|
| How | You give Foundry a key for that tool | The tool uses the login already on your computer |
| Isolation | Strongest. Each employee gets its own private home folder; the key is passed only to that one process | Weaker. The tool must use your real home folder to find your login |
| Cost shown as | Estimated | Unknown (often a subscription) |
| Watch out for | Pay-per-use costs | The tool's own terms may limit automated use. Check them before you turn this on |

Foundry never reads, copies or logs a coding tool's login files.

## Changing your AI later

Open Settings and look for the AI section (or Integrations in the current build). You can add another provider, test it, and choose which one manages the company.
