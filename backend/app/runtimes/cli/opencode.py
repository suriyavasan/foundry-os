"""OpenCode adapter (``cli:opencode``). Flags verified against ``opencode run --help`` 1.18.3 on
2026-10-24 and a live run against a local OpenAI-compatible model (docs/cli-runtimes.md).

All configuration is a per-run file named by ``OPENCODE_CONFIG`` (provider, MCP server, permissions);
project config, plugins (``--pure``), auto-update, external skills and Claude Code compatibility are
switched off. ``--auto`` (auto-approve) is never used: shell, web and subagent tools are denied."""

from __future__ import annotations

import json
import os
from typing import Any

from app.runtimes.cli.base import (
    CliAdapter,
    Invocation,
    Parsed,
    RunPrep,
    StreamParser,
    clip,
)
from app.runtimes.cli.catalog import CLIS

CUSTOM_PROVIDER_ID = "company"
KEY_ENV = "COMPANY_OS_CLI_API_KEY"
# Built-in OpenCode providers whose key is an environment variable.
PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "google": "GOOGLE_GENERATIVE_AI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}
PERMISSIONS = {
    "read": "allow",
    "edit": "allow",
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "company_*": "allow",
    "bash": "deny",
    "task": "deny",
    "skill": "deny",
    "lsp": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "codesearch": "deny",
    "external_directory": "deny",
    "doom_loop": "deny",
}
TEST_PERMISSIONS = {k: "deny" for k in PERMISSIONS}


class OpenCodeParser(StreamParser):
    def feed(self, obj: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        kind = obj.get("type")
        part = obj.get("part") if isinstance(obj.get("part"), dict) else {}
        out: list[tuple[str, dict[str, Any]]] = []
        if kind == "text":
            text = str(part.get("text") or "")
            if text.strip():
                self.parsed.final_text = text
                out.append(("message", {"text": clip(text, 2000)}))
        elif kind == "tool_use":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            name = str(part.get("tool") or "tool")
            status = state.get("status")
            if status in ("pending", "running"):
                out.append(("tool.started", {"name": name}))
            else:
                out.append(
                    (
                        "tool.started",
                        {
                            "name": name,
                            "input_preview": clip(json.dumps(state.get("input"), default=str), 300),
                        },
                    )
                )
                out.append(
                    (
                        "tool.completed",
                        {
                            "name": name,
                            "is_error": status == "error",
                            "result_preview": clip(state.get("output") or state.get("error"), 300),
                        },
                    )
                )
        elif kind == "step_finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}

            def n(v: Any) -> int:
                return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

            self.parsed.input_tokens += (
                n(tokens.get("input")) + n(cache.get("read")) + n(cache.get("write"))
            )
            self.parsed.output_tokens += n(tokens.get("output")) + n(tokens.get("reasoning"))
            cost = part.get("cost")
            if isinstance(cost, int | float) and not isinstance(cost, bool):
                self.parsed.cost_usd = (self.parsed.cost_usd or 0.0) + float(cost)
            if part.get("reason") == "stop":
                self.parsed.finished = True
        elif kind == "error":
            err = obj.get("error")
            if isinstance(err, dict):
                data = err.get("data") if isinstance(err.get("data"), dict) else {}
                msg = data.get("message") or err.get("message") or err.get("name")
            else:
                msg = err
            self.parsed.error = clip(msg or "OpenCode reported an error", 400)
        return out


class OpenCodeAdapter(CliAdapter):
    spec = CLIS["opencode"]
    parser_class = OpenCodeParser
    tool_prefix = "company_"
    config_hint = "Install with `npm install -g opencode-ai`; `opencode auth login`, or point it at a company model provider."

    def classify_failure(self, parsed: Parsed) -> str:
        return f"OpenCode reported an error: {parsed.error}"

    # ------------------------------------------------------------ provider wiring

    def _provider_config(
        self, prep: RunPrep, env: dict[str, str]
    ) -> tuple[dict[str, Any], str | None]:
        """(``provider`` config block, ``provider/model`` string)."""
        prov = prep.provider
        if prov is None:
            model = prep.model or ""
            if "/" not in model:
                return {}, None
            vendor = model.split("/", 1)[0]
            key_env = PROVIDER_KEY_ENV.get(vendor)
            if key_env and prep.secret:
                env[key_env] = prep.secret
            return {}, model
        if prov.kind == "anthropic":
            if prov.api_key or prep.secret:
                env["ANTHROPIC_API_KEY"] = prov.api_key or prep.secret or ""
            return {}, f"anthropic/{prov.model}"
        if prov.kind == "openrouter":
            if prov.api_key or prep.secret:
                env["OPENROUTER_API_KEY"] = prov.api_key or prep.secret or ""
            return {}, f"openrouter/{prov.model}"
        api_key = prov.api_key or prep.secret
        options: dict[str, Any] = {"baseURL": prov.base_url}
        if api_key:
            env[KEY_ENV] = api_key
            options["apiKey"] = "{env:" + KEY_ENV + "}"
        else:
            options["apiKey"] = "local"  # a public placeholder: local servers ignore the key
        output = max(1024, min(8192, prov.context_length // 4))
        block = {
            CUSTOM_PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": prov.name[:60],
                "options": options,
                "models": {
                    prov.model: {
                        "name": prov.model,
                        "tool_call": True,
                        "limit": {"context": prov.context_length, "output": output},
                    }
                },
            }
        }
        return block, f"{CUSTOM_PROVIDER_ID}/{prov.model}"

    def build(self, prep: RunPrep) -> Invocation:
        env = self.child_env(prep)
        env.update(
            {
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
                "OPENCODE_DISABLE_CLAUDE_CODE": "1",
                "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
                "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
            }
        )
        providers, model_ref = self._provider_config(prep, env)
        if model_ref is None:
            from app.runtimes.base import RuntimeAdapterError, RuntimeErrorKind

            raise RuntimeAdapterError(
                RuntimeErrorKind.CONFIG,
                "OpenCode needs a model: choose a company model provider for it, or a 'provider/model' name",
            )
        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "provider": providers,
            "model": model_ref,
            "permission": TEST_PERMISSIONS if prep.mode == "test" else PERMISSIONS,
        }
        if prep.mode != "test":
            token_file = self.write_token_file(prep)
            config["mcp"] = {
                "company": {
                    "type": "local",
                    "command": self.mcp_command(prep),
                    "environment": self.mcp_server_env(prep, token_file),
                    "enabled": True,
                }
            }
        config_text = json.dumps(config, indent=2)
        path = prep.run_dir / "opencode.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(config_text)
        env["OPENCODE_CONFIG"] = str(path)
        argv = [
            prep.binary,
            "run",
            prep.prompt,
            "--format",
            "json",
            "--dir",
            str(prep.workspace),
            "--pure",
            "-m",
            model_ref,
        ]
        return Invocation(
            argv=argv, env=env, cwd=prep.workspace, prompt_index=2, config_text=config_text
        )
