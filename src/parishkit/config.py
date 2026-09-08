"""YAML configuration loading and validation helpers."""

from __future__ import annotations

import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

ConfigData = dict[str, Any]

# Only the opt-in strict loader enforces these ceilings. Keep legacy tools'
# configuration compatibility independent of stewardship's bounded read path.
STRICT_YAML_MAX_BYTES = 8_000_000
STRICT_YAML_MAX_NODES = 100_000
STRICT_YAML_MAX_DEPTH = 64


class ConfigError(ValueError):
    """Raised when runtime configuration is missing or invalid."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Bound composition and alias expansion before constructing strict mappings."""

    def __init__(self, stream):
        """Initialize per-load budgets, never shared across configuration reads."""
        self._parse_nodes = 0
        self._parse_depth = 0
        self._expanded_nodes = 0
        super().__init__(stream)

    def compose_node(self, parent, index):
        """Stop recursive composition before it exhausts stack or node budgets."""
        self._parse_nodes += 1
        self._parse_depth += 1
        try:
            if (
                self._parse_nodes > STRICT_YAML_MAX_NODES
                or self._parse_depth > STRICT_YAML_MAX_DEPTH
            ):
                raise ConfigError("configuration YAML exceeds structural limits")
            return super().compose_node(parent, index)
        finally:
            self._parse_depth -= 1

    def _check_expansion(self, node, depth=1):
        """Count each alias occurrence before merge flattening can amplify it.

        Do not memoize shared nodes: the expanded graph, not just its compact
        representation, needs a budget. Cycles terminate at the depth ceiling.
        This traversal uses at most the bounded nesting depth of stack frames.
        """
        self._expanded_nodes += 1
        if (
            self._expanded_nodes > STRICT_YAML_MAX_NODES
            or depth > STRICT_YAML_MAX_DEPTH
        ):
            raise ConfigError("configuration YAML exceeds structural limits")
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                self._check_expansion(key, depth + 1)
                self._check_expansion(value, depth + 1)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                self._check_expansion(value, depth + 1)

    def construct_document(self, node):
        """Check the complete graph before any object or merged mapping is built."""
        self._expanded_nodes = 0
        self._check_expansion(node)
        return super().construct_document(node)

    def construct_mapping(self, node, deep=False):
        """Reject duplicate keys, including ambiguous overrides through YAML merges."""
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:
                raise ConfigError(
                    "configuration YAML has an unhashable mapping key"
                    f" at line {key_node.start_mark.line + 1},"
                    f" column {key_node.start_mark.column + 1}"
                ) from None
            if duplicate:
                raise ConfigError(
                    "configuration YAML has a duplicate mapping key"
                    f" at line {key_node.start_mark.line + 1},"
                    f" column {key_node.start_mark.column + 1}"
                )
        return super().construct_mapping(node, deep=deep)


def _load_strict_yaml(path: str | Path, *, required: bool) -> ConfigData:
    """Bound strict reads and expose only authored diagnostics and numeric locations.

    Keep path resolution, filesystem access, and scalar construction inside the
    private-error boundary. Chained parser/OS exceptions can contain filenames
    and YAML snippets, so suppress their normal traceback rendering as well.
    Legacy consumers retain the separate, detailed diagnostics below.
    """
    try:
        config_path = Path(path).expanduser()
        try:
            metadata = config_path.stat()
        except (FileNotFoundError, NotADirectoryError):
            if required:
                raise ConfigError("configuration file not found") from None
            return {}
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError("configuration file must be a regular file")
        with config_path.open("rb") as stream:
            data = stream.read(STRICT_YAML_MAX_BYTES + 1)
        if len(data) > STRICT_YAML_MAX_BYTES:
            raise ConfigError("configuration YAML exceeds input byte limit")
        raw_data = yaml.load(data.decode("utf-8"), Loader=_UniqueKeySafeLoader)
    except ConfigError:
        # Only our own loader emits ConfigError, with authored public messages.
        raise
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"could not parse configuration YAML{_yaml_error_location(exc)}. "
            "Check indentation, ':' after keys, and '-' before list items."
        ) from None
    except OSError:
        raise ConfigError(
            "could not read configuration file; check access and file permissions"
        ) from None
    except (ValueError, RuntimeError):
        # Covers invalid UTF-8/scalar values, path expansion, and recursion.
        raise ConfigError("configuration YAML cannot be safely parsed") from None
    if raw_data is None:
        return {}
    if not isinstance(raw_data, dict):
        raise ConfigError(
            "configuration YAML must contain a top-level mapping "
            "of key/value sections, not a list or scalar value."
        )
    return raw_data


def load_yaml_config(
    path: str | Path | None,
    *,
    required: bool = False,
    reject_duplicate_keys: bool = False,
) -> ConfigData:
    """Load a YAML config file as a dictionary.

    Empty files are treated as empty dictionaries. Invalid YAML and non-
    mapping top-level values fail fast with a user-facing ``ConfigError``.
    ``reject_duplicate_keys`` opts into strict nested mappings and bounded
    byte/node/depth consumption (including alias expansion). Strict diagnostics
    omit paths, source contents, and chained parser/OS error details, retaining
    authored hints and numeric source locations. The default preserves existing
    tools' YAML merge/last-value behavior, read limits, and detailed diagnostics.
    """

    if path is None:
        if required:
            raise ConfigError("configuration file path is required")
        return {}

    if reject_duplicate_keys:
        return _load_strict_yaml(path, required=required)

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


def reject_unknown_keys(
    config: Mapping[str, Any],
    allowed_keys: set[str],
    name: str,
) -> None:
    """Raise ``ConfigError`` when ``config`` contains unsupported keys."""
    unknown = sorted(str(key) for key in config if key not in allowed_keys)
    if not unknown:
        return
    allowed = ", ".join(sorted(allowed_keys))
    raise ConfigError(
        f"{name} has unsupported key(s): {', '.join(unknown)}. "
        f"Allowed key(s): {allowed}."
    )


def validate_with[T](config: ConfigData, validator: Callable[[ConfigData], T]) -> T:
    """Run a config validator and normalize common failures."""

    try:
        return validator(config)
    except ConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc


def resolve_path(value: Any, name: str, *, base_dir: Path | None = None) -> Path:
    """Resolve a required config path with config-relative semantics.

    Strings and ``Path`` objects are accepted. ``~`` is expanded, and relative
    paths are resolved against ``base_dir`` when provided so tool-specific
    credential files behave like the shared common paths.
    """
    if isinstance(value, Path):
        path = value.expanduser()
    elif isinstance(value, str) and value:
        path = Path(value).expanduser()
    else:
        raise ConfigError(f"{name} must be a path string")
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return path
