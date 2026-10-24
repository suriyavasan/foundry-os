# Troubleshooting

Start here, always:

```bash
companyos doctor        # will also be `foundryos doctor`
```

It checks Python and tools, the screen build, the database and its version, the agent runtime, Ollama and its context size, Docker, tunnel tools, free disk space, file permissions, sign-in mode, and running processes. For each problem it prints the fix. It exits with an error if something important fails.

## "Port 8700 is already in use"

Another copy is running. `doctor` lists the processes it knows about. Stop the other copy, or pick another port:

```bash
companyos up --port 8710
```

## The page will not load

- Check that Foundry is running (`companyos status`, or `doctor`).
- Use `http://127.0.0.1:8700`, not `https`.
- After an update, hard-reload the page (Cmd-Shift-R or Ctrl-Shift-R). An old tab can hold on to old files.

## I cannot create my account from my phone

Setup from another device needs a **setup token**. On the computer that runs Foundry:

```bash
companyos auth setup-token
```

Or create the account from that computer, where no token is needed. If the screen says the server will not open to the network, read [Reach it from anywhere](reach-it-from-anywhere.md): you must first sign in with a strong password.

## I forgot my password

On the computer that runs Foundry:

```bash
companyos auth reset-password YOUR_USERNAME
```

It signs you out everywhere and lets you set a new password. This only works on that computer, on purpose.

## Agents do not do anything

Work through these in order:

1. **Is an AI connected and passing its test?** See [Connect your AI](connect-your-ai.md).
2. **Ollama context.** If long tasks seem to forget things or get cut off, Ollama is probably using a short context. Restart it with `OLLAMA_CONTEXT_LENGTH=32768 ollama serve`. `doctor` shows the size a loaded model is really using.
3. **Is the company paused or stopped?** Check `/status`, or lift an emergency stop with `companyos resume --all`.
4. **Is the agent runtime installed?** Without it the screen runs, but agents cannot work. `doctor` tells you. From source, set it up using section 2 of [docs/deploy.md](../deploy.md).
5. **Is the budget used up?** Tasks over budget are blocked before they start. Check Finance, or `companyos budget show`.
6. **Does a run fail at once with a credential error?** The provider needs a stored key. Add one in Integrations (or `companyos credentials set NAME --stdin`) and attach it to the provider.
7. **Is it just slow?** A small local model can take one to six minutes per task.

## "Database schema is missing or out of date"

From source, run `uv run alembic upgrade head` in the `backend` folder. Installed copies upgrade themselves on start.

## Not enough disk space

`doctor` warns when under 2 GB is free. The agent runtime is about 180 MB. The data folder grows with files the agents make. Free space, or move the data with `COMPANY_OS_DATA_DIR`. Old backups in `backups/` can be deleted once you have copied a recent one elsewhere.

## A run was cut off when my computer slept or crashed

This is handled. A run that loses its worker is marked `worker_lost` and retried automatically. In our test the task finished on the second try.

## Docker

The Docker image and Compose files are written but were never run by us, so expect rough edges. If the container will not start, check `docker logs foundry-os`. If Ollama runs on your computer rather than in Docker, register its address as `http://host.docker.internal:11434`.

## Still stuck?

Open an issue at <https://github.com/suriyavasan/foundry-os/issues> and include the output of `companyos doctor`. Look through it and remove anything private before you post it.
