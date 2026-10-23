"""Fork patch: configurable minimum-context floor (agent/model_metadata.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from company_agent.tests.conftest import RUNTIME_ROOT


def floor(tmp_path: Path, *, env: Optional[str] = None, config: Optional[str] = None) -> int:
    home = tmp_path / "h"
    home.mkdir(exist_ok=True)
    if config is not None:
        (home / "config.yaml").write_text(config)
    e = {"PATH": "/usr/bin:/bin", "HERMES_HOME": str(home), "HOME": str(tmp_path), "HERMES_DISABLE_LAZY_INSTALLS": "1"}
    if env is not None:
        e["HERMES_MIN_CONTEXT_TOKENS"] = env
    r = subprocess.run([sys.executable, "-c", "from agent.model_metadata import MINIMUM_CONTEXT_LENGTH as M; print(M)"],
                       capture_output=True, text=True, env=e, cwd=RUNTIME_ROOT, timeout=60)
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip())


def test_default_is_upstream_64k(tmp_path):
    assert floor(tmp_path) == 64_000


def test_env_var_sets_the_floor(tmp_path):
    assert floor(tmp_path, env="16384") == 16384


def test_config_key_sets_the_floor(tmp_path):
    assert floor(tmp_path, config="model:\n  min_context_tokens: 20000\n") == 20000


def test_env_beats_config(tmp_path):
    assert floor(tmp_path, env="16384", config="model:\n  min_context_tokens: 20000\n") == 16384


@pytest.mark.parametrize("bad", ["abc", "100", "-5", "0", "1.5"])
def test_invalid_values_fall_back_to_the_default(tmp_path, bad):
    assert floor(tmp_path, env=bad) == 64_000


def test_bad_config_value_falls_back(tmp_path):
    assert floor(tmp_path, config="model:\n  min_context_tokens: lots\n") == 64_000
    assert floor(tmp_path, config="model: just-a-string\n") == 64_000
