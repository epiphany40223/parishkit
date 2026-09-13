"""Canonical non-secret deployment documents for offline and per-service mounts."""

from dataclasses import asdict, fields
from pathlib import Path


def deployment_document(configuration):
    """Serialize validated metadata only; credential values are never opened."""
    postgres = {
        item.name: getattr(configuration.postgres, item.name)
        for item in fields(configuration.postgres)
    }
    valkey = {
        item.name: getattr(configuration.valkey, item.name)
        for item in fields(configuration.valkey)
    }
    postgres["password_files"] = {
        name: str(path) for name, path in configuration.postgres.password_files.items()
    }
    for values in (postgres, valkey):
        values["password_files"] = {
            name: str(path) for name, path in values["password_files"].items()
        }
        for key in tuple(values):
            if key.endswith("password_file"):
                path = values[key]
                if path is None:
                    values.pop(key)
                else:
                    values[key] = str(path)
    result = {
        "schema_version": 1,
        "profile": str(configuration.profile),
        "service_role": str(configuration.service_role),
        "public_origin": configuration.public_origin,
        "trusted_proxy_hops": configuration.trusted_proxy_hops,
        "paths": {"root": str(configuration.paths.root)}
        | {name: str(path) for name, path in configuration.paths.values.items()},
        "postgres": postgres,
        "valkey": valkey,
        "secrets": {name: str(path) for name, path in configuration.secrets.items()},
        "authentication_limits": asdict(configuration.authentication_limits),
        "runtime_budget": asdict(configuration.runtime_budget),
        "runtime_network": asdict(configuration.runtime_network),
    }
    if configuration.credential_target is not None:
        result["credential_target"] = configuration.credential_target
    return {"deployment": result}


def service_configuration_file(configuration, name):
    """Use only a closed service identity to select an individual config file."""
    from parishkit.config import ConfigError

    from .deployment import SECRET_NAMES, ServiceRole

    names = {role.value for role in ServiceRole} | {
        "credential-installer-" + target.replace("_", "-")
        for target in SECRET_NAMES - {"handoff_private"}
    }
    if name not in names:
        raise ConfigError("Unknown operational service identity.")
    return Path(configuration.paths["config"]) / "services" / (name + ".yaml")
