"""Company-mode workspace jail (fork patch in agent/file_safety.py + tools/file_tools_write_guards.py).

Exercises the real file tools through the registry, exactly as the agent loop would."""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

import pytest

import tools.file_tools  # noqa: F401  (registers read_file / write_file / patch / search_files)
from agent import file_safety
from tools.registry import registry

_ids = itertools.count()


@pytest.fixture
def jail(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    home = Path(os.environ["HERMES_HOME"])
    (home / "skills" / "writing").mkdir(parents=True, exist_ok=True)
    (home / "skills" / "writing" / "SKILL.md").write_text("# Writing skill\nbe clear")
    (home / "cache" / "spillover").mkdir(parents=True, exist_ok=True)
    (home / "cache" / "spillover" / "big.txt").write_text("spilled output")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET-CONTENT")
    monkeypatch.setenv("HERMES_COMPANY_JAIL", str(ws))
    monkeypatch.setenv("TERMINAL_CWD", str(ws))
    monkeypatch.chdir(ws)
    monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)
    return {"ws": ws, "outside": outside, "home": home, "tid": f"jail-{next(_ids)}"}


def run(tool: str, jail, **args):
    out = registry.dispatch(tool, args, task_id=jail["tid"])
    try:
        return json.loads(out) if isinstance(out, str) else out
    except ValueError:
        return {"raw": out}


def denied(result) -> bool:
    text = json.dumps(result)
    return "outside your workspace" in text or "denied" in text.lower() or "read-only" in text


# ---------------------------------------------------------------- reads


def test_reads_inside_workspace_work(jail):
    (jail["ws"] / "notes.md").write_text("hello workspace")
    res = run("read_file", jail, path="notes.md")
    assert "hello workspace" in json.dumps(res) and not res.get("error")
    (jail["ws"] / "abs.md").write_text("absolute ok")
    res = run("read_file", jail, path=str(jail["ws"] / "abs.md"))
    assert "absolute ok" in json.dumps(res)


@pytest.mark.parametrize("target", ["abs_outside", "dotdot", "etc_hosts", "home_dir"])
def test_reads_outside_workspace_are_denied(jail, target):
    path = {
        "abs_outside": str(jail["outside"] / "secret.txt"),
        "dotdot": "../outside/secret.txt",
        "etc_hosts": "/etc/hosts",
        "home_dir": os.path.expanduser("~/.ssh/config"),
    }[target]
    res = run("read_file", jail, path=path)
    assert denied(res), res
    assert "TOP-SECRET-CONTENT" not in json.dumps(res)


def test_symlink_escape_is_blocked(jail):
    link = jail["ws"] / "innocent.txt"
    link.symlink_to(jail["outside"] / "secret.txt")
    res = run("read_file", jail, path="innocent.txt")
    assert denied(res) and "TOP-SECRET-CONTENT" not in json.dumps(res)
    dirlink = jail["ws"] / "dir"
    dirlink.symlink_to(jail["outside"], target_is_directory=True)
    res = run("read_file", jail, path="dir/secret.txt")
    assert denied(res) and "TOP-SECRET-CONTENT" not in json.dumps(res)


def test_skills_and_spillover_are_readable(jail):
    res = run("read_file", jail, path=str(jail["home"] / "skills" / "writing" / "SKILL.md"))
    assert "be clear" in json.dumps(res)
    res = run("read_file", jail, path=str(jail["home"] / "cache" / "spillover" / "big.txt"))
    assert "spilled output" in json.dumps(res)


def test_other_hermes_home_files_are_not_readable(jail):
    (jail["home"] / "config.yaml").write_text("approvals: manual")
    res = run("read_file", jail, path=str(jail["home"] / "config.yaml"))
    assert denied(res) and "approvals: manual" not in json.dumps(res)


def test_search_outside_workspace_denied_and_inside_allowed(jail):
    (jail["ws"] / "a.txt").write_text("needle in workspace")
    assert denied(run("search_files", jail, pattern="TOP-SECRET", path=str(jail["outside"])))
    res = run("search_files", jail, pattern="needle", path=str(jail["ws"]))
    assert "needle in workspace" in json.dumps(res)


# ---------------------------------------------------------------- writes


def test_writes_inside_workspace_work_including_new_directories(jail):
    res = run("write_file", jail, path="out/report.md", content="# Report")
    assert not res.get("error"), res
    assert (jail["ws"] / "out" / "report.md").read_text() == "# Report"


@pytest.mark.parametrize("where", ["abs_outside", "dotdot", "skills", "spillover", "home_config"])
def test_writes_outside_workspace_are_denied(jail, where):
    target = {
        "abs_outside": jail["outside"] / "new.txt",
        "dotdot": jail["ws"] / ".." / "outside" / "new.txt",
        "skills": jail["home"] / "skills" / "writing" / "SKILL.md",
        "spillover": jail["home"] / "cache" / "spillover" / "x.txt",
        "home_config": jail["home"] / "config.yaml",
    }[where]
    before = target.read_text() if target.exists() else None
    res = run("write_file", jail, path=str(target), content="PWNED")
    assert denied(res), res
    assert (target.read_text() if target.exists() else None) == before


def test_write_through_symlink_is_blocked(jail):
    (jail["ws"] / "ln").symlink_to(jail["outside"] / "secret.txt")
    res = run("write_file", jail, path="ln", content="PWNED")
    assert denied(res)
    assert (jail["outside"] / "secret.txt").read_text() == "TOP-SECRET-CONTENT"


def test_patch_replace_outside_workspace_denied_and_file_untouched(jail):
    target = jail["outside"] / "secret.txt"
    res = run("patch", jail, mode="replace", path=str(target), old_string="TOP-SECRET", new_string="PWNED")
    assert denied(res)
    assert target.read_text() == "TOP-SECRET-CONTENT"


def test_v4a_patch_cannot_reach_outside_and_leaks_nothing(jail):
    target = jail["outside"] / "secret.txt"
    patch = f"*** Begin Patch\n*** Update File: {target}\n@@\n-TOP-SECRET-CONTENT\n+PWNED\n*** End Patch"
    res = run("patch", jail, mode="patch", patch=patch)
    assert denied(res)
    assert target.read_text() == "TOP-SECRET-CONTENT"
    assert "TOP-SECRET-CONTENT" not in json.dumps(res)
    patch = f"*** Begin Patch\n*** Add File: {jail['outside'] / 'added.txt'}\n+x\n*** End Patch"
    assert denied(run("patch", jail, mode="patch", patch=patch))
    assert not (jail["outside"] / "added.txt").exists()


def test_v4a_patch_inside_workspace_still_works(jail):
    (jail["ws"] / "f.txt").write_text("alpha\nbeta\n")
    patch = "*** Begin Patch\n*** Update File: f.txt\n@@\n-alpha\n+ALPHA\n*** End Patch"
    res = run("patch", jail, mode="patch", patch=patch)
    assert not res.get("error"), res
    assert (jail["ws"] / "f.txt").read_text().startswith("ALPHA")


# ---------------------------------------------------------------- unit level


def test_jail_is_off_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_COMPANY_JAIL", raising=False)
    f = tmp_path / "x.txt"
    f.write_text("x")
    assert file_safety.company_jail_root() is None
    assert file_safety.get_company_jail_error(str(f), write=True) is None
    assert file_safety.get_read_block_error(str(f)) is None  # upstream behaviour preserved


def test_jail_unit_semantics(jail):
    ws = jail["ws"]
    assert file_safety.get_company_jail_error(str(ws), write=True) is None
    assert file_safety.get_company_jail_error(str(ws / "a" / "b.txt"), write=True) is None
    assert file_safety.get_company_jail_error(str(ws) + "-sibling/x", write=True)  # prefix trick
    assert file_safety.get_company_jail_error("/", write=False)
    assert file_safety.get_write_denied_error(str(jail["outside"] / "f"))
    assert file_safety.is_write_denied(str(jail["home"] / "skills" / "x"))
    assert not file_safety.is_write_denied(str(ws / "ok.txt"))
