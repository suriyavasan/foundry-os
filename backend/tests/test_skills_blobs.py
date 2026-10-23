"""Skill normalization, canonical hashing and the content-addressed blob store."""

from __future__ import annotations

import os
import stat

import pytest

from app.skills.blobstore import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    BlobStore,
    SkillFile,
    SkillRejected,
    hash_dir,
    hash_files,
    normalize_dir,
    normalize_files,
)
from tests.helpers import write_skill


def test_hash_is_canonical_independent_of_order_and_sensitive_to_content():
    a = hash_files([("SKILL.md", b"x"), ("scripts/run.sh", b"y")])
    assert a == hash_files([("scripts/run.sh", b"y"), ("SKILL.md", b"x")])
    assert a != hash_files([("SKILL.md", b"x"), ("scripts/run.sh", b"z")])
    assert a != hash_files([("SKILL.md", b"x"), ("scripts/other.sh", b"y")])
    # path/content boundaries cannot be shifted to forge a collision
    assert hash_files([("a", b"bc")]) != hash_files([("ab", b"c")])


def test_blobs_dedupe_by_sha256(tmp_path):
    first = write_skill(tmp_path / "src1", "alpha", files={"references/a.md": "A"})
    second = write_skill(tmp_path / "src2", "alpha", files={"references/a.md": "A"})
    store = BlobStore(tmp_path / "blobs")
    n1, n2 = normalize_dir(first), normalize_dir(second)
    assert n1.sha256 == n2.sha256  # same content, different location -> same blob
    info1, info2 = store.put(n1), store.put(n2)
    assert info1 == info2 and info1.file_count == 2
    assert [p.name for p in (tmp_path / "blobs").iterdir()] == [n1.sha256]
    assert store.exists(n1.sha256) and store.verify(n1.sha256)
    assert hash_dir(store.path(n1.sha256)) == n1.sha256


def test_put_repairs_a_corrupt_blob(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    n = normalize_dir(write_skill(tmp_path / "src", "alpha"))
    store.put(n)
    (store.path(n.sha256) / "SKILL.md").write_text("tampered")
    assert not store.verify(n.sha256)
    store.put(n)
    assert store.verify(n.sha256)


def test_symlinks_are_rejected(tmp_path):
    skill = write_skill(tmp_path, "alpha")
    (skill / "link.md").symlink_to(skill / "SKILL.md")
    with pytest.raises(SkillRejected, match="symlink"):
        normalize_dir(skill)
    other = write_skill(tmp_path, "beta")
    (other / "dirlink").symlink_to(tmp_path)
    with pytest.raises(SkillRejected, match="symlink"):
        normalize_dir(other)


def test_size_and_file_count_limits():
    big = [SkillFile("SKILL.md", b"x"), SkillFile("big.bin", b"0" * (MAX_TOTAL_BYTES))]
    with pytest.raises(SkillRejected, match="MB"):
        normalize_files(big)
    many = [SkillFile("SKILL.md", b"x")] + [SkillFile(f"f{i}.md", b"x") for i in range(MAX_FILES)]
    with pytest.raises(SkillRejected, match="files"):
        normalize_files(many)
    ok = [SkillFile("SKILL.md", b"x")] + [SkillFile(f"f{i}.md", b"x") for i in range(MAX_FILES - 1)]
    assert normalize_files(ok).file_count == MAX_FILES


def test_directory_walk_enforces_limits_before_reading(tmp_path):
    skill = write_skill(tmp_path, "alpha")
    (skill / "huge.txt").write_bytes(b"0" * (MAX_TOTAL_BYTES + 1))
    with pytest.raises(SkillRejected):
        normalize_dir(skill)


def test_executable_bit_only_survives_under_scripts(tmp_path):
    skill = write_skill(tmp_path, "alpha", files={"scripts/run.sh": "echo hi\n", "notes.md": "n"})
    (skill / "scripts" / "run.sh").chmod(0o755)
    (skill / "notes.md").chmod(0o755)
    (skill / "SKILL.md").chmod(0o755)  # upstream ships some SKILL.md files executable
    normalized = normalize_dir(skill)
    modes = {f.path: f.executable for f in normalized.files}
    assert modes == {"SKILL.md": False, "notes.md": False, "scripts/run.sh": True}
    store = BlobStore(tmp_path / "blobs")
    store.put(normalized)
    root = store.path(normalized.sha256)
    assert not (root / "notes.md").stat().st_mode & stat.S_IXUSR
    assert (root / "scripts" / "run.sh").stat().st_mode & stat.S_IXUSR
    # the executable bit is not part of the hash: only a normalized form is stored
    assert normalized.sha256 == hash_files([(f.path, f.data) for f in normalized.files])


@pytest.mark.parametrize(
    "path",
    ["../escape.md", "/abs.md", "a/../../b.md", "a\\b.md", ".env", "cfg/.env.local", "x/./y.md"],
)
def test_unsafe_paths_are_rejected(path):
    with pytest.raises(SkillRejected):
        normalize_files([SkillFile("SKILL.md", b"x"), SkillFile(path, b"x")])


def test_native_binaries_are_rejected():
    for magic in (b"\x7fELF....", b"MZ\x90\x00", b"\xcf\xfa\xed\xfe..", b"\xca\xfe\xba\xbe.."):
        with pytest.raises(SkillRejected, match="native executable"):
            normalize_files([SkillFile("SKILL.md", b"x"), SkillFile("scripts/tool", magic)])


def test_skill_md_is_required_and_noise_is_dropped(tmp_path):
    with pytest.raises(SkillRejected, match="SKILL.md"):
        normalize_files([SkillFile("README.md", b"x")])
    skill = write_skill(
        tmp_path,
        "alpha",
        files={".git/config": "x", ".DS_Store": "x", ".skillignore": "*", "__pycache__/a.pyc": "x"},
    )
    assert [f.path for f in normalize_dir(skill).files] == ["SKILL.md"]


def test_preview_cannot_leave_the_blob(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    n = normalize_dir(write_skill(tmp_path / "src", "alpha", files={"references/a.md": "hello"}))
    store.put(n)
    assert store.read_text(n.sha256, "references/a.md") == ("hello", False)
    assert store.read_text(n.sha256, "references/a.md", max_bytes=2) == ("he", True)
    with pytest.raises(SkillRejected):
        store.read_text(n.sha256, "../../etc/passwd")
    with pytest.raises(FileNotFoundError):
        store.read_text(n.sha256, "missing.md")
    with pytest.raises(SkillRejected):
        store.path("../../x")
    assert os.path.isdir(store.path(n.sha256))
