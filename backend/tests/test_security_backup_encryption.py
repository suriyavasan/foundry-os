"""Encrypted backups (docs/security.md G8): round trip, wrong passphrase, tampering of every kind,
streaming of large files, the CLI (passphrase never in argv), and 0600 on every backup file."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tracemalloc
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.core.config import Settings
from app.ops import backup_crypto
from app.ops.backup import (
    OpsError,
    create_backup,
    is_encrypted,
    read_manifest,
    restore_backup,
    verify_archive,
)
from app.ops.backup_crypto import CryptoFailure, DecryptReader, EncryptWriter
from tests.test_m9_backup import FILES, make_install, marker_of, target_settings

PASS = "correct horse battery staple"
FAST = {"kdf_log2_n": 10}  # the header carries the cost, so restore is fast too


@pytest.fixture
def source(tmp_path: Path, migrated_template: Path) -> Settings:
    return make_install(tmp_path / "src", migrated_template)


@pytest.fixture
def enc(source: Settings, tmp_path: Path) -> Path:
    return create_backup(source, tmp_path / "out" / "b.tar.gz", passphrase=PASS, **FAST).path


def refused(archive: Path, tmp_path: Path, passphrase: str | None = PASS) -> OpsError:
    """Restore must fail and leave nothing behind."""
    target = target_settings(tmp_path / "t")
    with pytest.raises(OpsError) as info:
        restore_backup(archive, target, passphrase=passphrase)
    assert not target.resolved_data_dir.exists(), "nothing may be written on a refused restore"
    assert not (tmp_path / "t").exists() or not any((tmp_path / "t").iterdir())
    return info.value


# ------------------------------------------------------------------ round trip


def test_encrypted_round_trip(source, enc, tmp_path):
    assert enc.name == "b.tar.gz.enc"  # the name says what is inside
    raw = enc.read_bytes()
    assert raw.startswith(backup_crypto.MAGIC) and is_encrypted(enc)
    # nothing readable leaks: not a gzip header, not file contents, not file names
    assert b"\x1f\x8b" not in raw[:64] and FILES["secret.key"] not in raw
    assert b"secret.key" not in raw and b"manifest" not in raw and b"SQLite format" not in raw
    result = create_backup(source, tmp_path / "again.tar.gz.enc", passphrase=PASS, **FAST)
    assert result.encrypted is True and result.files == 1 + len(FILES)
    assert enc.read_bytes() != result.path.read_bytes()  # fresh salt and nonce every time

    assert verify_archive(enc, passphrase=PASS)["files"]
    assert read_manifest(enc, PASS)["format"] == 1
    target = target_settings(tmp_path)
    outcome = restore_backup(enc, target, passphrase=PASS)
    assert outcome.files == 1 + len(FILES) and marker_of(target) == "alpha"
    data = target.resolved_data_dir
    for rel, body in FILES.items():
        assert (data / rel).read_bytes() == body
    assert stat.S_IMODE((data / "secret.key").stat().st_mode) == 0o600


def test_plain_backups_are_not_mistaken_for_encrypted_ones(source, tmp_path):
    plain = create_backup(source, tmp_path / "p.tar.gz")
    assert plain.encrypted is False and not is_encrypted(plain.path)
    assert plain.path.read_bytes()[:2] == b"\x1f\x8b"
    # a passphrase offered for a plain archive is simply unused
    assert verify_archive(plain.path, passphrase="whatever1")["files"]


def test_default_name_and_explicit_names(source, tmp_path):
    default = create_backup(source, passphrase=PASS, **FAST).path
    assert default.parent.name == "backups" and default.name.endswith(".tar.gz.enc")
    (tmp_path / "dir").mkdir()
    into_dir = create_backup(source, tmp_path / "dir", passphrase=PASS, **FAST).path
    assert into_dir.name.endswith(".tar.gz.enc")
    custom = create_backup(source, tmp_path / "mine.bak", passphrase=PASS, **FAST).path
    assert custom.name == "mine.bak"  # an explicit, unusual name is respected
    assert is_encrypted(custom)


def test_short_passphrases_are_refused_before_anything_is_written(source, tmp_path):
    out = tmp_path / "x.tar.gz.enc"
    with pytest.raises(OpsError) as info:
        create_backup(source, out, passphrase="short")
    assert info.value.code == "weak_passphrase" and not out.exists()
    assert not list(tmp_path.glob("*.partial"))


def test_unicode_passphrases_are_normalised(source, tmp_path):
    composed, decomposed = "pässwörd-é-1", "pässwörd-é-1"
    archive = create_backup(source, tmp_path / "u.tar.gz", passphrase=composed, **FAST).path
    assert verify_archive(archive, passphrase=decomposed)["files"]


# ------------------------------------------------------------------ wrong passphrase


def test_wrong_passphrase_is_a_clear_refusal(enc, tmp_path):
    err = refused(enc, tmp_path, "this is not the passphrase")
    assert err.code == "wrong_passphrase" and err.exit_code == 3
    assert "Wrong passphrase" in err.message and "Nothing was restored" in err.message
    for fn in (verify_archive, read_manifest):
        with pytest.raises(OpsError) as info:
            fn(enc, passphrase="nope nope nope")
        assert info.value.code == "wrong_passphrase"


def test_missing_passphrase_is_asked_for_not_guessed(enc, tmp_path):
    err = refused(enc, tmp_path, None)
    assert err.code == "passphrase_required" and "encrypted" in err.message
    with pytest.raises(OpsError) as info:
        verify_archive(enc)
    assert info.value.code == "passphrase_required"


# ------------------------------------------------------------------ tampering


def mutate(src: Path, dest: Path, fn) -> Path:
    data = bytearray(src.read_bytes())
    dest.write_bytes(bytes(fn(data)))
    return dest


def flip(index: int):
    def go(data: bytearray) -> bytearray:
        data[index % len(data)] ^= 0x01
        return data

    return go


HEADER = backup_crypto.HEADER_LEN


@pytest.mark.parametrize(
    "where",
    [
        8,  # version
        9,  # kdf id
        10,  # scrypt N
        11,  # scrypt r
        12,  # scrypt p
        13,  # cipher id
        16,  # chunk size
        20,  # salt
        36,  # nonce base
        45,  # key check
        HEADER,  # first ciphertext byte
        HEADER + 40,
        -40,  # inside the last chunk
        -1,  # the authentication tag
    ],
)
def test_flipping_any_single_byte_is_refused_and_writes_nothing(enc, tmp_path, where):
    bad = mutate(enc, tmp_path / "bad.enc", flip(where))
    err = refused(bad, tmp_path)
    assert err.code in {"tampered", "wrong_passphrase", "unsupported", "bad_format"}
    assert err.exit_code != 0


def test_truncation_appending_and_a_damaged_magic_are_refused(enc, tmp_path):
    cases = {
        "cut the tail": lambda d: d[:-10],
        "cut the whole final chunk": lambda d: d[:HEADER],
        "header only, cut in the middle of it": lambda d: d[: HEADER - 5],
        "appended junk": lambda d: d + b"\x00" * 7,
        "appended a whole fake chunk": lambda d: d + b"\x00" * (backup_crypto.TAG_LEN + 5),
        "empty file": lambda d: d[:0],
    }
    for name, fn in cases.items():
        bad = mutate(enc, tmp_path / "bad.enc", lambda d, fn=fn: bytearray(fn(d)))
        err = refused(bad, tmp_path)
        assert err.exit_code != 0, name
    # a damaged magic is no longer recognised as encrypted: it is just not a valid backup
    bad = mutate(enc, tmp_path / "magic.enc", flip(0))
    assert not is_encrypted(bad)
    with pytest.raises(OpsError):
        restore_backup(bad, target_settings(tmp_path / "m"), passphrase=PASS)


def test_an_edited_archive_inside_the_encryption_is_still_caught(source, tmp_path):
    """Someone who knows the passphrase can re-encrypt altered content; the manifest checksums
    still catch a file that does not match (defence in depth, same as plain archives)."""
    import tarfile

    plain = create_backup(source, tmp_path / "p.tar.gz")
    members: list[tuple[str, bytes]] = []
    with tarfile.open(plain.path, "r:gz") as tar:
        for m in tar:
            body = tar.extractfile(m).read()  # type: ignore[union-attr]
            if m.name.endswith("owner.token"):
                body += b"x"
            members.append((m.name, body))
    forged = tmp_path / "forged.tar.gz.enc"
    with forged.open("wb") as raw:
        sink = EncryptWriter(raw, PASS, log2_n=10)
        with tarfile.open(fileobj=sink, mode="w:gz") as tar:
            for name, body in members:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
        sink.finish()
    err = refused(forged, tmp_path)
    assert err.code == "checksum_mismatch"


# ------------------------------------------------------------------ the crypto layer


def seal(data: bytes, chunk: int = 1024, passphrase: str = PASS) -> bytes:
    out = io.BytesIO()
    w = EncryptWriter(out, passphrase, log2_n=10, chunk_size=chunk)
    for i in range(0, len(data), 777):  # awkward write sizes
        w.write(data[i : i + 777])
    w.finish()
    return out.getvalue()


def unseal(blob: bytes, passphrase: str = PASS) -> bytes:
    reader = DecryptReader(io.BytesIO(blob), passphrase)
    out = bytearray()
    while chunk := reader.read(500):
        out += chunk
    reader.drain()
    return bytes(out)


@pytest.mark.parametrize("size", [0, 1, 1023, 1024, 1025, 2048, 5 * 1024 + 7])
def test_chunk_boundaries_round_trip(size):
    data = os.urandom(size)
    assert unseal(seal(data)) == data


def block_ranges(blob: bytes, chunk: int = 1024) -> list[tuple[int, int]]:
    body = len(blob) - HEADER
    size = chunk + backup_crypto.TAG_LEN
    starts = list(range(HEADER, HEADER + body, size))
    return [(s, min(s + size, len(blob))) for s in starts]


def test_reordering_duplicating_and_dropping_chunks_fails_authentication():
    blob = seal(os.urandom(5 * 1024 + 100))
    blocks = [blob[a:b] for a, b in block_ranges(blob)]
    assert len(blocks) == 6
    head = blob[:HEADER]
    variants = {
        "swap two": [blocks[0], blocks[2], blocks[1], *blocks[3:]],
        "duplicate": [blocks[0], blocks[1], blocks[1], *blocks[2:]],
        "drop one": [blocks[0], *blocks[2:]],
        "drop last (truncate at a boundary)": blocks[:-1],
        "repeat last": [*blocks, blocks[-1]],
    }
    for name, parts in variants.items():
        with pytest.raises(CryptoFailure) as info:
            unseal(head + b"".join(parts))
        assert info.value.code == "tampered", name


def test_a_header_from_one_file_with_the_chunks_of_another_is_refused():
    a, b = seal(os.urandom(3000)), seal(os.urandom(3000))
    with pytest.raises(CryptoFailure):
        unseal(a[:HEADER] + b[HEADER:])


def test_hostile_kdf_parameters_are_rejected_before_any_work_is_done():
    import struct

    fixed = struct.pack(
        ">8sBBBBBBI16s8s",
        backup_crypto.MAGIC,
        1,
        1,
        30,  # scrypt N = 2^30: would need hundreds of GiB
        8,
        1,
        1,
        1024,
        b"s" * 16,
        b"n" * 8,
    )
    with pytest.raises(CryptoFailure) as info:
        DecryptReader(io.BytesIO(fixed + b"\x00" * backup_crypto.CHECK_LEN), PASS)
    assert info.value.code == "tampered"


def test_future_format_versions_are_reported_not_misread():
    blob = bytearray(seal(b"hello"))
    blob[8] = 99
    with pytest.raises(CryptoFailure) as info:
        DecryptReader(io.BytesIO(bytes(blob)), PASS)
    assert info.value.code == "unsupported"


# ------------------------------------------------------------------ streaming / large files


def test_encrypting_and_decrypting_a_large_stream_uses_constant_memory(tmp_path):
    total = 96 * 1024 * 1024
    block = os.urandom(1024 * 1024)
    path = tmp_path / "big.enc"
    sha = hashlib.sha256()
    tracemalloc.start()
    try:
        with path.open("wb") as fh:
            w = EncryptWriter(fh, PASS, log2_n=10)
            for _ in range(total // len(block)):
                w.write(block)
                sha.update(block)
            w.finish()
        _, enc_peak = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        got = hashlib.sha256()
        size = 0
        with path.open("rb") as fh:
            reader = DecryptReader(fh, PASS)
            while chunk := reader.read(256 * 1024):
                got.update(chunk)
                size += len(chunk)
            reader.drain()
        _, dec_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert size == total and got.digest() == sha.digest()
    overhead = (total // backup_crypto.DEFAULT_CHUNK) * backup_crypto.TAG_LEN  # 96 full chunks
    assert path.stat().st_size == backup_crypto.HEADER_LEN + total + overhead
    # a handful of chunks, never the 96 MiB stream
    assert enc_peak < 12 * 1024 * 1024 and dec_peak < 12 * 1024 * 1024


def test_a_large_incompressible_file_survives_a_full_encrypted_backup(source, tmp_path):
    big = source.resolved_data_dir / "artifacts" / "c1" / "big.bin"
    digest = hashlib.sha256()
    with big.open("wb") as fh:
        for _ in range(24):
            piece = os.urandom(1024 * 1024)
            fh.write(piece)
            digest.update(piece)
    archive = create_backup(source, tmp_path / "big.tar.gz", passphrase=PASS, **FAST).path
    assert archive.stat().st_size > 24 * 1024 * 1024  # random data does not compress
    target = target_settings(tmp_path)
    restore_backup(archive, target, passphrase=PASS)
    restored = target.resolved_data_dir / "artifacts" / "c1" / "big.bin"
    assert hashlib.sha256(restored.read_bytes()).digest() == digest.digest()
    # flipping one byte deep inside the big payload is caught too, and nothing is written
    bad = mutate(archive, tmp_path / "bad.enc", flip(archive.stat().st_size // 2))
    refused(bad, tmp_path / "again")


# ------------------------------------------------------------------ file permissions


@pytest.mark.parametrize("encrypted", [False, True])
def test_every_backup_file_is_created_0600(source, tmp_path, encrypted):
    old = os.umask(0)  # the most permissive umask: the mode must not depend on it
    try:
        out = tmp_path / "perm" / ("b.tar.gz")
        result = create_backup(
            source, out, passphrase=PASS if encrypted else None, **(FAST if encrypted else {})
        )
        assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
        default = create_backup(
            source, passphrase=PASS if encrypted else None, **(FAST if encrypted else {})
        )
        assert stat.S_IMODE(default.path.stat().st_mode) == 0o600
        assert not list(result.path.parent.glob("*.partial"))
    finally:
        os.umask(old)


def test_the_automatic_pre_upgrade_backup_is_plain_and_0600(tmp_path, migrated_template):
    """It runs unattended (nobody to ask for a passphrase) and lives next to secret.key anyway."""
    from datetime import UTC, datetime

    from app.ops import upgrade
    from app.ops.backup import _head_revision

    source = make_install(tmp_path / "up", migrated_template)
    upgrade.write_marker(source, "0.1.0", _head_revision())
    said: list[str] = []
    ok = upgrade.upgrade_database(
        source,
        said.append,
        lambda s: None,
        version="0.2.0",
        now=datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC),
    )
    assert ok
    (pre,) = (source.resolved_data_dir / "backups").glob("pre-0.2.0-*")
    assert pre.name.endswith(".tar.gz") and not is_encrypted(pre)
    assert stat.S_IMODE(pre.stat().st_mode) == 0o600
    assert verify_archive(pre)["files"]


# ------------------------------------------------------------------ the CLI


def run(args, input=None, env=None):
    return CliRunner().invoke(cli_app, args, input=input, env=env)


def test_cli_encrypts_with_a_passphrase_from_stdin_and_restores_it(source, tmp_path, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    data = str(source.resolved_data_dir)
    out = tmp_path / "cli.tar.gz"
    r = run(
        [
            "backup",
            "create",
            "--data-dir",
            data,
            "--out",
            str(out),
            "--encrypt",
            "--passphrase-stdin",
            "--json",
        ],
        input=PASS + "\n",
    )
    assert r.exit_code == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["encrypted"] is True and body["path"].endswith(".tar.gz.enc")
    assert PASS not in r.stdout and PASS not in r.stderr
    archive = Path(body["path"])
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600 and is_encrypted(archive)

    v = run(
        ["backup", "restore", str(archive), "--verify-only", "--passphrase-stdin", "--json"],
        input=PASS + "\n",
    )
    assert v.exit_code == 0 and json.loads(v.stdout)["verified"] is True

    dst = tmp_path / "dst" / "data"
    ok = run(
        ["backup", "restore", str(archive), "--data-dir", str(dst), "--passphrase-stdin", "--json"],
        input=PASS + "\n",
    )
    assert ok.exit_code == 0, ok.stderr
    assert (dst / "secret.key").read_bytes() == FILES["secret.key"]


def test_cli_prompts_for_the_passphrase_twice_when_creating_and_hides_it(source, tmp_path):
    data = str(source.resolved_data_dir)
    out = tmp_path / "p.tar.gz"
    mismatch = run(
        ["backup", "create", "--data-dir", data, "--out", str(out), "--encrypt"],
        input=f"{PASS}\nsomething else entirely\n" * 3,
    )
    assert mismatch.exit_code != 0 and not list(tmp_path.glob("p.tar.gz*"))
    r = run(
        ["backup", "create", "--data-dir", data, "--out", str(out), "--encrypt"],
        input=f"{PASS}\n{PASS}\n",
    )
    assert r.exit_code == 0, r.stderr
    assert "Encrypted" in r.stdout and "NOT encrypted" not in r.stdout
    assert PASS not in r.stdout  # not echoed
    prompted = run(
        ["backup", "restore", str(tmp_path / "p.tar.gz.enc"), "--verify-only"], input=PASS + "\n"
    )
    assert prompted.exit_code == 0, prompted.stderr + prompted.stdout


def test_cli_wrong_passphrase_and_tampering_are_clear_and_write_nothing(source, tmp_path):
    data = str(source.resolved_data_dir)
    out = tmp_path / "w.tar.gz"
    run(
        [
            "backup",
            "create",
            "--data-dir",
            data,
            "--out",
            str(out),
            "--encrypt",
            "--passphrase-stdin",
        ],
        input=PASS + "\n",
    )
    archive = tmp_path / "w.tar.gz.enc"
    dst = tmp_path / "dst" / "data"
    wrong = run(
        ["backup", "restore", str(archive), "--data-dir", str(dst), "--passphrase-stdin"],
        input="not the passphrase\n",
    )
    assert wrong.exit_code == 3 and "Wrong passphrase" in wrong.stderr
    bad = mutate(archive, tmp_path / "bad.enc", flip(-5))
    tampered = run(
        ["backup", "restore", str(bad), "--data-dir", str(dst), "--passphrase-stdin"],
        input=PASS + "\n",
    )
    assert tampered.exit_code == 1 and "failed authentication" in tampered.stderr
    assert not dst.exists() and not (tmp_path / "dst").exists()


def test_cli_passphrase_is_never_an_argument(source, tmp_path):
    data = str(source.resolved_data_dir)
    for args in (
        ["backup", "create", "--data-dir", data, "--encrypt", "--passphrase", "secret-pass-1"],
        ["backup", "create", "--data-dir", data, "--encrypt", "--passphrase=secret-pass-1"],
        ["backup", "restore", str(tmp_path / "x"), "--passphrase", "secret-pass-1"],
    ):
        r = run(args)
        assert r.exit_code == 2 and "No such option" in r.stderr
    # the options that exist take no value
    r = run(["backup", "create", "--data-dir", data, "--passphrase-stdin"], input=PASS + "\n")
    assert r.exit_code == 2 and "--encrypt" in r.stderr
    r = run(
        [
            "backup",
            "create",
            "--data-dir",
            data,
            "--out",
            str(tmp_path / "s"),
            "--encrypt",
            "--passphrase-stdin",
        ],
        input="short\n",
    )
    assert r.exit_code == 2 and "at least 8" in r.stderr


def test_cli_unencrypted_backups_warn_that_they_hold_secrets(source, tmp_path, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    r = run(
        [
            "backup",
            "create",
            "--data-dir",
            str(source.resolved_data_dir),
            "--out",
            str(tmp_path / "plain.tar.gz"),
        ]
    )
    assert r.exit_code == 0
    assert "NOT encrypted" in r.stdout and "secret key" in r.stdout and "--encrypt" in r.stdout
    out = tmp_path / "plain.tar.gz"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600 and not is_encrypted(out)
