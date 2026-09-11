"""Public asset collection never adopts a nonempty target or initializes real SQL."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from parishkit.stewardship.cli import main


def collect_in_fresh_process(path):
    """Do not inherit pytest's configured Django settings or an external provider."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "PARISHKIT_"))
    }
    return subprocess.run(
        [
            sys.executable,
            str(Path(sys.executable).with_name("pk-stewardship")),
            "collect-static",
            "--destination",
            str(path),
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_static_collection_without_database_or_provider_credentials(tmp_path):
    """The compiled tree contains packaged UI code with private on-disk modes."""
    result = collect_in_fresh_process(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"static_assets_collected": True}
    for name in ("ui-v1.css", "ui-v1.js"):
        path = tmp_path / "stewardship" / name
        assert path.is_file()
        assert path.stat().st_mode & 0o777 == 0o600
    sentinel = tmp_path / "preserve.txt"
    sentinel.write_bytes(b"existing-user-content")
    assert collect_in_fresh_process(tmp_path).returncode == 2
    assert sentinel.read_bytes() == b"existing-user-content"


@pytest.mark.parametrize("kind", ["missing", "symlink", "public", "file"])
def test_static_collection_refuses_unsafe_destinations(tmp_path, kind):
    """No automatic chmod, symlink traversal or file replacement is authorized."""
    path = tmp_path / "target"
    if kind == "symlink":
        path.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "public":
        path.mkdir(mode=0o755)
    elif kind == "file":
        path.write_bytes(b"preserve")
    result = collect_in_fresh_process(path)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    if kind == "file":
        assert path.read_bytes() == b"preserve"


def test_static_command_missing_destination_is_sanitized(capsys):
    """No implicit current-directory write is allowed."""
    assert main(["collect-static"]) == 2
    assert "empty owner-only destination" in capsys.readouterr().err
