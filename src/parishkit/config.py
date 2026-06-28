"""YAML configuration loading and validation helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

ConfigData = dict[str, Any]


class ConfigError(ValueError):
    """Raised when runtime configuration is missing or invalid."""


def load_yaml_config(path: str | Path | None, *, required: bool = False) -> ConfigData:
    """Load a YAML config file as a dictionary.

    Empty files are treated as empty dictionaries. Invalid YAML and non-
    mapping top-level values fail fast with a user-facing ``ConfigError``.
    """

    if path is None:
        if required:
            raise ConfigError("configuration file path is required")
        return {}

    config_path = Path(path).expanduser()
    if not config_path.exists():
        if required:
            raise ConfigError(f"configuration file not found: {config_path}")
        return {}

    try:
        raw_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        location = _yaml_error_location(exc)
        raise ConfigError(
            f"could not parse YAML config file {config_path}{location}: {exc}. "
            "Check indentation, ':' after keys, and '-' before list items."
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"could not read configuration file {config_path}: {exc}"
        ) from exc

    if raw_data is None:
        return {}
    if not isinstance(raw_data, dict):
        raise ConfigError(
            f"YAML config file {config_path} must contain a top-level mapping "
            "of key/value sections, not a list or scalar value."
        )
    return raw_data


def _yaml_error_location(exc: yaml.YAMLError) -> str:
    """Return a human-readable line/column suffix for a YAML parser error."""
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return ""
    return f" at line {mark.line + 1}, column {mark.column + 1}"


def require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    """Return ``value`` if it is a mapping, else raise ``ConfigError``.

    ``name`` is used only to build a clear, user-facing error message.
    """
    if not isinstance(value, Mapping):
        raise ConfigError(f"{name} must be a mapping")
    return value


def require_keys(config: Mapping[str, Any], required_keys: set[str]) -> None:
    """Raise ``ConfigError`` if any required key is absent from ``config``.

    Missing keys are reported together, sorted for stable output.
    """
    missing = sorted(required_keys.difference(config))
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(f"missing required configuration key(s): {joined}")


def validate_with[T](config: ConfigData, validator: Callable[[ConfigData], T]) -> T:
    """Run a config validator and normalize common failures."""

    try:
        return validator(config)
    except ConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc
