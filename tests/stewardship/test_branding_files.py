"""Durable normalized media bundles with explicit receipt and cleanup ownership."""

import io
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from PIL import Image

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.branding_files import (
    BrandingFile,
    create_bundle,
    media_lock,
    read_variant,
    remove_bundle,
)
from parishkit.stewardship.web.content import prepare_graphics


@pytest.fixture
def graphics():
    """Decode and normalize an actual tiny image, not forged Graphic metadata."""
    output = io.BytesIO()
    Image.new("RGB", (150, 80), "blue").save(output, format="PNG")
    output.seek(0)
    return prepare_graphics(output)


def test_bundle_preserves_all_variants_and_never_overwrites(tmp_path, graphics):
    """Only random bundle IDs and fixed normalized PNG names reach durable storage."""
    tmp_path.chmod(0o700)
    identifier = uuid4()
    receipts = create_bundle(tmp_path, identifier, graphics)
    assert {item.label for item in receipts} == {"large", "menu", "icon", "favicon"}
    assert len({item.reference for item in receipts}) == 4
    for item in receipts:
        data = read_variant(tmp_path, identifier, item)
        with Image.open(io.BytesIO(data)) as image:
            assert image.format == "PNG" and image.size == (item.width, item.height)
        assert len(data) == item.size
    with pytest.raises(ConfigError):
        create_bundle(tmp_path, identifier, graphics)
    assert read_variant(tmp_path, identifier, receipts[0])
    assert {
        path.name for path in (tmp_path / "branding" / identifier.hex).iterdir()
    } == {
        "large.png",
        "menu.png",
        "icon.png",
        "favicon.png",
    }


@pytest.mark.parametrize("tamper", ["size", "digest", "mode", "symlink", "hardlink"])
def test_changed_or_linked_file_never_serves(tmp_path, graphics, tamper):
    """A writable media mount cannot silently alter historical branding content."""
    identifier = uuid4()
    item = create_bundle(tmp_path, identifier, graphics)[0]
    target = tmp_path / "branding" / identifier.hex / "large.png"
    if tamper == "size":
        target.write_bytes(b"short")
    elif tamper == "digest":
        target.write_bytes(b"x" * item.size)
    elif tamper == "mode":
        target.chmod(0o644)
    elif tamper == "hardlink":
        os.link(target, tmp_path / "another-link")
    else:
        target.unlink()
        target.symlink_to(tmp_path / "outside")
    with pytest.raises(ConfigError):
        read_variant(tmp_path, identifier, item)


@pytest.mark.parametrize("kind", ["relative", "public", "symlink"])
def test_media_root_must_be_explicit_private_and_not_linked(
    tmp_path, graphics, monkeypatch, kind
):
    """Do not repair or adopt an unsafe deployment root during an upload."""
    root = tmp_path / "media"
    root.mkdir(mode=0o700)
    if kind == "relative":
        monkeypatch.chdir(tmp_path)
        root = "media"
    elif kind == "public":
        root.chmod(0o755)
    else:
        link = tmp_path / "alias"
        link.symlink_to(root, target_is_directory=True)
        root = link
    with pytest.raises(ConfigError):
        create_bundle(root, uuid4(), graphics)


def test_cleanup_is_exact_and_idempotent(tmp_path, graphics):
    """The caller-selected bundle is removed; a neighboring historical bundle stays."""
    removed, retained = uuid4(), uuid4()
    create_bundle(tmp_path, removed, graphics)
    receipt = create_bundle(tmp_path, retained, graphics)[0]
    remove_bundle(tmp_path, removed)
    remove_bundle(tmp_path, removed)
    assert not (tmp_path / "branding" / removed.hex).exists()
    assert read_variant(tmp_path, retained, receipt)


def test_absent_cleanup_does_not_create_storage(tmp_path):
    """Aborting before any filesystem work has no directory-creation side effect."""
    remove_bundle(tmp_path, uuid4())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["extra", "directory", "symlink", "mode", "hardlink"])
def test_unsafe_cleanup_inventory_deletes_nothing(tmp_path, graphics, kind):
    """Validate the entire closed inventory before performing its first unlink."""
    identifier = uuid4()
    create_bundle(tmp_path, identifier, graphics)
    bundle = tmp_path / "branding" / identifier.hex
    if kind == "extra":
        (bundle / "unrelated.txt").write_text("keep")
    elif kind == "directory":
        (bundle / "large.png").unlink()
        (bundle / "large.png").mkdir(mode=0o700)
    elif kind == "symlink":
        (bundle / "large.png").unlink()
        (bundle / "large.png").symlink_to(tmp_path / "outside")
    elif kind == "mode":
        (bundle / "large.png").chmod(0o644)
    else:
        os.link(bundle / "large.png", tmp_path / "another-link")
    before = {item.name for item in bundle.iterdir()}
    with pytest.raises(ConfigError):
        remove_bundle(tmp_path, identifier)
    assert {item.name for item in bundle.iterdir()} == before


def test_fsync_failure_leaves_recoverable_unpublished_bundle(
    tmp_path, graphics, monkeypatch
):
    """An exception is not proof of no files; the owning workflow can clean residue."""
    identifier = uuid4()
    original = os.fsync
    calls = []

    def fail_fourth(descriptor):
        """Interrupt after at least one normalized file has reached the bundle."""
        calls.append(descriptor)
        if len(calls) == 4:
            raise OSError("synthetic failure")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", fail_fourth)
    with pytest.raises(ConfigError):
        create_bundle(tmp_path, identifier, graphics)
    assert (tmp_path / "branding" / identifier.hex).exists()
    monkeypatch.setattr(os, "fsync", original)
    remove_bundle(tmp_path, identifier)
    assert not (tmp_path / "branding" / identifier.hex).exists()


def test_invalid_inventory_or_receipt_fails_before_io(tmp_path, graphics):
    """A submitted path, partial set or forged reference cannot identify an asset."""
    with pytest.raises(ValueError):
        create_bundle(tmp_path, "../../outside", graphics)
    with pytest.raises(ValueError):
        create_bundle(tmp_path, uuid4(), {})
    with pytest.raises(ValueError):
        create_bundle(
            tmp_path,
            uuid4(),
            graphics | {"large": replace(graphics["large"], data=b"not-png")},
        )
    with pytest.raises(ValueError):
        read_variant(
            tmp_path, uuid4(), BrandingFile(uuid4(), "large", 1, 1, 1, "a" * 64)
        )
    assert list(tmp_path.iterdir()) == []


def test_upload_and_cleanup_share_a_nonblocking_store_lock(tmp_path):
    """A competing owner yields rather than hanging an interactive request."""
    with (
        media_lock(tmp_path),
        pytest.raises(ConfigError, match="busy"),
        media_lock(tmp_path),
    ):
        pytest.fail("Another owner must not enter.")
    with media_lock(tmp_path):
        pass


def test_lock_inode_is_never_adopted_if_linked(tmp_path):
    """The fixed lock name cannot follow a filesystem alias to another object."""
    with media_lock(tmp_path):
        pass
    os.link(tmp_path / "branding" / ".lock", tmp_path / "alias")
    with pytest.raises(ConfigError, match="not private"), media_lock(tmp_path):
        pytest.fail("Linked lock must not be adopted.")
