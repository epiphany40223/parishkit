"""Typed, non-secret deployment settings resolved before database startup.

This module never opens a credential file, connects to a service, or creates a
directory. The later startup validator checks the actual deployment state.
"""

import json
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
from .jobs.operational_policy import IncidentPolicy
from .runtime_budget import RuntimeBudget, parse_budget
from .runtime_network import RuntimeNetwork, parse_network


class DeploymentProfile(StrEnum):
    """Execution environment, independent of database-authoritative system mode.

    Development and test are plain HTTP on loopback with no proxy. LOCAL is the
    production-shaped laptop environment (#476): HTTPS through one Caddy hop,
    synthetic data, no real provider. New code that distinguishes LOCAL must
    test ``is LOCAL`` or ``behind_proxy``, never ``!= DEVELOPMENT``, which
    would silently change meaning as profiles are added.
    """

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"
    LOCAL = "local"

    @property
    def behind_proxy(self) -> bool:
        """Whether web is reached only through the deployment's own Caddy hop."""
        return self in PROXIED_PROFILES


# The profiles whose web service sits behind the deployment's own Caddy.
PROXIED_PROFILES = frozenset({DeploymentProfile.PRODUCTION, DeploymentProfile.LOCAL})
# The one public origin the LOCAL profile admits: the Caddy publication, the
# VM port forward and every printed link use exactly this value.
LOCAL_PUBLIC_ORIGIN = "https://localhost:8443"


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
    DATABASE_PROVISION = "database-provision"


VALKEY_IDENTITIES = frozenset(
    {"web", "worker", "scheduler", "mail-dispatch", "backup-worker"}
)


# The keys are stable configuration names; all paths, including derived stores,
# can be overridden. Never traverse persistent_root during temporary cleanup.
PATH_DEFAULTS = {
    "config": "config",
    "credentials": "credentials",
    "cache": "cache",
    "logs": "logs",
    "reports": "reports",
    "run": "run",
    "backups": "backups",
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
    download_password_file: Path | None = field(default=None, repr=False)
    password_files: Mapping[str, Path] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Copy references so callers cannot mutate paths after admission."""
        object.__setattr__(
            self, "password_files", MappingProxyType(dict(self.password_files))
        )


@dataclass(frozen=True)
class ValkeyConfiguration:
    """Valkey connection metadata; no password-bearing URL is accepted."""

    host: str
    port: int
    database: int
    password_file: Path | None = field(repr=False)
    password_files: Mapping[str, Path] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Keep independently overridable credential references immutable."""
        object.__setattr__(
            self, "password_files", MappingProxyType(dict(self.password_files))
        )


# How the mail worker submits Family mail (#284): "batched" reuses one private
# helper, token and SMTP connection across messages; "per_message" is the
# earlier one-helper-per-message transport, kept as an operator fallback.
FAMILY_MAIL_TRANSPORTS = frozenset({"batched", "per_message"})
FAMILY_MAIL_TRANSPORT_VARIABLE = "PARISHKIT_STEWARDSHIP_FAMILY_MAIL_TRANSPORT"
# How many consumer processes the mail-dispatch container runs. Two
# send the launch's Family mail in about half the time; one is the fallback.
# More would need a larger mail login connection limit than role_limit gives.
MAIL_CONSUMER_COUNTS = frozenset({1, 2})
MAIL_CONSUMERS_VARIABLE = "PARISHKIT_STEWARDSHIP_MAIL_CONSUMERS"
# The bulk Family send (#430, jobs/family_mail_bulk.py): off by default; "1"
# on the scheduler, worker and mail-dispatch services plans, prepares and
# sends scheduled Family mail in batches. "0" or unset is the one-at-a-time
# path. BULK_SEND_BATCH is B, the most messages one send batch commits as
# "submitting" before sending them (1-100, default 20).
BULK_FAMILY_SEND_VARIABLE = "PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND"
BULK_SEND_BATCH_VARIABLE = "PARISHKIT_STEWARDSHIP_BULK_SEND_BATCH"


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
    runtime_network: RuntimeNetwork = field(default_factory=RuntimeNetwork)
    operational_alerts: IncidentPolicy = field(default_factory=IncidentPolicy)
    family_mail_transport: str = "batched"
    mail_consumers: int = 2
    bulk_family_send: bool = False
    bulk_send_batch: int = 20


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
    """Allow only a bare origin, with one scheme and host rule per profile.

    Production requires HTTPS; LOCAL requires exactly LOCAL_PUBLIC_ORIGIN;
    development and test require loopback HTTP.
    """
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
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        authority += f":{port}"
    normalized = f"{parsed.scheme}://{authority}"
    if profile is DeploymentProfile.PRODUCTION:
        if parsed.scheme != "https":
            raise ConfigError("production public_origin requires HTTPS")
    elif profile is DeploymentProfile.LOCAL:
        # The normalized form is compared, so a trailing slash or upper-case
        # host is still the one origin; any other host (127.0.0.1 and ::1
        # included), scheme or port is refused. The go-live origin check
        # (origin_check) refuses LOCAL outright until its own LOCAL rule lands
        # (OPS-10.04); that rule will not live here.
        if normalized != LOCAL_PUBLIC_ORIGIN:
            raise ConfigError(f"local public_origin must be {LOCAL_PUBLIC_ORIGIN}")
    elif parsed.scheme != "http" or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ConfigError("development/test public_origin requires loopback HTTP")
    return normalized


def load_deployment(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
    document: Mapping | None = None,
) -> DeploymentConfiguration:
    """Resolve explicit overrides > environment > YAML > development defaults.

    Override and environment names are identical; explicit overrides come from
    the CLI. Environment/CLI paths are cwd-relative, YAML paths file-relative.
    Only documented names are consumed from the environment. A production
    profile has no implicit public origin or credential fallback. An already
    parsed `document` (a recorded deployment document, whose paths are
    absolute) is validated exactly as a file would be, without touching disk.
    """
    env = os.environ if environ is None else environ
    explicit = {} if overrides is None else overrides
    if document is not None:
        if path is not None:
            raise ConfigError("Give a deployment file or a document, not both.")
        # A private copy: validation must not share structure with the caller.
        document = json.loads(json.dumps(document))
    else:
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
            "runtime_network",
            "operational_alerts",
            "family_mail_transport",
            "mail_consumers",
            "bulk_family_send",
            "bulk_send_batch",
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
        {
            "host",
            "port",
            "name",
            "user",
            "password_file",
            "connect_timeout",
            "download_password_file",
            "password_files",
        },
        "postgres",
    )
    password_names = {role.value for role in ServiceRole} | {"operator", "download"}
    password_names |= {
        "credential-installer-" + target.replace("_", "-")
        for target in SECRET_NAMES - {"handoff_private"}
    }
    password_files = _mapping(
        postgres.get("password_files", {}), password_names, "database password files"
    )
    resolved_passwords = {}
    for name in sorted(password_names):
        credential_path = select_path(
            "POSTGRES_PASSWORD_FILE_" + name.upper().replace("-", "_"),
            password_files.get(name),
            None,
        )
        if credential_path is not None:
            resolved_passwords[name] = credential_path
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
        download_password_file=select_path(
            "POSTGRES_DOWNLOAD_PASSWORD_FILE",
            postgres.get("download_password_file"),
            None,
        ),
        password_files=resolved_passwords,
    )
    valkey = _mapping(
        deployment.get("valkey", {}),
        {"host", "port", "database", "password_file", "password_files"},
        "valkey",
    )
    valkey_files = _mapping(
        valkey.get("password_files", {}), VALKEY_IDENTITIES, "Valkey password files"
    )
    resolved_valkey_files = {}
    for name in sorted(VALKEY_IDENTITIES):
        reference = select_path(
            "VALKEY_PASSWORD_FILE_" + name.upper().replace("-", "_"),
            valkey_files.get(name),
            None,
        )
        if reference is not None:
            resolved_valkey_files[name] = reference
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
        password_files=resolved_valkey_files,
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
    # Production has no implicit origin; LOCAL has exactly one; development
    # and test default to the scaffold's loopback HTTP port.
    if profile is DeploymentProfile.PRODUCTION:
        default_origin = None
    elif profile is DeploymentProfile.LOCAL:
        default_origin = LOCAL_PUBLIC_ORIGIN
    else:
        default_origin = "http://localhost:8000"
    origin = _origin(
        select("PUBLIC_ORIGIN", deployment.get("public_origin"), default_origin),
        profile,
    )
    # Exactly one trusted hop behind Caddy (production and local), none when
    # web is reached directly (development and test).
    expected_hops = 1 if profile.behind_proxy else 0
    hops = _integer(
        select(
            "TRUSTED_PROXY_HOPS", deployment.get("trusted_proxy_hops"), expected_hops
        ),
        "trusted_proxy_hops",
        0,
        1,
    )
    if hops != expected_hops:
        raise ConfigError(
            "proxy hops must be one in production and local, "
            "zero in development and test"
        )
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
    alert_fields = fields(IncidentPolicy)
    alert_config = _mapping(
        deployment.get("operational_alerts", {}),
        {item.name for item in alert_fields},
        "operational alerts",
    )
    alert_policy = IncidentPolicy(
        **{
            item.name: _integer(
                select(
                    "OPERATIONAL_" + item.name.upper(),
                    alert_config.get(item.name),
                    item.default,
                ),
                "operational alert window",
                60,
                86400,
            )
            for item in alert_fields
        }
    )
    transport = select("FAMILY_MAIL_TRANSPORT", None)
    if transport in (None, ""):
        # The rendered Compose file passes the variable to the mail worker
        # empty unless the operator's shell exports it, so empty means unset.
        transport = deployment.get("family_mail_transport", "batched")
    if type(transport) is not str or transport not in FAMILY_MAIL_TRANSPORTS:
        raise ConfigError("family_mail_transport must be batched or per_message")
    consumers = select("MAIL_CONSUMERS", None)
    if consumers in (None, ""):
        # Empty means unset here too, as for the transport switch above.
        consumers = deployment.get("mail_consumers", 2)
    elif consumers in {"1", "2"}:
        # The environment carries text; the YAML carries an integer.
        consumers = int(consumers)
    if type(consumers) is not int or consumers not in MAIL_CONSUMER_COUNTS:
        raise ConfigError("mail_consumers must be 1 or 2")
    bulk = select("BULK_FAMILY_SEND", None)
    if bulk in (None, ""):
        # Empty means unset, as for the transport switch above.
        bulk = deployment.get("bulk_family_send", False)
    elif bulk in {"0", "1"}:
        bulk = bulk == "1"
    if type(bulk) is not bool:
        raise ConfigError("bulk_family_send must be true or false (1 or 0)")
    batch = select("BULK_SEND_BATCH", None)
    if batch in (None, ""):
        batch = deployment.get("bulk_send_batch", 20)
    batch = _integer(batch, "bulk_send_batch", 1, 100)
    supplied_keys = set(explicit) | {
        key for key in env if key.startswith("PARISHKIT_STEWARDSHIP_")
    }
    if supplied_keys - consumed_keys:
        raise ConfigError("unknown deployment environment or CLI override")
    # Production only: LOCAL holds synthetic data, so weaker limits there are
    # a developer's choice, not an operational warning.
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
        parse_network(deployment.get("runtime_network", {})),
        alert_policy,
        transport,
        consumers,
        bulk,
        batch,
    )
