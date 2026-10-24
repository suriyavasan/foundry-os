"""M11: data directory per OS, FOUNDRYOS_* settings, install-kind detection, version, `--version`."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import install
from app.core.config import Settings
from tests.m11_helpers import Env, as_json, env, installer_layout, invoke, write_bundle  # noqa: F401

# ------------------------------------------------------------------ data directory


def test_platform_data_dir_on_macos_and_linux(tmp_path: Path):
    home = {"HOME": str(tmp_path)}
    assert (
        install.platform_data_dir("darwin", home)
        == tmp_path / "Library" / "Application Support" / "Foundry AI OS"
    )
    assert install.platform_data_dir("linux", home) == tmp_path / ".local" / "share" / "foundry-os"
    xdg = {"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg")}
    assert install.platform_data_dir("linux", xdg) == tmp_path / "xdg" / "foundry-os"
    relative = {"HOME": str(tmp_path), "XDG_DATA_HOME": "relative/dir"}  # not absolute: ignored
    assert (
        install.platform_data_dir("linux", relative) == tmp_path / ".local" / "share" / "foundry-os"
    )


@pytest.mark.parametrize(
    ("system", "tail"),
    [
        ("darwin", ("Library", "Application Support", "Foundry AI OS")),
        ("linux", (".local", "share", "foundry-os")),
    ],
)
def test_installed_mode_defaults_to_the_platform_dir(env, monkeypatch, system, tail):
    monkeypatch.delenv("FOUNDRYOS_DATA_DIR")
    monkeypatch.setattr(install, "installed_mode", lambda: True)
    monkeypatch.setattr(install, "current_os", lambda: system)
    monkeypatch.setattr("app.core.config.installed_mode", lambda: True)
    monkeypatch.setattr(
        "app.core.config.platform_data_dir", lambda: install.platform_data_dir(system)
    )
    s = Settings(_env_file=None)
    assert s.resolved_data_dir == env.home.joinpath(*tail)
    assert s.resolved_database_url == f"sqlite:///{env.home.joinpath(*tail)}/company_os.db"
    assert s.logs_dir == env.home.joinpath(*tail) / "logs"


def test_a_source_checkout_keeps_repo_data(env, monkeypatch):
    monkeypatch.delenv("FOUNDRYOS_DATA_DIR")
    s = Settings(_env_file=None)
    assert s.resolved_data_dir == install.REPO_DIR / "data"
    assert s.resolved_database_url.endswith("/data/company_os.db")


def test_foundryos_data_dir_moves_data_and_database(env):
    s = Settings(_env_file=None)
    assert s.resolved_data_dir == env.data
    assert s.resolved_database_url == f"sqlite:///{env.data}/company_os.db"


def test_company_os_variables_win_over_the_aliases(env, monkeypatch):
    monkeypatch.setenv("COMPANY_OS_DATA_DIR", str(env.home / "explicit"))
    monkeypatch.setenv("COMPANY_OS_PORT", "8801")
    monkeypatch.setenv("FOUNDRYOS_PORT", "8802")
    s = Settings(_env_file=None)
    assert s.resolved_data_dir == env.home / "explicit"
    assert s.port == 8801


def test_foundryos_port_and_update_check(env, monkeypatch):
    monkeypatch.delenv("FOUNDRYOS_PORT")
    assert Settings(_env_file=None).port == 8700
    monkeypatch.setenv("FOUNDRYOS_PORT", "8713")
    monkeypatch.setenv("FOUNDRYOS_UPDATE_CHECK", "0")
    s = Settings(_env_file=None)
    assert s.port == 8713 and s.update_check is False


def test_explicit_constructor_arguments_are_untouched(env, tmp_path):
    s = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/x.db", data_dir=tmp_path / "d")
    assert s.resolved_data_dir == tmp_path / "d"
    only_db = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/y.db")
    # the alias must not override an explicitly given database's neighbourhood
    assert only_db.resolved_database_url == f"sqlite:///{tmp_path}/y.db"


def test_installed_flag(monkeypatch):
    monkeypatch.setenv("FOUNDRYOS_INSTALLED", "1")
    assert install.installed_mode() is True
    monkeypatch.setenv("FOUNDRYOS_INSTALLED", "0")
    assert install.installed_mode() is False
    monkeypatch.delenv("FOUNDRYOS_INSTALLED")
    assert install.installed_mode() is (not install.source_checkout())


def test_installed_mode_when_there_is_no_git_checkout(monkeypatch):
    monkeypatch.delenv("FOUNDRYOS_INSTALLED", raising=False)
    monkeypatch.setattr(install, "source_checkout", lambda: False)
    assert install.installed_mode() is True


# ------------------------------------------------------------------ install kinds


def test_source_checkout_kind():
    assert install.install_kind() == install.SOURCE
    assert install.installer_prefix() is None
    assert install.launcher_command()[0] == install.sys.executable


def test_installer_kind_and_stable_launcher(env, monkeypatch):
    root = installer_layout(env, monkeypatch)
    assert install.install_kind() == install.INSTALLER
    assert install.installer_prefix() == env.prefix
    assert install.launcher_command() == [str(env.prefix / "current" / "bin" / "foundryos")]
    assert root.name == "0.1.0"


def test_homebrew_kind_is_detected_from_the_cellar(env, monkeypatch, tmp_path):
    cellar = tmp_path / "brew" / "Cellar" / "foundry-os" / "0.1.0" / "libexec"
    write_bundle(cellar, "0.1.0")
    monkeypatch.setattr(install, "bundle_root", lambda: cellar)
    assert install.install_kind() == install.HOMEBREW
    assert install.homebrew_prefix() == tmp_path / "brew"
    assert install.launcher_command() == [
        str(tmp_path / "brew" / "opt" / "foundry-os" / "bin" / "foundryos")
    ]
    assert install.installer_prefix() is None


def test_homebrew_by_environment(env, monkeypatch, tmp_path):
    cellar = tmp_path / "custom-cellar"
    root = write_bundle(cellar / "foundry-os" / "0.1.0", "0.1.0")
    monkeypatch.setenv("HOMEBREW_CELLAR", str(cellar))
    monkeypatch.setattr(install, "bundle_root", lambda: root)
    assert install.install_kind() == install.HOMEBREW


def test_unmanaged_bundle_and_docker(env, monkeypatch, tmp_path):
    root = write_bundle(tmp_path / "somewhere" / "foundry-os", "0.1.0")
    monkeypatch.setattr(install, "bundle_root", lambda: root)
    assert install.install_kind() == install.BUNDLE
    monkeypatch.setattr(install, "in_container", lambda: True)
    assert install.install_kind() == install.DOCKER


def test_platform_tag_prefers_the_bundle_build_info(env, monkeypatch):
    installer_layout(env, monkeypatch)
    assert install.platform_tag() == "darwin-arm64"


# ------------------------------------------------------------------ version


def test_version_comes_from_the_version_file():
    assert install.read_version() == (install.REPO_DIR / "VERSION").read_text().strip()


def test_version_flag_and_command(env):
    r = invoke("--version")
    assert r.exit_code == 0 and r.stdout.strip() == f"foundryos {install.APP_VERSION}"
    r = invoke("version")
    assert r.stdout.strip() == f"foundryos {install.APP_VERSION}"
    info = as_json(invoke("version", "--json"))
    assert info["version"] == install.APP_VERSION and info["install_kind"] == "source"
    assert info["data_dir"] == str(env.data)


def test_version_falls_back_to_package_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "REPO_DIR", tmp_path)
    monkeypatch.setattr(install, "bundle_root", lambda: None)
    assert install.read_version() == "0.1.0"  # backend/pyproject.toml's metadata
