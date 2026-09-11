"""Typed, non-secret deployment settings resolved before database startup.

This module never opens a credential file, connects to a service, or creates a
directory. The later startup validator checks the actual deployment state.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from parishkit.config import ConfigError, load_yaml_config
from parishkit.paths import runtime_root

from .authentication_policy import AuthenticationLimits
from .runtime_budget import RuntimeBudget, parse_budget


class DeploymentProfile(StrEnum):
    """Execution environment, independent of database-authoritative system mode."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ServiceRole(StrEnum):
    """Least-privilege process identity, not a human portal role."""

    WEB = "web"
    WORKER = "worker"
    SCHEDULER = "scheduler"
    CONFIG_INSTALLER = "config-installer"
    CREDENTIAL_INSTALLER = "credential-installer"
    BACKUP_WORKER = "backup-worker"
    MAIL_DISPATCH = "mail-dispatch"
    TOKEN_KEY_ROTATION = "token-key-rotation"
    BOOTSTRAP = "bootstrap"
    MIGRATION = "migration"
    ADMIN_RECOVERY = "admin-recovery"


# The keys are stable configuration names; all paths, including derived stores,
# can be overridden. Never traverse persistent_root during temporary cleanup.
PATH_DEFAULTS = {
    "config": "config",
    "credentials": "credentials",
    "cache": "cache",
    "logs": "logs",
    "reports": "reports",
    "run": "run",
}
PERSISTENT_STORES = {"postgresql", "valkey", "caddy", "media"}
SECRET_NAMES = frozenset(
    {
        "django_signing",
        "general_encryption",
        "family_code_mac",
        "token_public",
        "token_private",
        "google_oauth",
        "google_workspace",
        "parishsoft",
        "slack",
        "backup_target",
        "backup_data",
        "metrics",
        "handoff_private",
    }
)


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved root and immutable, individually overridable runtime paths."""

    root: Path
    values: Mapping[str, Path]

    def __getitem__(self, name: str) -> Path:
        """Look up a canonical path name without recomputing environment defaults."""
        return self.values[name]


@dataclass(frozen=True)
class DatabaseConfiguration:
    """PostgreSQL connection metadata; passwords remain in individual files."""

    host: str
    port: int
    name: str
    user: str
    password_file: Path | None = field(repr=False)
    connect_timeout: int


@dataclass(frozen=True)
class ValkeyConfiguration:
    """Valkey connection metadata; no password-bearing URL is accepted."""

    host: str
    port: int
    database: int
    password_file: Path | None = field(repr=False)


@dataclass(frozen=True)
class DeploymentConfiguration:
    """Resolved deployment input, not a readiness or authorization decision."""

    profile: DeploymentProfile
    service_role: ServiceRole
    public_origin: str
    trusted_proxy_hops: int
    paths: RuntimePaths = field(repr=False)
    postgres: DatabaseConfiguration
    valkey: ValkeyConfiguration
    secrets: Mapping[str, Path] = field(repr=False)
    credential_target: str | None
    authentication_limits: AuthenticationLimits = field(
        default_factory=AuthenticationLimits
    )
    configuration_file: Path | None = field(default=None, repr=False)
    runtime_budget: RuntimeBudget = field(default_factory=RuntimeBudget)


def _mapping(value: object, keys: set[str] | frozenset[str], label: str) -> dict:
    """Validate object shape without echoing unexpected keys or their values."""
    if not isinstance(value, dict) or set(value) - keys:
        raise ConfigError(f"invalid {label} configuration shape")
    return value


def _text(value: object, label: str) -> str:
    """Require a nonempty string; diagnostics never repeat its contents."""
    if type(value) is not str or not value or value != value.strip():
        raise ConfigError(f"{label} must be a nonempty trimmed string")
    return value


def _integer(value: object, label: str, low: int, high: int) -> int:
    """Accept YAML integers or canonical environment/CLI integer strings."""
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not low <= value <= high:
        raise ConfigError(f"{label} is outside its supported integer range")
    return value


def _path(value: object, base: Path, label: str) -> Path:
    """Resolve YAML paths relative to its directory; callers select CLI cwd."""
    value = _text(value, label)
    try:
        path = Path(value).expanduser()
    except (OSError, ValueError, RuntimeError):
        raise ConfigError(f"{label} path cannot be resolved") from None
    return path if path.is_absolute() else base / path


def _host(value: object, label: str) -> str:
    """Accept an IP address or DNS hostname, never a URL or inline password."""
    host = _text(value, label)
    try:
        ip_address(host)
        return host
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ConfigError(f"{label} must be an IP address or DNS hostname") from None
    if len(ascii_host) > 253 or not all(
        re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", part)
        for part in ascii_host.removesuffix(".").split(".")
    ):
        raise ConfigError(f"{label} must be an IP address or DNS hostname")
    return ascii_host


def _origin(value: object, profile: DeploymentProfile) -> str:
    """Allow only a bare origin, HTTPS in production and loopback HTTP locally."""
    origin = _text(value, "public_origin")
    if any(char.isspace() or ord(char) < 32 for char in origin):
        raise ConfigError(
            "public_origin must not contain whitespace or control characters"
        )
    try:
        parsed = urlsplit(origin)
        port = parsed.port
        invalid = (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.netloc.endswith(":")
            or (port is not None and not 1 <= port <= 65535)
        )
    except ValueError:
        invalid = True
    if invalid:
        raise ConfigError("public_origin must be a bare HTTP(S) origin")
    hostname = _host(parsed.hostname, "public_origin host").lower()
    if profile is DeploymentProfile.PRODUCTION:
        if parsed.scheme != "https":
            raise ConfigError("production public_origin requires HTTPS")
    elif parsed.scheme != "http" or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ConfigError("development/test public_origin requires loopback HTTP")
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


def load_deployment(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> DeploymentConfiguration:
    """Resolve explicit overrides > environment > YAML > development defaults.

    Override and environment names are identical; explicit overrides come from
    the CLI. Environment/CLI paths are cwd-relative, YAML paths file-relative.
    Only documented names are consumed from the environment. A production
    profile has no implicit public origin or credential fallback.
    """
    env = os.environ if environ is None else environ
    explicit = {} if overrides is None else overrides
    try:
        document = load_yaml_config(
            path, required=path is not None, reject_duplicate_keys=True
        )
    except (ConfigError, OSError, UnicodeError):
        raise ConfigError("deployment YAML is unreadable or invalid") from None
    _mapping(
        document,
        # Recognize other tools' sections without interpreting their contents.
        # Keep this list aligned with scripts/*/example-config.yaml; regression
        # tests load every example so new shared sections require review here.
        {
            "calendars",
            "common",
            "constant_contact",
            "deployment",
            "email",
            "google",
            "jobs",
            "lock",
            "logging",
            "parishsoft",
            "print_member",
            "print_ministries",
            "rosters",
            "runner",
            "slack",
            "sync",
        },
        "top-level",
    )
    deployment = _mapping(
        document.get("deployment", {}),
        {
            "schema_version",
            "profile",
            "service_role",
            "public_origin",
            "trusted_proxy_hops",
            "paths",
            "postgres",
            "valkey",
            "secrets",
            "credential_target",
            "authentication_limits",
            "runtime_budget",
        },
        "deployment",
    )
    if (
        type(deployment.get("schema_version", 1)) is not int
        or deployment.get("schema_version", 1) != 1
    ):
        raise ConfigError("unsupported deployment schema version")
    base = path.expanduser().absolute().parent if path is not None else Path.cwd()
    consumed_keys: set[str] = set()

    def select(name: str, yaml_value: object, default: object = None) -> object:
        """Resolve one documented deployment setting without exposing its value."""
        key = f"PARISHKIT_STEWARDSHIP_{name}"
        consumed_keys.add(key)
        return explicit.get(
            key, env.get(key, yaml_value if yaml_value is not None else default)
        )

    def select_path(name: str, yaml_value: object, default: Path | None) -> Path | None:
        """Apply source-specific relative path rules to one path setting."""
        key = "PARISHKIT_ROOT" if name == "ROOT" else f"PARISHKIT_STEWARDSHIP_{name}"
        consumed_keys.add(key)
        if key in explicit or key in env:
            return _path(explicit.get(key, env.get(key)), Path.cwd(), name)
        return default if yaml_value is None else _path(yaml_value, base, name)

    try:
        profile = DeploymentProfile(
            select("PROFILE", deployment.get("profile"), "development")
        )
        role = ServiceRole(
            select("SERVICE_ROLE", deployment.get("service_role"), "web")
        )
    except ValueError:
        raise ConfigError("unknown deployment profile or service role") from None
    path_config = _mapping(
        deployment.get("paths", {}),
        {
            "root",
            *PATH_DEFAULTS,
            "authority",
            "persistent_root",
            *PERSISTENT_STORES,
        },
        "paths",
    )
    root = select_path("ROOT", path_config.get("root"), runtime_root({}))
    resolved = {
        name: select_path(f"PATH_{name.upper()}", path_config.get(name), root / suffix)
        for name, suffix in PATH_DEFAULTS.items()
    }
    resolved["authority"] = select_path(
        "PATH_AUTHORITY",
        path_config.get("authority"),
        resolved["config"] / "stewardship",
    )
    resolved["persistent_root"] = select_path(
        "PATH_PERSISTENT_ROOT",
        path_config.get("persistent_root"),
        resolved["run"] / "persistent",
    )
    for name in sorted(PERSISTENT_STORES):
        resolved[name] = select_path(
            f"PATH_{name.upper()}",
            path_config.get(name),
            resolved["persistent_root"] / name,
        )
    paths = RuntimePaths(root, MappingProxyType(resolved))
    postgres = _mapping(
        deployment.get("postgres", {}),
        {"host", "port", "name", "user", "password_file", "connect_timeout"},
        "postgres",
    )
    database = DatabaseConfiguration(
        host=_host(
            select("POSTGRES_HOST", postgres.get("host"), "postgres"), "postgres.host"
        ),
        port=_integer(
            select("POSTGRES_PORT", postgres.get("port"), 5432),
            "postgres.port",
            1,
            65535,
        ),
        name=_text(
            select("POSTGRES_NAME", postgres.get("name"), "stewardship"),
            "postgres.name",
        ),
        user=_text(
            select("POSTGRES_USER", postgres.get("user"), "stewardship"),
            "postgres.user",
        ),
        password_file=select_path(
            "POSTGRES_PASSWORD_FILE", postgres.get("password_file"), None
        ),
        connect_timeout=_integer(
            select("POSTGRES_CONNECT_TIMEOUT", postgres.get("connect_timeout"), 5),
            "postgres.connect_timeout",
            1,
            60,
        ),
    )
    valkey = _mapping(
        deployment.get("valkey", {}),
        {"host", "port", "database", "password_file"},
        "valkey",
    )
    broker = ValkeyConfiguration(
        host=_host(select("VALKEY_HOST", valkey.get("host"), "valkey"), "valkey.host"),
        port=_integer(
            select("VALKEY_PORT", valkey.get("port"), 6379), "valkey.port", 1, 65535
        ),
        database=_integer(
            select("VALKEY_DATABASE", valkey.get("database"), 0),
            "valkey.database",
            0,
            15,
        ),
        password_file=select_path(
            "VALKEY_PASSWORD_FILE", valkey.get("password_file"), None
        ),
    )
    secret_config = _mapping(deployment.get("secrets", {}), SECRET_NAMES, "secrets")
    secrets = {}
    for name in sorted(SECRET_NAMES):
        reference = select_path(f"SECRET_{name.upper()}", secret_config.get(name), None)
        if reference is not None:
            secrets[name] = reference
    target = select("CREDENTIAL_TARGET", deployment.get("credential_target"))
    if target is not None and (
        type(target) is not str or target not in SECRET_NAMES - {"handoff_private"}
    ):
        raise ConfigError("unknown credential installer target")
    if (role is ServiceRole.CREDENTIAL_INSTALLER) != (target is not None):
        raise ConfigError("credential_target is required only for credential-installer")
    origin = _origin(
        select(
            "PUBLIC_ORIGIN",
            deployment.get("public_origin"),
            None
            if profile is DeploymentProfile.PRODUCTION
            else "http://localhost:8000",
        ),
        profile,
    )
    hops = _integer(
        select(
            "TRUSTED_PROXY_HOPS",
            deployment.get("trusted_proxy_hops"),
            1 if profile is DeploymentProfile.PRODUCTION else 0,
        ),
        "trusted_proxy_hops",
        0,
        1,
    )
    if hops != (1 if profile is DeploymentProfile.PRODUCTION else 0):
        raise ConfigError("proxy hops must be one in production and zero locally")
    limit_fields = fields(AuthenticationLimits)
    limit_config = _mapping(
        deployment.get("authentication_limits", {}),
        {item.name for item in limit_fields},
        "authentication limits",
    )
    limits = AuthenticationLimits(
        **{
            item.name: _integer(
                select(
                    "AUTH_LIMIT_" + item.name.upper(),
                    limit_config.get(item.name),
                    item.default,
                ),
                "authentication limit",
                1,
                1000,
            )
            for item in limit_fields
        }
    )
    supplied_keys = set(explicit) | {
        key for key in env if key.startswith("PARISHKIT_STEWARDSHIP_")
    }
    if supplied_keys - consumed_keys:
        raise ConfigError("unknown deployment environment or CLI override")
    if profile is DeploymentProfile.PRODUCTION:
        limits.warn_if_weaker()
    return DeploymentConfiguration(
        profile,
        role,
        origin,
        hops,
        paths,
        database,
        broker,
        MappingProxyType(secrets),
        target,
        limits,
        path.expanduser().absolute() if path is not None else None,
        parse_budget(deployment.get("runtime_budget", {})),
    )
