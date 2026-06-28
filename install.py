#!/usr/bin/env python3
"""Install ParishKit into a local runtime tree."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_INSTALL_ROOT = Path("/opt/parishkit")
DEFAULT_EXTRAS = "google,slack"
RUNTIME_DIR_MODES = {
    ".": 0o750,
    "bin": 0o755,
    "config": 0o750,
    "credentials": 0o700,
    "cache": 0o750,
    "logs": 0o750,
    "reports": 0o750,
    "run": 0o750,
}
CONSOLE_COMMANDS = (
    "pk-cron-runner",
    "pk-query-ps-memfam",
    "pk-print-ps-ministries",
    "pk-validate-gcalendar-reservations",
    "pk-create-ps-ministry-rosters",
    "pk-sync-ps-to-ggroup",
    "pk-sync-ps-to-cc",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install ParishKit into a runtime tree."
    )
    parser.add_argument(
        "--installdir",
        type=Path,
        help=(
            "installation root; defaults to PARISHKIT_ROOT when set, otherwise "
            "/opt/parishkit"
        ),
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python interpreter used to create the install venv",
    )
    parser.add_argument(
        "--extras",
        default=DEFAULT_EXTRAS,
        help=(
            "comma-separated package extras to install; use an empty value for "
            "the base package only"
        ),
    )
    parser.add_argument(
        "--skip-package-install",
        action="store_true",
        help="create directories and command links without running pip",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print planned actions without changing the filesystem",
    )
    return parser.parse_args(argv)


def resolve_install_root(installdir: Path | None) -> Path:
    if installdir is not None:
        return installdir.expanduser().resolve()
    parishkit_root = os.environ.get("PARISHKIT_ROOT")
    if parishkit_root:
        return Path(parishkit_root).expanduser().resolve()
    return DEFAULT_INSTALL_ROOT


def package_spec(source_root: Path, extras: str) -> str:
    if not extras:
        return str(source_root)
    return f"{source_root}[{extras}]"


def command_path(root: Path, *parts: str) -> Path:
    return root.joinpath(*parts)


def ensure_directory(path: Path, mode: int, *, dry_run: bool) -> None:
    if dry_run:
        print(f"mkdir -p -m {mode:04o} {path}")
        return
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)


def create_runtime_tree(root: Path, *, dry_run: bool) -> None:
    for relative_path, mode in RUNTIME_DIR_MODES.items():
        ensure_directory(command_path(root, relative_path), mode, dry_run=dry_run)


def run_command(command: list[str], *, dry_run: bool) -> None:
    printable = " ".join(str(part) for part in command)
    if dry_run:
        print(printable)
        return
    subprocess.run(command, check=True)


def install_package(
    root: Path,
    source_root: Path,
    python: Path,
    extras: str,
    *,
    dry_run: bool,
) -> None:
    venv = command_path(root, "venv")
    run_command([str(python), "-m", "venv", str(venv)], dry_run=dry_run)
    pip = command_path(venv, "bin", "pip")
    run_command(
        [str(pip), "install", "--upgrade", "pip"],
        dry_run=dry_run,
    )
    run_command(
        [str(pip), "install", "--upgrade", package_spec(source_root, extras)],
        dry_run=dry_run,
    )


def link_console_commands(root: Path, *, dry_run: bool) -> None:
    bin_dir = command_path(root, "bin")
    venv_bin_dir = command_path(root, "venv", "bin")
    for command in CONSOLE_COMMANDS:
        source = command_path(venv_bin_dir, command)
        target = command_path(bin_dir, command)
        if dry_run:
            print(f"ln -sfn {source} {target}")
            continue
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(source)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_root = Path(__file__).resolve().parent
    install_root = resolve_install_root(args.installdir)

    print(f"Installing ParishKit into {install_root}")
    create_runtime_tree(install_root, dry_run=args.dry_run)
    if args.skip_package_install:
        print("Skipping package installation")
    else:
        install_package(
            install_root,
            source_root,
            args.python,
            args.extras,
            dry_run=args.dry_run,
        )
    link_console_commands(install_root, dry_run=args.dry_run)

    print(f"Add {install_root / 'bin'} to PATH for ParishKit commands.")
    print(f"Set PARISHKIT_ROOT={install_root} when using this install root.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
