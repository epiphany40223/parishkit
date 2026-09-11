"""Local operator diagnostics expose check names, never private exception values."""

import json
import sys

from parishkit.config import ConfigError

from .deployment import ServiceRole, load_deployment
from .runtime_paths import RuntimeLayout
from .startup_interlock import StartupLease


def health_command(configuration):
    """Inspect dependencies inside an admitted web container without serving HTTP.

    Diagnosis uses the web's own restricted SQL login and exact read-only secret
    mounts. It acquires a shared lifecycle lease, so an offline operator process
    cannot race its schema/configuration reads. No external provider is contacted.
    """
    from django.db import connections

    from .accounts.authority import AuthorityStore
    from .accounts.configuration_schema import validate_sections
    from .accounts.key_files import read_private
    from .accounts.metrics_credentials import MetricsCredential
    from .operator_commands import configure_operator_database
    from .runtime_grants import admit_runtime_database
    from .runtime_health import RuntimeHealth
    from .runtime_web import admit_lifecycle_mounts, valkey_client
    from .service_boundaries import admit_online_service

    if admit_online_service(configuration) is not ServiceRole.WEB:
        raise ConfigError("Detailed health requires the admitted web profile.")
    admit_lifecycle_mounts(configuration)
    with StartupLease(RuntimeLayout(configuration).interlock, offline=False):
        configure_operator_database(configuration)
        client = valkey_client(configuration)
        try:
            runtime = RuntimeHealth(
                configuration,
                AuthorityStore(configuration.paths["authority"], validate_sections),
                client,
                MetricsCredential.parse(
                    read_private(configuration.secrets["metrics"])
                ).token,
            )
            result = runtime.checks()
            try:
                admit_runtime_database(configuration)
            except Exception:
                result["database_authority"] = False
            else:
                result["database_authority"] = True
            return result
        finally:
            connections.close_all()
            client.connection_pool.disconnect()


def execute_health(args):
    """Only fixed check identifiers and booleans leave this protected CLI boundary."""
    try:
        if args.config is None:
            raise ConfigError("Detailed health requires an explicit configuration.")
        checks = health_command(load_deployment(args.config))
    except Exception:
        print(
            "ERROR: diagnostics unavailable; verify web profile, mount permissions "
            "and credential files",
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"checks": checks, "ready": all(checks.values())}, sort_keys=True))
    return 0 if all(checks.values()) else 1
