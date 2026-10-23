# AGENTS.md — runtime/ (Company OS agent runtime, a Hermes Agent fork)

Read `../AGENTS.md` first, then `FORK.md` (what was kept, dropped and changed) and
`../docs/specs/m3-company-agent-runtime.md`.

- This tree is a **pruned fork of Hermes Agent** (MIT, upstream `db1fa153`). Upstream files keep their
  headers. **Every change to an upstream file must be logged in `FORK.md`** (file + reason) and marked
  in the code with a `COMPANY FORK` comment so syncs with upstream stay tractable.
- Our code: `company_agent/` (launcher, protocol, prompts, tests), `tools/company_tools.py` (company
  toolset), `scripts/`. Everything else is upstream.
- Nested `AGENTS.md` files under `agent/`, `tools/`, `gateway/` ... are upstream's notes about their own
  code; they describe upstream workflows (e.g. its `tests/` tree) that are not shipped here.
- Never read or write the real `~/.hermes`: set `HERMES_HOME` to a temp dir before importing Hermes.
- Never import Hermes from `backend/`; the control plane talks to this runtime only through the
  `python -m company_agent.run` process boundary (stdin spec -> stdout JSONL).
- Checks: `.venv/bin/python scripts/check_imports.py` and `.venv/bin/python -m pytest` (see `README.md`).
