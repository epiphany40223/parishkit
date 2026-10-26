"""Canonical non-secret deployment documents for offline and per-service mounts."""

from dataclasses import asdict, fields
from pathlib import Path

from .reports.export_window import DEFAULT_MINUTES


def deployment_document(configuration, *, switches=True):
    """Serialize validated metadata only; credential values are never opened.

    ``switches=False`` leaves out the operational switches that are not
    deployment inputs (the bulk Family send, #430, the local rehearsal's
    SMTP latency, BG-12, the export download window, #1398, the
    ParishSoft base URL, #1399, and the mail redirect ``mail_redirect``,
    #1399): the provisioning record
    keeps only inputs, so turning a switch on or off is never an input
    change, and an earlier release can still read the record.
    """
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
        # Every service loads its rendered document, not the operator's YAML,
        # so an omitted section silently reverts to its defaults there.
        "operational_alerts": asdict(configuration.operational_alerts),
        "family_mail_transport": configuration.family_mail_transport,
        "mail_consumers": configuration.mail_consumers,
    }
    if configuration.bulk_family_send and switches:
        # Written only while the bulk Family send (#430) is on, so a
        # deployment with it off renders exactly what the release before it
        # did, and that release can load every document (rollback).
        result["bulk_family_send"] = True
        result["bulk_send_batch"] = configuration.bulk_send_batch
    if configuration.local_smtp_latency_ms and switches:
        # Likewise written only when set, which the loader admits only in
        # the local profile (BG-12's rehearsal latency): every other
        # deployment renders exactly what it rendered before the setting.
        result["local_smtp_latency_ms"] = configuration.local_smtp_latency_ms
    if configuration.parishsoft_publication and switches:
        # Likewise written only while census publication (#528, BG-09) is
        # on, so a deployment with it off renders what it rendered before.
        result["parishsoft_publication"] = True
    if configuration.export_download_window_minutes != DEFAULT_MINUTES and switches:
        # An operational switch like the bulk send's (#1398): it can change
        # after provisioning (the upgrade script carries it over), and a
        # shorter window applies at once. Written only when it is not the
        # default, so a default deployment renders exactly what the release
        # before it did, and that release can load every document.
        result["export_download_window_minutes"] = (
            configuration.export_download_window_minutes
        )
    if configuration.parishsoft_base_url is not None and switches:
        # Likewise written only when it is not the profile's default, which
        # only a LOCAL deployment that chose the real API has (#1399).
        result["parishsoft_base_url"] = configuration.parishsoft_base_url
    if configuration.mail_redirect is not None and switches:
        # Likewise written only when set (local profile only, #1399).
        result["mail_redirect"] = configuration.mail_redirect
    if configuration.credential_target is not None:
        result["credential_target"] = configuration.credential_target
    return {"deployment": result}


def service_configuration_file(configuration, name):
    """Use only a closed service identity to select an individual config file."""
    from parishkit.config import ConfigError

    from .deployment import SECRET_NAMES, ServiceRole

    names = (
        {role.value for role in ServiceRole}
        | {
            # No longer rendered since #2228 (the initial topology is the
            # configured one), but still admitted so a document an earlier
            # release wrote keeps loading.
            "worker-initial",
            "mail-dispatch-initial",
            "worker-slack",
        }
        | {
            "credential-installer-" + target.replace("_", "-")
            for target in SECRET_NAMES - {"handoff_private"}
        }
    )
    if name not in names:
        raise ConfigError("Unknown operational service identity.")
    return Path(configuration.paths["config"]) / "services" / (name + ".yaml")
