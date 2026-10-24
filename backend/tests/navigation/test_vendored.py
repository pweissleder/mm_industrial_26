"""The vendored official kit files stay byte-identical to kit 1.1.0 (git blob pin)."""

import subprocess

import pytest

from app.navigation import kit
from app.navigation.official import (
    HEADER_END,
    KIT_COMMIT,
    PINNED_BLOBS,
    git_blob_id,
    vendored_body,
)


@pytest.mark.parametrize("filename", sorted(PINNED_BLOBS))
def test_vendored_body_matches_pinned_blob(filename: str) -> None:
    path, blob = PINNED_BLOBS[filename]
    body = vendored_body(filename)
    assert body and HEADER_END not in body
    assert git_blob_id(body) == blob, f"{filename} differs from {path} at kit commit {KIT_COMMIT}"


def test_git_blob_id_matches_git_hash_object() -> None:
    assert git_blob_id(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert git_blob_id(b"hi\n") == "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"
    assert git_blob_id(b"a\r\nb\n") == git_blob_id(b"a\nb\n")
    assert git_blob_id(b"a\r\n", normalise_crlf=False) != git_blob_id(b"a\n")


@pytest.mark.kit
def test_pins_agree_with_the_kit_commit_tree() -> None:
    tree = kit.pinned_tree(str(kit.reference_dir()))
    for path, blob in PINNED_BLOBS.values():
        assert tree[path] == blob
    head = subprocess.run(
        ["git", "-C", str(kit.reference_dir()), "rev-parse", f"{KIT_COMMIT}^{{commit}}"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    assert head == KIT_COMMIT
