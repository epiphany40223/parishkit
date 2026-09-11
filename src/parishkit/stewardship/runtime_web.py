"""Validated web-process assembly; no settings import or flag bypasses admission."""

import hashlib
import hmac
import json
from copy import deepcopy
from importlib import import_module

from parishkit.config import ConfigError

from .accounts.cryptography import independent_keyrings
from .accounts.key_files import _unique_object, parse_keyring, read_private
from .accounts.metrics_credentials import MetricsCredential, credential_receipt
from .deployment import DeploymentProfile, ServiceRole
from .runtime_database import database_settings
from .runtime_health import RuntimeHealth
from .runtime_paths import RuntimeLayout, private_directory
from .service_boundaries import admit_online_service, kernel_mounts
from .web.security import browser_settings


def admit_lifecycle_mounts(configuration):
    """The operational path additionally requires exact read-only config/lock files."""
    mounts = {mount.target: mount for mount in kernel_mounts()}
    for path in (
        configuration.configuration_file,
        RuntimeLayout(configuration).interlock,
    ):
        if path is None or path not in mounts or not mounts[path].read_only:
            raise ConfigError(
                "Runtime configuration and lifecycle mounts are required."
            )


def google_client(path):
    """Load a local OAuth application, never reuse Workspace mail credentials.

    The compact operator file has only client_id/client_secret. The application
    never returns this document in diagnostics or persists a SocialApp secret.
    Ownership of the Google account itself is established only during Google login.
    """
    return parse_google_client(read_private(path))


def parse_google_client(raw):
    """Parse the same admitted bytes later identified by the consumer receipt."""
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if type(value) is not dict or set(value) != {"client_id", "client_secret"}:
            raise ValueError
        if any(
            type(item) is not str
            or not item
            or len(item) > 2048
            or any(ord(char) <= 32 for char in item)
            for item in value.values()
        ):
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ConfigError("The OAuth client file is invalid.") from None


def valkey_client(configuration):
    """Create a finite authenticated pool; never put a password in a broker URL."""
    from redis import Redis

    broker = configuration.valkey
    if broker.password_file is None:
        raise ConfigError("An individual Valkey password file is required.")
    try:
        password = read_private(broker.password_file).decode("ascii").removesuffix("\n")
        if not password or any(ord(char) <= 32 for char in password):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ConfigError("The Valkey password file is invalid.") from None
    return Redis(
        host=broker.host,
        port=broker.port,
        db=broker.database,
        username="web",
        password=password,
        socket_connect_timeout=3,
        socket_timeout=3,
        max_connections=configuration.runtime_budget.web_threads + 2,
        retry_on_timeout=False,
    )


def google_provider_settings(oauth):
    """Translate the private operator document into allauth's credential contract."""
    return {
        "google": {
            "SCOPE": ["openid", "email"],
            "OAUTH_PKCE_ENABLED": True,
            "APPS": [
                {
                    "client_id": oauth["client_id"],
                    "secret": oauth["client_secret"],
                    "key": "",
                }
            ],
        }
    }


def _download_settings(configuration, ordinary):
    """Keep all server-side timeouts compatible with the finite total stream budget."""
    path = configuration.postgres.download_password_file
    if path is None:
        raise ConfigError("Web requires its isolated download-role password file.")
    try:
        password = read_private(path).decode("utf-8").removesuffix("\n")
        if not password or any(ord(char) < 32 for char in password):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ConfigError("The download password file is invalid.") from None
    result = deepcopy(ordinary)
    budget = configuration.runtime_budget
    result.update(USER="pk_stewardship_download", PASSWORD=password)
    result["OPTIONS"]["options"] = (
        f"-c idle_in_transaction_session_timeout={budget.download_idle_seconds}s "
        f"-c statement_timeout={budget.download_seconds}s "
        f"-c transaction_timeout={budget.download_seconds}s"
    )
    return result


def configure_web(configuration):
    """Admit mounts, key purposes, SQL roles and coherent authority before serving."""
    import django
    from django.conf import settings

    if settings.configured:
        raise ConfigError("Operational web startup requires a fresh process.")
    if admit_online_service(configuration) is not ServiceRole.WEB:
        raise ConfigError("Web startup requires its isolated service profile.")
    admit_lifecycle_mounts(configuration)
    required = {
        "django_signing",
        "general_encryption",
        "family_code_mac",
        "token_public",
        "google_oauth",
        "metrics",
    }
    if configuration.secrets.keys() != required:
        raise ConfigError("Web credential mounts are incomplete.")
    # Read each mounted inode once. A receipt must identify bytes actually used
    # by this worker, not a later reopening that could observe another file.
    loaded = {name: read_private(configuration.secrets[name]) for name in required}
    rings = {
        name: parse_keyring(loaded[name], name)
        for name in required - {"google_oauth", "metrics"}
    }
    independent_keyrings(*rings.values())
    oauth = parse_google_client(loaded["google_oauth"])
    metrics_token = MetricsCredential.parse(loaded["metrics"]).token
    base = import_module("parishkit.stewardship.settings.base")
    values = {name: getattr(base, name) for name in dir(base) if name.isupper()}
    ordinary = database_settings(configuration)
    values.update(rings["django_signing"].django_settings())
    values.update(browser_settings(configuration))
    values["ALLOWED_HOSTS"] = list(
        dict.fromkeys([*values["ALLOWED_HOSTS"], "localhost", "127.0.0.1", "[::1]"])
    )
    values["DATABASES"] = {
        "default": ordinary,
        "download": _download_settings(configuration, ordinary),
    }
    values["SOCIALACCOUNT_PROVIDERS"] = google_provider_settings(oauth)
    values["STEWARDSHIP_PROXY_HOPS"] = configuration.trusted_proxy_hops
    values["STEWARDSHIP_TRUSTED_PROXY_NETWORKS"] = (
        (configuration.runtime_network.caddy + "/32",)
        if configuration.profile is DeploymentProfile.PRODUCTION
        else ()
    )
    values["STEWARDSHIP_INTERNAL_NETWORKS"] = (
        "127.0.0.0/8",
        "::1/128",
        configuration.runtime_network.backend,
    )
    values["MIDDLEWARE"] = [
        "parishkit.stewardship.runtime_health.HttpMetricsMiddleware",
        *values["MIDDLEWARE"],
    ]
    if configuration.profile is DeploymentProfile.DEVELOPMENT:
        # Keep template edits visible without enabling Django's private debug
        # pages or requiring Python module changes to wake the worker reloader.
        values["TEMPLATES"] = deepcopy(values["TEMPLATES"])
        for template in values["TEMPLATES"]:
            template["APP_DIRS"] = False
            template["OPTIONS"]["loaders"] = [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ]
    settings.configure(**values)
    django.setup()
    from django.db import connections

    from .accounts.auth_incidents import record_incident
    from .accounts.authentication import AuthRuntime
    from .accounts.authority import AuthorityStore
    from .accounts.configuration_installation import coherent_configuration
    from .accounts.configuration_schema import validate_sections
    from .accounts.family_authentication import FamilyRuntime
    from .accounts.limiting import Limiter
    from .runtime_grants import admit_download_database, admit_runtime_database

    admit_runtime_database(configuration)
    downloads = connections["download"]
    admit_download_database(configuration, downloads)
    from .campaigns.read_guards import DownloadPool, ReadLimits

    budget = configuration.runtime_budget
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(
        ReadLimits(
            download_seconds=budget.download_seconds,
            download_idle_seconds=budget.download_idle_seconds,
            drain_seconds=budget.drain_seconds,
            download_capacity=budget.download_capacity,
            process_pool_size=budget.download_pool_per_process,
        ),
        database=downloads.settings_dict,
    )
    store = AuthorityStore(configuration.paths["authority"], validate_sections)
    coherent_configuration(store)
    for name in ("reports", "media"):
        private_directory(configuration.paths[name])
    client = valkey_client(configuration)
    limiter_key = hmac.digest(
        rings["django_signing"].active.material,
        b"stewardship-auth-limiter-key-v1",
        hashlib.sha256,
    )
    limiter = Limiter(
        client,
        limiter_key,
        incident=record_incident,
        limits=configuration.authentication_limits,
    )
    limiter.check_health(force=True)
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(store, limiter)
    settings.STEWARDSHIP_FAMILY_RUNTIME = FamilyRuntime(
        store,
        limiter,
        rings["general_encryption"],
        rings["family_code_mac"],
        rings["token_public"],
    )
    settings.STEWARDSHIP_HEALTH_RUNTIME = RuntimeHealth(
        configuration, store, client, metrics_token
    )
    # Admission sockets are not idle worker reservations. Runtime opens fresh
    # thread-local connections as needed, bounded by the separately checked roles.
    connections.close_all()
    client.connection_pool.disconnect()
    settings.STEWARDSHIP_LOADED_CREDENTIAL_RECEIPTS = {
        name: credential_receipt(raw, name) for name, raw in loaded.items()
    }
    return settings.STEWARDSHIP_HEALTH_RUNTIME
