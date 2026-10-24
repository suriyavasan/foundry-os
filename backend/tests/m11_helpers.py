"""Shared fakes for the M11 tests: an installer-layout install, release tarballs, a fake GitHub API,
fake ``launchctl`` / ``systemctl`` / ``brew`` programs, and a tiny stand-in server process."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.core import install

REPO = "suriyavasan/foundry-os"
PLATFORM = "darwin-arm64"


# ------------------------------------------------------------------ bundles and installs


def bundle_files(version: str, *, revisions: tuple[str, ...] = ("0001",)) -> dict[str, bytes]:
    files = {
        "VERSION": f"{version}\n".encode(),
        "BUILD_INFO": f"version: {version}\nplatform: {PLATFORM}\n".encode(),
        "bin/foundryos": b"#!/bin/sh\necho foundryos\n",
        "web/dist/index.html": b"<!doctype html><div id=root></div>",
    }
    for rev in revisions:
        files[f"app/backend/alembic/versions/{rev}_x.py"] = (
            f'revision: str = "{rev}"\ndown_revision = None\n'.encode()
        )
    return files


def write_bundle(root: Path, version: str, **kw) -> Path:
    for rel, data in bundle_files(version, **kw).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "bin" / "foundryos").chmod(0o755)
    if not (root / "bin" / "companyos").exists():
        (root / "bin" / "companyos").symlink_to("foundryos")
    return root


def make_tarball(
    dest: Path, version: str, *, platform: str = PLATFORM, top: str = "foundry-os", **kw
) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"foundry-os-{version}-{platform}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for rel, data in bundle_files(version, **kw).items():
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            info.mode = 0o755 if rel == "bin/foundryos" else 0o644
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(f"{top}/bin/companyos")
        link.type = tarfile.SYMTYPE
        link.linkname = "foundryos"
        tar.addfile(link)
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Release:
    version: str
    tarball: Path
    sums: bytes


def make_release(dest: Path, version: str, *, bad_sum: bool = False, **kw) -> Release:
    dest.mkdir(parents=True, exist_ok=True)
    tarball = make_tarball(dest, version, **kw)
    digest = "0" * 64 if bad_sum else sha256(tarball)
    return Release(version, tarball, f"{digest}  {tarball.name}\n".encode())


def fake_github(
    releases: dict[str, Release],
    *,
    latest: str | None,
    with_sums: bool = True,
    calls: list[str] | None = None,
) -> Callable[[], httpx.Client]:
    """A client factory answering the GitHub releases API and the asset downloads."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        if url.startswith("https://dl.example/"):
            version, _, rest = url.removeprefix("https://dl.example/").partition("/")
            rel = releases[version]
            if rest == "SHA256SUMS":
                return httpx.Response(200, content=rel.sums)
            return httpx.Response(200, content=rel.tarball.read_bytes())
        prefix = f"https://api.github.com/repos/{REPO}/releases/"
        assert url.startswith(prefix), url
        tail = url.removeprefix(prefix)
        if tail == "latest":
            version = latest
        elif tail.startswith("tags/v"):
            version = tail.removeprefix("tags/v")
        else:
            return httpx.Response(404, json={"message": "Not Found"})
        if version is None or version not in releases:
            return httpx.Response(404, json={"message": "Not Found"})
        rel = releases[version]
        assets = [
            {
                "name": rel.tarball.name,
                "browser_download_url": f"https://dl.example/{version}/{rel.tarball.name}",
            }
        ]
        if with_sums:
            assets.append(
                {
                    "name": "SHA256SUMS",
                    "browser_download_url": f"https://dl.example/{version}/SHA256SUMS",
                }
            )
        return httpx.Response(
            200,
            json={
                "tag_name": f"v{version}",
                "html_url": f"https://github.com/{REPO}/releases/tag/v{version}",
                "assets": assets,
            },
        )

    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    return factory


# ------------------------------------------------------------------ fake system programs


def fake_tool(bin_dir: Path, name: str, log: Path, script: str = "") -> Path:
    """An executable that appends its arguments to ``log`` (one line per call) and then runs ``script``."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(
        "#!/bin/sh\n"
        f'echo "{name} $*" >> "{log}"\n' + textwrap.dedent(script) + "\nexit ${FAKE_RC:-0}\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


# ------------------------------------------------------------------ a stand-in server process

FAKE_SERVER = """
import json, os, signal, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])
data = os.environ["FOUNDRYOS_DATA_DIR"]
run = os.path.join(data, "run")
os.makedirs(run, exist_ok=True)
path = os.path.join(run, "supervisor.%d.json" % os.getpid())
with open(path, "w") as fh:
    json.dump({"role": "supervisor", "pid": os.getpid(), "port": port, "started_at": int(time.time()),
               "mode": os.environ.get("FOUNDRYOS_RUN_MODE", "foreground")}, fh)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/api/health":
            body = json.dumps({"status": "ok", "database": "ok", "version": "9.9.9", "auth_mode": "password"}).encode()
        else:
            body = json.dumps({"state": "setup_required"}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

srv = HTTPServer(("127.0.0.1", port), H)
def bye(*_):
    try: os.unlink(path)
    except OSError: pass
    os._exit(0)
signal.signal(signal.SIGTERM, bye)
print("fake server up on", port, flush=True)
srv.serve_forever()
"""


def fake_launcher(tmp: Path) -> Path:
    server = tmp / "fake_server.py"
    server.write_text(FAKE_SERVER)
    launcher = tmp / "fake-foundryos"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{server}" "$@"\n')
    launcher.chmod(0o755)
    return launcher


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ environment


@dataclass
class Env:
    home: Path
    data: Path
    bin: Path
    log: Path
    prefix: Path

    def current_version_dir(self) -> Path:
        return self.prefix / "versions" / "0.1.0"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    """A private HOME, data dir and PATH (fake system programs only); nothing real is reachable."""
    home = tmp_path / "home"
    data = tmp_path / "data dir"
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    home.mkdir()
    # core tools the fakes use come from /bin and /usr/bin through absolute shebangs / sh builtins
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("FOUNDRYOS_DATA_DIR", str(data))
    monkeypatch.setenv("FOUNDRYOS_NO_BROWSER", "1")
    # a free port of our own: the real instance on :8700 must never be probed, stopped or reused
    monkeypatch.setenv("FOUNDRYOS_PORT", str(free_port()))
    for name in (
        "FOUNDRYOS_INSTALLED",
        "FOUNDRYOS_UPDATE_CHECK",
        "FOUNDRYOS_RUN_MODE",
        "FOUNDRYOS_LOG_FILE",
        "COMPANY_OS_PORT",
        "COMPANY_OS_DATABASE_URL",
        "COMPANY_OS_DATA_DIR",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "HOMEBREW_CELLAR",
        "HOMEBREW_PREFIX",
        "FOUNDRYOS_PROG",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(install, "in_container", lambda: False)
    return Env(home, data, bin_dir, tmp_path / "tool-calls.log", tmp_path / "foundry-os-prefix")


def installer_layout(env: Env, monkeypatch: pytest.MonkeyPatch, version: str = "0.1.0") -> Path:
    """Pretend this process runs from ``<prefix>/versions/<version>`` made by install.sh."""
    root = write_bundle(env.prefix / "versions" / version, version)
    (env.prefix / "current").symlink_to(f"versions/{version}")
    monkeypatch.setattr(install, "bundle_root", lambda: root)
    monkeypatch.setattr(install, "APP_VERSION", version)
    return root


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(cli_app, list(args), input=input)


def as_json(result) -> dict:
    return json.loads(result.stdout)
