from __future__ import annotations

import os
import stat

import pytest

from parishkit.files import atomic_write_text


def test_atomic_write_text_sets_mode(tmp_path):
    path = tmp_path / "credential.json"

    atomic_write_text(path, '{"token": "value"}')

    assert path.read_text(encoding="utf-8") == '{"token": "value"}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_atomic_write_text_preserves_existing_file_on_replace_failure(
    tmp_path, monkeypatch
):
    path = tmp_path / "credential.json"
    path.write_text("old", encoding="utf-8")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".*.tmp")) == []
