#!/usr/bin/env python
"""List imports in the pruned tree that point at modules which exist upstream but were dropped.

    python scripts/scan_dropped_imports.py ../vendor/hermes-agent

Pure ``ast`` scan (no imports executed). Use it after a prune or upstream sync to review which kept
modules still reference dropped surfaces, and confirm none sits on the company runtime's hot path
(see FORK.md, "Dangling imports").
"""

from __future__ import annotations

import ast
import collections
import os
import sys
from pathlib import Path

SKIP_DIRS = {".venv", ".git", "node_modules", "__pycache__", "tests", "web", "website", "apps", "ui-tui"}
OWN_DIRS = {"company_agent", "scripts"}


def modules(root: Path) -> set[str]:
    mods: set[str] = set()
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        rel = Path(dp).relative_to(root)
        for f in fn:
            if f.endswith(".py"):
                name = ".".join((*rel.parts, f[:-3]))
                mods.add(name[: -len(".__init__")] if name.endswith(".__init__") else name)
    return mods


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    here = Path(__file__).resolve().parent.parent
    upstream = modules(Path(sys.argv[1]))
    ours = modules(here)
    top = {m.split(".")[0] for m in upstream}
    found: dict[str, set[tuple[str, int]]] = collections.defaultdict(set)
    for dp, dn, fn in os.walk(here):
        dn[:] = [d for d in dn if d not in SKIP_DIRS | OWN_DIRS]
        for f in fn:
            if not f.endswith(".py"):
                continue
            path = Path(dp) / f
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
                for n in names:
                    if n.split(".")[0] in top and n in upstream and n not in ours:
                        found[n].add((str(path.relative_to(here)), node.lineno))
    for n, sites in sorted(found.items()):
        first = sorted(sites)[0]
        print(f"{n:55} {len(sites):3} site(s), e.g. {first[0]}:{first[1]}")
    print(f"\n{len(found)} dropped module(s) still referenced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
