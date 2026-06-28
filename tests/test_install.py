from __future__ import annotations

import os
import subprocess
import sys
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

INSTALL_SCRIPT = Path(__file__).parents[1] / "install.py"


def load_install_module():
    """Import the standalone install.py script as a module for direct testing.

    The installer lives outside the package, so it is loaded by path rather
    than via a normal import.
    """
    module = types.ModuleType("parishkit_install")
    loader = SourceFileLoader(module.__name__, str(INSTALL_SCRIPT))
    loader.exec_module(module)
    return module


def test_install_root_precedence(monkeypatch, tmp_path):
    """Root resolution prefers the CLI value, then PARISHKIT_ROOT, then the default."""
    install = load_install_module()
    env_root = tmp_path / "env-root"
    cli_root = tmp_path / "cli-root"

    monkeypatch.setenv("PARISHKIT_ROOT", str(env_root))

    # An explicit CLI root wins even when PARISHKIT_ROOT is set.
    assert cli_root == install.resolve_install_root(cli_root)
    # With no CLI root, the environment variable is used.
    assert env_root == install.resolve_install_root(None)

    monkeypatch.delenv("PARISHKIT_ROOT")

    # With neither override present, the deployment default applies.
    assert Path("/opt/parishkit") == install.resolve_install_root(None)


def test_install_dry_run_uses_parishkit_root(tmp_path):
    """Dry run rooted at PARISHKIT_ROOT prints the expected mkdir/symlink/env steps."""
    env = os.environ.copy()
    env["PARISHKIT_ROOT"] = str(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(INSTALL_SCRIPT),
            "--dry-run",
            "--skip-package-install",
        ],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )

    assert f"Installing ParishKit into {tmp_path}" in result.stdout
    assert f"mkdir -p -m 0750 {tmp_path / 'config'}" in result.stdout
    assert f"mkdir -p -m 0700 {tmp_path / 'credentials'}" in result.stdout
    assert f"ln -sfn {tmp_path / 'venv/bin/pk-cron-runner'}" in result.stdout
    assert f"Set PARISHKIT_ROOT={tmp_path}" in result.stdout


def test_install_dry_run_installdir_overrides_parishkit_root(tmp_path):
    """The --installdir option overrides PARISHKIT_ROOT, which stays out of output."""
    env = os.environ.copy()
    env["PARISHKIT_ROOT"] = str(tmp_path / "env-root")
    install_root = tmp_path / "cli-root"

    result = subprocess.run(
        [
            sys.executable,
            str(INSTALL_SCRIPT),
            "--dry-run",
            "--skip-package-install",
            "--installdir",
            str(install_root),
        ],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )

    assert f"Installing ParishKit into {install_root}" in result.stdout
    assert str(tmp_path / "env-root") not in result.stdout
