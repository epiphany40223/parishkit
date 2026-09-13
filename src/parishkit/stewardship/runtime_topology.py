"""Render concrete Compose mounts and identities from validated deployment paths.

The renderer is non-mutating. An operator command writes its documents only to
explicit private configuration targets. Images and process counts are concrete;
there are no compose-time secret substitutions, broad credential mounts or
readiness-based proxy removal. Later-phase workers remain explicitly pending.
"""

import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from parishkit.config import ConfigError

from .deployment import SECRET_NAMES, DeploymentProfile, ServiceRole
from .deployment_documents import deployment_document, service_configuration_file
from .offline_boundaries import offline_targets
from .runtime_paths import APPLICATION_GID, APPLICATION_UID, RuntimeLayout
from .service_boundaries import ALLOWED_SECRETS

POSTGRES_IMAGE = (
    "postgres:18.6-trixie@sha256:"
    "4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280"
)
VALKEY_IMAGE = (
    "valkey/valkey:9.1.2@sha256:"
    "c123e3715db63d06d4ad6964884037aa0d5d4d703939b9929954112889708e1d"
)
CADDY_IMAGE = (
    "caddy:2.11.4-alpine@sha256:"
    "5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
)


def bind(path, *, target=None, read_only=True):
    """Never let Compose implicitly create a misspelled host source directory."""
    return {
        "type": "bind",
        "source": str(path),
        "target": str(target or path),
        "read_only": read_only,
        "bind": {"create_host_path": False},
    }


def _image(value, profile):
    """Production must select an immutable repository-registry application digest."""
    if (
        type(value) is not str
        or (
            profile is DeploymentProfile.PRODUCTION
            and re.fullmatch(
                r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+/parishkit@sha256:[0-9a-f]{64}",
                value,
            )
            is None
        )
        or (
            profile is not DeploymentProfile.PRODUCTION
            and value != "parishkit-stewardship:development"
        )
    ):
        raise ConfigError("An approved immutable application image is required.")
    return value


def _service_config(configuration, role, *, target=None):
    """Each process sees only its credential references and independent SQL login."""
    from .runtime_grants import login_name

    layout = RuntimeLayout(configuration)
    name = "credential-installer-" + target.replace("_", "-") if target else role.value
    if role is ServiceRole.BOOTSTRAP:
        from .bootstrap import INITIAL_TARGETS

        names = {*INITIAL_TARGETS, "google_oauth"}
    else:
        names = {target} if target else ALLOWED_SECRETS.get(role, set())
    secrets = {key: layout.credential(key) for key in names}
    if target:
        secrets["handoff_private"] = layout.handoff(target)
    if role is ServiceRole.MIGRATION:
        database_user = "pk_stewardship_migration"
    elif role is ServiceRole.DATABASE_PROVISION:
        database_user = "pk_stewardship_operator"
    else:
        database_user = login_name(role, target=target)
    return replace(
        configuration,
        service_role=role,
        credential_target=target,
        secrets=secrets,
        configuration_file=service_configuration_file(configuration, name),
        postgres=replace(
            configuration.postgres,
            user=database_user,
            password_file=layout.database_password(
                "operator" if role is ServiceRole.DATABASE_PROVISION else name
            ),
            download_password_file=(
                configuration.postgres.download_password_file
                or layout.database_password("download")
            )
            if role is ServiceRole.WEB
            else None,
        ),
        valkey=replace(
            configuration.valkey,
            password_file=layout.valkey_password(role.value)
            if role is ServiceRole.WEB
            else None,
        ),
    )


def _application(image, budget):
    """One unprivileged process namespace; only declared mounts may be written."""
    return {
        "image": image,
        "user": f"{APPLICATION_UID}:{APPLICATION_GID}",
        "init": True,
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,mode=1777"],
        "environment": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"},
        "networks": {"backend": {}},
        "restart": "no",
        "stop_grace_period": str(budget.drain_seconds) + "s",
    }


def _online_mounts(configuration):
    """Authority and per-target installers never inherit each other's mounts."""
    role, layout = configuration.service_role, RuntimeLayout(configuration)
    result = [
        bind(configuration.configuration_file),
        bind(layout.interlock),
        bind(
            configuration.paths["authority"],
            read_only=role is not ServiceRole.CONFIG_INSTALLER,
        ),
        bind(configuration.postgres.password_file),
    ]
    for name, path in configuration.secrets.items():
        if name == configuration.credential_target:
            result.append(bind(path.parent, read_only=False))
        else:
            result.append(bind(path))
    if role is ServiceRole.WEB:
        if configuration.valkey.password_file is None:
            raise ConfigError("The web limiter needs its individual Valkey credential.")
        result += [
            bind(configuration.valkey.password_file),
            bind(configuration.postgres.download_password_file),
            *(
                bind(configuration.paths[name], read_only=False)
                for name in ("reports", "media")
            ),
        ]
    return result


def render_runtime(configuration, *, image, checkout=None):
    """Build one complete foundation topology, with independent offline profiles."""
    configuration = resolve_database_files(configuration)
    configuration = resolve_valkey_files(configuration)
    RuntimeLayout(configuration).validate()
    from .runtime_paths import admit_credential_directory

    for target in SECRET_NAMES - {"handoff_private"}:
        admit_credential_directory(
            RuntimeLayout(configuration).credential(target), allow_missing=True
        )
    if (
        configuration.postgres.host != "postgres"
        or configuration.postgres.port != 5432
        or configuration.valkey.host != "valkey"
        or configuration.valkey.port != 6379
    ):
        raise ConfigError("Compose requires internal database and broker addresses.")
    budget, network = configuration.runtime_budget, configuration.runtime_network
    if budget.replicas != 1:
        raise ConfigError("Operational runtime requires one web container.")
    targets = sorted(SECRET_NAMES - {"handoff_private"})
    # Reserve concrete SQL slots even before source handlers enable these
    # services: configuration/target installers + worker main/renewal + scheduler.
    budget.validate_topology(background_processes=1 + len(targets) + 3)
    image = _image(image, configuration.profile)
    if checkout is not None and (
        configuration.profile is not DeploymentProfile.DEVELOPMENT
        or not Path(checkout).is_absolute()
    ):
        raise ConfigError("Source mounts require an explicit development checkout.")
    services, documents = {}, {}
    roles = [(ServiceRole.WEB, None), (ServiceRole.CONFIG_INSTALLER, None)]
    roles += [(ServiceRole.CREDENTIAL_INSTALLER, target) for target in targets]
    roles += [
        (role, None)
        for role in (
            ServiceRole.BOOTSTRAP,
            ServiceRole.MIGRATION,
            ServiceRole.ADMIN_RECOVERY,
            ServiceRole.DATABASE_PROVISION,
        )
    ]
    for role, target in roles:
        selected = _service_config(configuration, role, target=target)
        name = selected.configuration_file.stem
        documents[selected.configuration_file] = deployment_document(selected)
        service = _application(image, budget)
        if role in {
            ServiceRole.BOOTSTRAP,
            ServiceRole.MIGRATION,
            ServiceRole.ADMIN_RECOVERY,
            ServiceRole.DATABASE_PROVISION,
        }:
            service["profiles"] = [name]
            service["command"] = [
                {
                    ServiceRole.BOOTSTRAP: "bootstrap",
                    ServiceRole.MIGRATION: "migrate",
                    ServiceRole.ADMIN_RECOVERY: "recover-admin",
                    ServiceRole.DATABASE_PROVISION: "database-roles",
                }[role],
                "--config",
                str(selected.configuration_file),
            ]
            service["volumes"] = [
                bind(path, read_only=ro)
                for path, ro in offline_targets(selected).items()
            ]
        else:
            service["command"] = [
                "runtime",
                "--config",
                str(selected.configuration_file),
            ]
            service["volumes"] = _online_mounts(selected)
            service["healthcheck"] = {
                "test": ["CMD", "pk-stewardship", "installer-healthcheck"],
                "interval": "10s",
                "timeout": "4s",
                "retries": 3,
            }
            if role is ServiceRole.WEB:
                service["networks"]["application-egress"] = {}
                service["healthcheck"] = {
                    "test": ["CMD", "pk-stewardship", "healthcheck"],
                    "interval": "10s",
                    "timeout": "4s",
                    "retries": 6,
                }
        if checkout is not None:
            service["volumes"].append(bind(Path(checkout) / "src", target="/app/src"))
        services[name] = service
    web = services.pop("web")
    for replica in range(budget.replicas):
        from copy import deepcopy

        selected = deepcopy(web)
        if configuration.profile is DeploymentProfile.PRODUCTION:
            selected["networks"]["proxy"] = {"ipv4_address": network.web(replica)}
        elif replica == 0:
            origin = urlsplit(configuration.public_origin)
            host = "[::1]" if origin.hostname == "::1" else "127.0.0.1"
            selected["ports"] = [f"{host}:{origin.port or 80}:8000"]
        services["web" if replica == 0 else f"web-{replica}"] = selected
    services.update(_infrastructure(configuration))
    if configuration.profile is DeploymentProfile.PRODUCTION:
        from .runtime_ingress import render_caddy

        services["caddy"] = _caddy(configuration)
        documents[RuntimeLayout(configuration).service_directory / "Caddyfile"] = (
            render_caddy(configuration)
        )
        for service in services.values():
            if "profiles" not in service:
                service["restart"] = "unless-stopped"
    return {
        "name": "parishkit-stewardship",
        "services": services,
        "networks": {
            "backend": {
                "internal": True,
                "ipam": {"config": [{"subnet": network.backend}]},
            },
            "proxy": {
                "internal": True,
                "ipam": {"config": [{"subnet": network.proxy}]},
            },
            "application-egress": {},
            "ingress": {},
        },
    }, documents


def resolve_database_files(configuration):
    """Preserve every individual override across rendering and role provisioning.

    Scalar password_file belongs to the input profile, not every generated role.
    Canonical per-identity references let the provisioning service read exactly
    the same files later mounted into independently authenticated consumers.
    """
    files = dict(configuration.postgres.password_files)
    name = configuration.service_role.value
    if configuration.service_role is ServiceRole.DATABASE_PROVISION:
        name = "operator"
    elif configuration.service_role is ServiceRole.CREDENTIAL_INSTALLER:
        name += "-" + configuration.credential_target.replace("_", "-")
    for identity, path in (
        (name, configuration.postgres.password_file),
        ("download", configuration.postgres.download_password_file),
    ):
        if path is not None:
            if identity in files and files[identity] != path:
                raise ConfigError("Database password override references disagree.")
            files[identity] = path
    return replace(
        configuration, postgres=replace(configuration.postgres, password_files=files)
    )


def resolve_valkey_files(configuration):
    """Preserve this profile's scalar override across per-service rendering."""
    from .deployment import VALKEY_IDENTITIES

    files = dict(configuration.valkey.password_files)
    name, scalar = configuration.service_role.value, configuration.valkey.password_file
    if scalar is not None:
        if name not in VALKEY_IDENTITIES:
            raise ConfigError("This service has no Valkey credential authority.")
        if name in files and files[name] != scalar:
            raise ConfigError("Valkey password override references disagree.")
        files[name] = scalar
    return replace(
        configuration, valkey=replace(configuration.valkey, password_files=files)
    )


def _infrastructure(configuration):
    """Stock images run without root/capabilities and retain only their own state."""
    layout = RuntimeLayout(configuration)
    shared = _application(POSTGRES_IMAGE, configuration.runtime_budget)
    shared.pop("environment")
    postgres = shared | {
        "image": POSTGRES_IMAGE,
        "environment": {
            "POSTGRES_USER": "pk_stewardship_operator",
            "POSTGRES_DB": configuration.postgres.name,
            "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password",
        },
        "volumes": [
            bind(
                configuration.paths["postgresql"],
                target="/var/lib/postgresql",
                read_only=False,
            ),
            bind(
                layout.database_password("operator"),
                target="/run/secrets/postgres-password",
            ),
        ],
        "tmpfs": [
            *shared["tmpfs"],
            "/var/run/postgresql:rw,nosuid,nodev,noexec,uid=10001,gid=10001,mode=0700",
        ],
        "command": [
            "postgres",
            "-c",
            f"max_connections={configuration.runtime_budget.database_connections}",
            "-c",
            "log_statement=none",
            "-c",
            "log_min_error_statement=panic",
            "-c",
            "log_parameter_max_length_on_error=0",
        ],
        "healthcheck": {
            "test": [
                "CMD",
                "pg_isready",
                "-U",
                "pk_stewardship_operator",
                "-d",
                configuration.postgres.name,
            ],
            "interval": "5s",
            "timeout": "3s",
            "retries": 12,
        },
    }
    valkey = shared | {
        "image": VALKEY_IMAGE,
        "healthcheck": {
            "test": [
                "CMD-SHELL",
                "valkey-cli ping | grep -qx 'NOAUTH Authentication required.'",
            ],
            "interval": "10s",
            "timeout": "3s",
            "retries": 3,
        },
        "command": [
            "valkey-server",
            "--appendonly",
            "yes",
            "--protected-mode",
            "yes",
            "--aclfile",
            "/run/secrets/valkey.acl",
        ],
        "volumes": [
            bind(configuration.paths["valkey"], target="/data", read_only=False),
            bind(
                configuration.paths["credentials"] / "valkey" / "server.acl",
                target="/run/secrets/valkey.acl",
            ),
        ],
    }
    return {"postgres": postgres, "valkey": valkey}


def _caddy(configuration):
    """The same stable read-only lifecycle inode covers the stock proxy process."""
    layout = RuntimeLayout(configuration)
    result = _application(CADDY_IMAGE, configuration.runtime_budget)
    result.update(
        # Stock Caddy carries a NET_BIND_SERVICE file capability. Linux refuses
        # exec when that capability is absent from the bounding set, even when
        # this deployment uses high internal ports. Retain only that capability.
        cap_add=["NET_BIND_SERVICE"],
        healthcheck={
            "test": [
                "CMD-SHELL",
                "nc -z -w 2 127.0.0.1 8080 && nc -z -w 2 127.0.0.1 8443",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
        entrypoint=["/bin/sh", "-c"],
        command=[
            "exec 9</run/stewardship/startup.lock; "
            "flock -sn 9 || { echo 'Offline maintenance prevents ingress startup.' "
            ">&2; exit 1; }; "
            "exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile"
        ],
        ports=["80:8080", "443:8443"],
        networks={
            "proxy": {"ipv4_address": configuration.runtime_network.caddy},
            "ingress": {},
        },
        volumes=[
            bind(layout.interlock, target="/run/stewardship/startup.lock"),
            bind(layout.service_directory / "Caddyfile", target="/etc/caddy/Caddyfile"),
            bind(configuration.paths["cache"] / "static", target="/srv/static"),
            bind(
                configuration.paths["caddy"] / "data", target="/data", read_only=False
            ),
            bind(
                configuration.paths["caddy"] / "config",
                target="/config",
                read_only=False,
            ),
        ],
    )
    return result
