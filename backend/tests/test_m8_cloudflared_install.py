"""The cloudflared install: refuses without confirmation or a published checksum, verifies SHA-256,
only ever downloads from the official release location, installs into data/bin."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile

import pytest

from app.core.config import Settings
from app.core.errors import Conflict
from app.integrations import cloudflared
from app.integrations.base import IntegrationHttp, ProviderError
from tests.m8_helpers import FakeNetwork

API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"
BASE = "https://github.com/cloudflare/cloudflared/releases/download/2026.10.3/"
SCRIPT = b"#!/bin/sh\necho 'cloudflared version 2026.10.3'\n"


def tgz(payload: bytes = SCRIPT, name: str = "cloudflared") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def release(
    asset: str, blob: bytes, *, digest: str | None = "auto", notes: str | None = "auto", url=None
):
    sha = hashlib.sha256(blob).hexdigest()
    entry = {"name": asset, "browser_download_url": url or BASE + asset, "size": len(blob)}
    if digest:
        entry["digest"] = "sha256:" + (sha if digest == "auto" else digest)
    body = (
        f"### SHA256 Checksums:\n```\n{asset}: {sha if notes == 'auto' else notes}\n```\n"
        if notes
        else "no checksums"
    )
    return {"tag_name": "2026.10.3", "assets": [entry], "body": body}


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path}/x.db", bin_dir=tmp_path / "bin"
    )
    net = FakeNetwork()
    return settings, net, IntegrationHttp(net.transport())


ASSET = "cloudflared-darwin-arm64.tgz"


def test_platform_assets():
    assert cloudflared.platform_asset("Darwin", "arm64") == "cloudflared-darwin-arm64.tgz"
    assert cloudflared.platform_asset("Darwin", "x86_64") == "cloudflared-darwin-amd64.tgz"
    assert cloudflared.platform_asset("Linux", "aarch64") == "cloudflared-linux-arm64"
    assert cloudflared.platform_asset("Linux", "x86_64") == "cloudflared-linux-amd64"
    assert cloudflared.platform_asset("Windows", "AMD64") == "cloudflared-windows-amd64.exe"
    assert cloudflared.platform_asset("FreeBSD", "x86_64") is None
    assert cloudflared.platform_asset("Linux", "riscv64") is None


def test_install_without_confirmation_downloads_nothing(env):
    settings, net, http = env
    with pytest.raises(Conflict) as err:
        cloudflared.install(settings, http, confirm=False, asset_name=ASSET)
    assert err.value.details["reason"] == "confirmation_required"
    assert net.requests == []  # not even the metadata lookup
    assert not settings.resolved_bin_dir.exists()


def test_a_verified_download_is_installed_executable_into_data_bin(env):
    settings, net, http = env
    blob = tgz()
    net.add("GET", API, release(ASSET, blob))
    net.add("GET", BASE + ASSET, content=blob)
    path = cloudflared.install(settings, http, confirm=True, asset_name=ASSET)
    assert path == settings.resolved_bin_dir / "cloudflared"
    assert path.read_bytes() == SCRIPT and os.access(path, os.X_OK)
    assert cloudflared.find_cloudflared(settings) == str(path)
    assert cloudflared.binary_version(str(path)) == "2026.10.3"
    # no temp or staging files are left behind
    assert [p.name for p in settings.resolved_bin_dir.iterdir()] == ["cloudflared"]


def test_a_checksum_mismatch_installs_nothing(env):
    settings, net, http = env
    blob = tgz()
    claimed = hashlib.sha256(b"something else").hexdigest()
    net.add("GET", API, release(ASSET, blob, digest=claimed, notes=claimed))
    net.add("GET", BASE + ASSET, content=blob)
    with pytest.raises(ProviderError) as err:
        cloudflared.install(settings, http, confirm=True, asset_name=ASSET)
    assert "does not match the published checksum" in err.value.message
    assert list(settings.resolved_bin_dir.iterdir()) == []
    assert cloudflared.find_cloudflared(
        settings
    ) is None or "bin" not in cloudflared.find_cloudflared(settings)


def test_no_published_checksum_means_no_download(env):
    settings, net, http = env
    blob = tgz()
    net.add("GET", API, release(ASSET, blob, digest=None, notes=None))
    net.add("GET", BASE + ASSET, content=blob)
    with pytest.raises(ProviderError) as err:
        cloudflared.install(settings, http, confirm=True, asset_name=ASSET)
    assert "no published SHA-256" in err.value.message
    assert not net.calls("GET", "releases/download")  # the binary was never fetched


def test_disagreeing_published_checksums_refuse(env):
    settings, net, http = env
    blob = tgz()
    net.add("GET", API, release(ASSET, blob, notes=hashlib.sha256(b"x").hexdigest()))
    with pytest.raises(ProviderError) as err:
        cloudflared.plan_install(settings, http, ASSET)
    assert "disagree" in err.value.message


def test_a_download_url_outside_the_official_release_location_is_refused(env):
    settings, net, http = env
    blob = tgz()
    net.add("GET", API, release(ASSET, blob, url="https://evil.example.com/cloudflared.tgz"))
    with pytest.raises(ProviderError) as err:
        cloudflared.plan_install(settings, http, ASSET)
    assert "official" in err.value.message


def test_the_checksum_the_owner_saw_must_still_be_the_published_one(env):
    settings, net, http = env
    blob = tgz()
    net.add("GET", API, release(ASSET, blob))
    with pytest.raises(Conflict) as err:
        cloudflared.install(
            settings, http, confirm=True, expected_sha256="0" * 64, asset_name=ASSET
        )
    assert err.value.details["reason"] == "checksum_changed"
    assert not net.calls("GET", "releases/download")


def test_an_archive_without_the_binary_is_refused_and_path_tricks_are_ignored(env):
    settings, net, http = env
    blob = tgz(name="../../evil")
    net.add("GET", API, release(ASSET, blob))
    net.add("GET", BASE + ASSET, content=blob)
    with pytest.raises(ProviderError):
        cloudflared.install(settings, http, confirm=True, asset_name=ASSET)
    assert not (settings.resolved_bin_dir.parent / "evil").exists()
    assert list(settings.resolved_bin_dir.iterdir()) == []


def test_plain_binary_assets_are_installed_as_is(env):
    settings, net, http = env
    asset = "cloudflared-linux-amd64"
    net.add("GET", API, release(asset, SCRIPT))
    net.add("GET", BASE + asset, content=SCRIPT)
    path = cloudflared.install(settings, http, confirm=True, asset_name=asset)
    assert path.read_bytes() == SCRIPT and os.access(path, os.X_OK)


def test_the_install_plan_is_metadata_only(env):
    settings, net, http = env
    blob = tgz()
    net.add("GET", API, release(ASSET, blob))
    plan = cloudflared.plan_install(settings, http, ASSET)
    assert plan.sha256 == hashlib.sha256(blob).hexdigest() and plan.url == BASE + ASSET
    assert plan.checksum_sources == ("github_asset_digest", "release_notes")
    assert all(r.url.host == "api.github.com" for r in net.requests)
