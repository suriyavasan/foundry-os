#!/usr/bin/env python3
"""Write THIRD_PARTY_NOTICES for a release bundle.

Reads the installed distributions of the bundle's site-packages directories (METADATA and the license
files shipped in each .dist-info), the Hermes Agent license of the agent runtime, and, when given,
the production web dependencies listed by ``pnpm licenses list --prod --json``.

    python3 -I packaging/gen_notices.py --out THIRD_PARTY_NOTICES \\
        --site-packages python/backend/lib/python3.14/site-packages \\
        --site-packages python/runtime/lib/python3.14/site-packages \\
        --hermes-license runtime/LICENSE --python-version 3.14.9 --pbs-tag 20261021 \\
        [--web-licenses web-licenses.json]

License texts that are byte-identical are printed once, followed by the packages they apply to.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from importlib import metadata
from pathlib import Path

LICENSE_FILE = re.compile(r"(^|/)(licen[sc]es?|copying|notice|authors)([._-][^/]*)?$", re.IGNORECASE)
SKIP_DISTS = {"pip", "setuptools", "wheel"}


def license_label(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    lic = (meta.get("License") or "").strip()
    if lic and len(lic) < 80 and "\n" not in lic:
        return lic
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if "License ::" in c]
    if classifiers:
        return ", ".join(classifiers)
    return "see license text" if lic else "unknown"


def license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for f in dist.files or []:
        parts = str(f).split("/")
        if not parts[0].endswith(".dist-info"):
            continue
        rel = "/".join(parts[1:])
        if not LICENSE_FILE.search(rel) and not rel.startswith("licenses/"):
            continue
        path = Path(dist.locate_file(f))
        if path.is_file():
            try:
                out.append((rel, path.read_text(encoding="utf-8", errors="replace").strip()))
            except OSError:
                continue
    return out


def collect(site_dirs: list[Path]) -> dict[tuple[str, str], metadata.Distribution]:
    found: dict[tuple[str, str], metadata.Distribution] = {}
    for site in site_dirs:
        for dist in metadata.distributions(path=[str(site)]):
            name = (dist.metadata.get("Name") or "").strip()
            if not name or name.lower() in SKIP_DISTS:
                continue
            found.setdefault((name.lower().replace("_", "-"), dist.version), dist)
    return found


def web_packages(path: Path | None) -> list[tuple[str, str, str]]:
    if path is None or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: set[tuple[str, str, str]] = set()
    for lic, pkgs in data.items():
        for pkg in pkgs:
            for version in pkg.get("versions") or [""]:
                rows.add((pkg.get("name", "?"), version, lic))
    return sorted(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--site-packages", action="append", default=[], type=Path)
    ap.add_argument("--hermes-license", type=Path)
    ap.add_argument("--project-license", type=Path)
    ap.add_argument("--web-licenses", type=Path)
    ap.add_argument("--python-version", default="3.14")
    ap.add_argument("--pbs-tag", default="")
    args = ap.parse_args(argv)

    dists = collect(args.site_packages)
    lines: list[str] = []
    w = lines.append
    w("THIRD-PARTY NOTICES - Foundry AI OS")
    w("=" * 36)
    w("")
    w("Foundry AI OS is released under the MIT license (file LICENSE). This bundle also contains the")
    w("software listed below, each under its own license.")
    w("")
    w("1. Hermes Agent (the agent runtime in app/runtime)")
    w("-" * 50)
    w("Derived from Hermes Agent by Nous Research (https://github.com/NousResearch/hermes-agent),")
    w("MIT license:")
    w("")
    if args.hermes_license and args.hermes_license.is_file():
        w(args.hermes_license.read_text(encoding="utf-8", errors="replace").strip())
    else:
        w("(Hermes Agent license file not found at build time)")
    w("")
    w("2. Python")
    w("-" * 50)
    w(f"python/backend and python/runtime are CPython {args.python_version} builds from")
    w("python-build-standalone (https://github.com/astral-sh/python-build-standalone"
      + (f", release {args.pbs_tag}" if args.pbs_tag else "") + "),")
    w("distributed under the Python Software Foundation License; the build bundles components under")
    w("their own permissive licenses (OpenSSL/Apache-2.0, zlib, bzip2, expat, libffi, SQLite (public")
    w("domain), xz, ncurses and others). Full texts: the LICENSE.txt in python/*/lib/python3.*/ and")
    w("https://github.com/astral-sh/python-build-standalone/blob/main/docs/running.rst .")
    w("")
    w(f"3. Python packages ({len(dists)})")
    w("-" * 50)
    by_text: dict[str, list[str]] = {}
    for (name, version), dist in sorted(dists.items()):
        w(f"{name} {version} - {license_label(dist)}")
        texts = license_texts(dist)
        for _rel, text in texts:
            if text:
                digest = hashlib.sha256(text.encode()).hexdigest()
                by_text.setdefault(digest, [text]).append(f"{name} {version}")
    w("")
    w("License and notice texts of those packages (identical texts are printed once):")
    for digest, (text, *owners) in by_text.items():
        w("")
        w("-" * 72)
        w("Applies to: " + ", ".join(sorted(set(owners))))
        w("-" * 72)
        w(text)
    web = web_packages(args.web_licenses)
    w("")
    w("4. Web UI packages (bundled into web/dist)")
    w("-" * 50)
    if web:
        for name, version, lic in web:
            w(f"{name} {version} - {lic}")
    else:
        w("(the list of production web dependencies was not available at build time; they are the")
        w(" dependencies of web/package.json: React, Radix UI, TanStack Query, React Router, Motion,")
        w(" lucide-react, clsx, qrcode-generator - all under permissive licenses (MIT/ISC/Apache-2.0))")
    w("")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(dists)} python packages, {len(web)} web packages)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
