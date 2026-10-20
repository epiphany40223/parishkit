"""Validated web-process assembly; no settings import or flag bypasses admission."""

import hashlib
import hmac
import json
import os
from copy import deepcopy
from importlib import import_module

from parishkit.config import ConfigError

from .accounts.cryptography import independent_keyrings
from .accounts.key_files import _unique_object, parse_keyring, read_private
from .accounts.metrics_credentials import MetricsCredential, credential_receipt
from .deployment import DeploymentProfile, ServiceRole
from .mail_catcher import fake_clock
from .runtime_database import database_settings, profile_settings
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


# The LOCAL ``google_oauth`` sentinel (#476). It has the client document's
# shape but no real client ID or secret. ``up`` installs it, so LOCAL starts
# with no Google sign-in and the Admin signs in with the local test sign-in
# (OPS-10.08). LOCAL also admits a real client, a separate one registered for
# the localhost origin only (#1399), but every other profile refuses the
# sentinel, so it can never reach Production.
LOCAL_OAUTH_SENTINEL = {
    "client_id": "local-environment-sentinel.invalid",
    "client_secret": "local-environment-sentinel-not-a-secret",
}
LOCAL_OAUTH_DOCUMENT = json.dumps(LOCAL_OAUTH_SENTINEL).encode() + b"\n"


def parse_google_client(raw, *, profile):
    """Parse the operator's OAuth client document; never reuse Workspace credentials.

    The compact operator file has only client_id/client_secret. The application
    never returns this document in diagnostics or persists a SocialApp secret.
    Ownership of the Google account itself is established only during Google
    login. ``profile`` applies the sentinel rule: every profile but LOCAL
    refuses ``LOCAL_OAUTH_SENTINEL``. LOCAL admits it or a real client (#1399);
    ``admit_google_clock`` then keeps a real client off the fake clock.
    """
    if not isinstance(profile, DeploymentProfile):
        raise ConfigError("The OAuth client check requires a deployment profile.")
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
    except (ValueError, UnicodeError, RecursionError):
        raise ConfigError("The OAuth client file is invalid.") from None
    if profile is not DeploymentProfile.LOCAL and value == LOCAL_OAUTH_SENTINEL:
        raise ConfigError(
            "The sentinel OAuth client is admitted only in the local profile."
        )
    return value


class GoogleClockRefused(ConfigError):
    """A real Google client refused under the fake clock (#1399).

    Its message is fixed text that names the cause and holds nothing from the
    client document, so web start and the smoke check show it as it is
    instead of their generic failure lines.
    """


def admit_google_clock(oauth, environ=os.environ):
    """Refuse a real Google client on the fake clock (#1399).

    Google sign-in checks the ID token's ``iat`` and ``exp`` and Google's TLS
    certificates against this process's clock. The fake clock runs up to
    weeks behind real time, so every token would look issued in the future
    and a certificate's dates would be checked against a past day. A real
    client therefore needs the normal clock; the sentinel, which never
    reaches Google, runs under either. Inert outside LOCAL, which never runs
    under libfaketime.
    """
    if oauth != LOCAL_OAUTH_SENTINEL and fake_clock(environ):
        raise GoogleClockRefused(
            "A real Google OAuth client needs the normal clock: this deployment "
            "runs under the fake clock, which Google's token and certificate "
            "checks cannot use. Reinstall the sentinel OAuth client, or install "
            "the real one on a normal-clock (seeded) deployment."
        )


def valkey_client(configuration, *, telemetry=False):
    """Create a finite authenticated pool; never put a password in a broker URL."""
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

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
        socket_connect_timeout=0.05 if telemetry else 3,
        socket_timeout=0.05 if telemetry else 3,
        max_connections=configuration.runtime_budget.web_threads + 3,
        retry_on_timeout=False,
        retry=Retry(NoBackoff(), 0),
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


def trusted_proxy_networks(configuration):
    """Only the deployment's own Caddy address may forward a client identity.

    Production and LOCAL sit behind that one hop; development and test have no
    proxy at all, so no peer is trusted to forward anything.
    """
    if configuration.profile.behind_proxy:
        return (configuration.runtime_network.caddy + "/32",)
    return ()


def template_settings(templates, configuration):
    """Development alone reloads templates from disk on every request.

    Template edits then show without Django's private debug pages or a Python
    change to wake the worker reloader. Every other profile, LOCAL included,
    keeps the cached loaders of the given settings, which are returned as is.
    """
    if configuration.profile is DeploymentProfile.DEVELOPMENT:
        templates = deepcopy(templates)
        for template in templates:
            template["APP_DIRS"] = False
            template["OPTIONS"]["loaders"] = [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ]
    return templates


def url_configuration(profile):
    """Select the URL module: LOCAL alone adds the test sign-in route (#476).

    ``local_urls`` carries every production route plus that one; ``urls.py``
    never imports it, so under any other profile the route does not exist.
    """
    if profile is DeploymentProfile.LOCAL:
        return "parishkit.stewardship.local_urls"
    return "parishkit.stewardship.urls"


def configure_web(configuration):
    """Admit mounts, key purposes, SQL roles and coherent authority before serving."""
    from django.conf import settings

    if settings.configured:
        raise ConfigError("Operational web startup requires a fresh process.")
    if admit_online_service(configuration) is not ServiceRole.WEB:
        raise ConfigError("Web startup requires its isolated service profile.")
    admit_lifecycle_mounts(configuration)
    return configure_web_runtime(configuration)


def configure_web_runtime(configuration):
    """Everything ``configure_web`` does after the mount admission.

    The web service calls this through ``configure_web``. The LOCAL seeder
    (#476) calls it directly from a one-shot container that runs under the
    web identity with web's mounts plus the writable clock directory, which
    the online mount policy would refuse; the key, SQL-role and coherent-
    authority admissions below still apply in full.
    """
    import django
    from django.conf import settings

    if settings.configured:
        raise ConfigError("Operational web startup requires a fresh process.")
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
    oauth = parse_google_client(loaded["google_oauth"], profile=configuration.profile)
    admit_google_clock(oauth)
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
    # Whether Google sign-in can work here: False only for LOCAL's sentinel,
    # whose sign-in pages then offer the test sign-in alone
    # (accounts.branding_context.local_environment).
    values["STEWARDSHIP_GOOGLE_SIGN_IN"] = oauth != LOCAL_OAUTH_SENTINEL
    values["STEWARDSHIP_PROXY_HOPS"] = configuration.trusted_proxy_hops
    values["STEWARDSHIP_OPERATIONAL_POLICY"] = configuration.operational_alerts
    values["STEWARDSHIP_TRUSTED_PROXY_NETWORKS"] = trusted_proxy_networks(configuration)
    values["STEWARDSHIP_INTERNAL_NETWORKS"] = (
        "127.0.0.0/8",
        "::1/128",
        configuration.runtime_network.backend,
    )
    values["MIDDLEWARE"] = [
        "parishkit.stewardship.runtime_health.HttpMetricsMiddleware",
        *values["MIDDLEWARE"],
    ]
    values["TEMPLATES"] = template_settings(values["TEMPLATES"], configuration)
    values.update(profile_settings(configuration))
    values["ROOT_URLCONF"] = url_configuration(configuration.profile)
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
    from .accounts.setup_completion import setup_is_complete
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
    try:
        runtime = coherent_configuration(store)
    except ConfigError:
        from .accounts.setup_startup import initial_setup_hold

        # Keep the real store for every request. Only the existing original-
        # login cancellation route can use predecessor policy during this hold.
        runtime = initial_setup_hold(store)
    from .accounts.users_schema import require_users_form

    # Once users ship, never serve sign-ins from unconverted rules (#952).
    require_users_form(runtime)
    for name in ("reports", "media"):
        private_directory(configuration.paths[name])
    settings.STEWARDSHIP_MEDIA_ROOT = configuration.paths["media"]
    settings.STEWARDSHIP_REPORTS_ROOT = configuration.paths["reports"]
    settings.STEWARDSHIP_PUBLIC_ORIGIN = configuration.public_origin
    # Publish to ParishSoft (#528) offers its check only while the web's own
    # parishsoft_publication setting is on; the worker obeys its own copy.
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = configuration.parishsoft_publication
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
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(store, limiter, setup_is_complete)
    settings.STEWARDSHIP_FAMILY_RUNTIME = FamilyRuntime(
        store,
        limiter,
        rings["general_encryption"],
        rings["family_code_mac"],
        rings["token_public"],
    )
    settings.STEWARDSHIP_HEALTH_RUNTIME = RuntimeHealth(
        configuration,
        store,
        client,
        metrics_token,
        telemetry_client=valkey_client(configuration, telemetry=True),
    )
    # Admission sockets are not idle worker reservations. Runtime opens fresh
    # thread-local connections as needed, bounded by the separately checked roles.
    connections.close_all()
    client.connection_pool.disconnect()
    settings.STEWARDSHIP_LOADED_CREDENTIAL_RECEIPTS = {
        name: credential_receipt(raw, name) for name, raw in loaded.items()
    }
    return settings.STEWARDSHIP_HEALTH_RUNTIME
