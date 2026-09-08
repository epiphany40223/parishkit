"""Shared late-bound runtime defaults for ParishKit tools and services."""

import os
from collections.abc import Mapping
from pathlib import Path


def runtime_root(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve PARISHKIT_ROOT when called, preserving existing CLI semantics."""
    source = os.environ if environ is None else environ
    return Path(source.get("PARISHKIT_ROOT", "/opt/parishkit")).expanduser()
