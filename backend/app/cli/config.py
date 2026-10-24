"""CLI configuration and credentials.

``~/.config/companyos/config.toml`` (``api_url``, ``company_id``; no secrets) and
``~/.config/companyos/token`` (a personal API token, mode 0600). On the server machine the owner token
(``<data dir>/owner.token``) is the fallback credential. Secrets are never accepted on the command line;
``COMPANYOS_TOKEN`` (environment) exists for CI.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8700


def config_dir() -> Path:
    explicit = os.environ.get("COMPANYOS_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "companyos"


def config_path() -> Path:
    return config_dir() / "config.toml"


def token_path() -> Path:
    return config_dir() / "token"


def load_config() -> dict[str, str]:
    path = config_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(**updates: str | None) -> None:
    current = load_config()
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k} = {_quote(v)}\n" for k, v in sorted(current.items()))
    _write_private(config_path(), body)


def _write_private(path: Path, text: str) -> None:
    """Create/replace ``path`` readable by this user only (no window where it is world-readable)."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_token(token: str) -> Path:
    config_dir().mkdir(parents=True, exist_ok=True)
    _write_private(token_path(), token + "\n")
    return token_path()


def delete_token() -> bool:
    try:
        token_path().unlink()
        return True
    except FileNotFoundError:
        return False


def read_token_file(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return value or None


def mode_of(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


@dataclass(frozen=True)
class Credential:
    token: str
    source: str  # env | token-file | owner-token


def owner_token_path() -> Path | None:
    """<data dir>/owner.token as the server process would resolve it (same env vars)."""
    try:
        from app.core.config import Settings
        from app.core.owner_token import OWNER_TOKEN_FILENAME

        return Settings().resolved_data_dir / OWNER_TOKEN_FILENAME
    except Exception:
        return None


def resolve_credential() -> Credential | None:
    env = os.environ.get("COMPANYOS_TOKEN", "").strip()
    if env:
        return Credential(env, "env")
    saved = read_token_file(token_path())
    if saved:
        return Credential(saved, "token-file")
    owner = owner_token_path()
    if owner is not None:
        value = read_token_file(owner)
        if value:
            return Credential(value, "owner-token")
    return None


def resolve_api_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("COMPANYOS_API_URL") or load_config().get("api_url")
    if not url:
        port = (
            os.environ.get("COMPANY_OS_PORT")
            or os.environ.get("FOUNDRYOS_PORT")
            or str(DEFAULT_PORT)
        )
        url = f"http://127.0.0.1:{port}"
    return url.rstrip("/")
