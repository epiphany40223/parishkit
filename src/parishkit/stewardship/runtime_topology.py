"""Render concrete Compose mounts and identities from validated deployment paths.

The renderer is non-mutating. An operator command writes its documents only to
explicit private configuration targets. Images and process counts are concrete;
there are no compose-time secret substitutions, broad credential mounts or
readiness-based proxy removal. Only the compiled source task registry is enabled;
later-phase delivery, publication and backup workers remain explicitly pending.
"""

import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from parishkit.config import ConfigError

from .deployment import (
    BULK_FAMILY_SEND_VARIABLE,
    BULK_SEND_BATCH_VARIABLE,
    FAMILY_MAIL_TRANSPORT_VARIABLE,
    MAIL_CONSUMERS_VARIABLE,
    SECRET_NAMES,
    DeploymentProfile,
    ServiceRole,
)
from .deployment_documents import deployment_document, service_configuration_file
from .observability import DEBUG_LOGGING_VARIABLE
from .offline_boundaries import offline_targets
from .runtime_paths import APPLICATION_GID, APPLICATION_UID, RuntimeLayout
from .service_boundaries import ALLOWED_SECRETS, rotating_directories
from .source.loading import DROP_OVERRIDE_VARIABLE

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
# The LOCAL mail catcher (#476), pinned by its multi-arch index digest like the
# other third-party images. Never rendered for any other profile.
MAILPIT_IMAGE = (
    "axllent/mailpit:v1.31.4@sha256:"
    "b68349e3a014b90c5610bfb26b2ae36f3892d7b8cf25ee140c6c71c98d2fcf48"
)
# Mailpit keeps at most this many messages; a seeded local campaign sends a
# few hundred, so months of local use fit without pruning anything a developer
# is looking at.
MAILPIT_MAX_MESSAGES = 50_000

# Docker's default json-file driver keeps a container's log forever, so one
# noisy service could fill the host's disk during a month-long campaign.
# Every service, the one-shot profiles included, keeps at most five 10 MB
# files (50 MB); about eighteen long-running services then use under 1 GB.
# Docker applies it when a container is created, so the next retarget-image
# and `up` recreate each container with the cap. Durable evidence lives in
# the database, not in these logs.
CONTAINER_LOGGING = {
    "driver": "json-file",
    "options": {"max-size": "10m", "max-file": "5"},
}


def bind(path, *, target=None, read_only=True):
    """Never let Compose implicitly create a misspelled host source directory."""
    return {
        "type": "bind",
        "source": str(path),
        "target": str(target or path),
        "read_only": read_only,
        "bind": {"create_host_path": False},
    }


# Each check starts a new Python process that imports the application, about
# a CPU-second. Eighteen services checked every 10 seconds kept roughly three
# of a 4-vCPU host's cores busy, so steady state checks once a minute. During
# start_period Docker checks every start_interval instead, so `up --wait`
# still sees a started service healthy within seconds. The timeout allows for
# a loaded host; a slow import is not a failed service.
# Standard-library-only probes: see parishkit.stewardship.probe.
PROBE = ["CMD", "python", "-m", "parishkit.stewardship.probe"]
PYTHON_HEALTHCHECK = {
    "interval": "60s",
    "timeout": "15s",
    "start_period": "120s",
    "start_interval": "2s",
}


PRODUCTION_IMAGE_PATTERN = (
    r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+/stewardship@sha256:[0-9a-f]{64}"
)
# The LOCAL build tag (#476): the checkout's commit, "-dirty" when it has
# uncommitted edits, and the build time in Unix seconds. Production rejects
# it, and LOCAL rejects GHCR references and the development tag.
LOCAL_IMAGE_PATTERN = r"parishkit-stewardship-local:[0-9a-f]{40}(-dirty)?-[0-9]{10}"
# The fake-clock image derived from a local build (local.clock.faketime_image);
# LOCAL admits it too, so a deployment can be re-rendered from either tag.
LOCAL_FAKETIME_IMAGE_PATTERN = (
    r"parishkit-stewardship-local-faketime-stewardship:[0-9a-f]{40}(-dirty)?-[0-9]{10}"
)
DEVELOPMENT_IMAGE = "parishkit-stewardship:development"


def _image(value, profile):
    """Each profile admits exactly one image shape; production's is a GHCR digest."""
    if type(value) is str:
        if profile is DeploymentProfile.PRODUCTION:
            admitted = re.fullmatch(PRODUCTION_IMAGE_PATTERN, value) is not None
        elif profile is DeploymentProfile.LOCAL:
            admitted = (
                re.fullmatch(LOCAL_IMAGE_PATTERN, value) is not None
                or re.fullmatch(LOCAL_FAKETIME_IMAGE_PATTERN, value) is not None
            )
        else:
            admitted = value == DEVELOPMENT_IMAGE
        if admitted:
            return value
    raise ConfigError("An approved immutable application image is required.")


def _service_config(configuration, role, *, target=None, provider_mode="configured"):
    """Each process sees only its credential references and independent SQL login."""
    from .runtime_grants import login_name

    layout = RuntimeLayout(configuration)
    name = "credential-installer-" + target.replace("_", "-") if target else role.value
    if role is ServiceRole.BOOTSTRAP:
        from .bootstrap import INITIAL_TARGETS

        names = {*INITIAL_TARGETS, "google_oauth"}
    elif role is ServiceRole.BACKUP_WORKER:
        # The v1 backup seals to the operator's public key and transfers
        # nothing itself, so the off-host target credential stays uninstalled.
        names = {"backup_data"}
    else:
        names = {target} if target else ALLOWED_SECRETS.get(role, set())
    secrets = {key: layout.credential(key) for key in names}
    document_name = name
    if role in {ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH}:
        # A configured worker always mounts Slack's read-only credential
        # directory, even before Slack is set up, so an Administrator can add
        # or replace Slack later with no compose switch. "configured-slack" is
        # kept for existing runbooks and renders the same mounts.
        if provider_mode == "initial":
            secrets.pop("slack", None)
            secrets.pop("parishsoft", None)
            secrets.pop("google_workspace", None)
            document_name += "-initial"
        elif provider_mode == "configured-slack" and role is ServiceRole.WORKER:
            document_name += "-slack"
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
        configuration_file=service_configuration_file(configuration, document_name),
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
            if role
            in {
                ServiceRole.WEB,
                ServiceRole.WORKER,
                ServiceRole.SCHEDULER,
                ServiceRole.MAIL_DISPATCH,
            }
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
        "environment": {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            # Off unless the operator's shell exports it when running Compose;
            # see observability.debug_logging_enabled. Pre-launch debugging only.
            DEBUG_LOGGING_VARIABLE: "${" + DEBUG_LOGGING_VARIABLE + ":-0}",
            # Empty unless the operator's shell exports it to accept one known
            # large source change; see source.loading.maximum_drop_percent.
            DROP_OVERRIDE_VARIABLE: "${" + DROP_OVERRIDE_VARIABLE + ":-}",
        },
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
    rotating = rotating_directories(configuration)
    for name, path in configuration.secrets.items():
        if name == configuration.credential_target:
            result.append(bind(path.parent, read_only=False))
        elif name in rotating:
            # The installer renames a replacement into place; a directory
            # mount lets this running consumer see it without recreation.
            result.append(bind(rotating[name]))
        else:
            result.append(bind(path))
    if role in {
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
    }:
        if configuration.valkey.password_file is None:
            raise ConfigError("The service needs its individual Valkey credential.")
        result.append(bind(configuration.valkey.password_file))
    if role is ServiceRole.WEB:
        result += [
            bind(configuration.postgres.download_password_file),
            *(
                bind(configuration.paths[name], read_only=False)
                for name in ("reports", "media")
            ),
        ]
    if role is ServiceRole.WORKER:
        # The worker renders report exports into the reports root, which the
        # web service then streams back to the requesting Admin.
        result += [
            bind(configuration.paths[name], read_only=False)
            for name in ("reports", "media")
        ]
    return result


def render_runtime(configuration, *, image, checkout=None, provider_mode="configured"):
    """Build one complete foundation topology, with independent offline profiles."""
    if type(provider_mode) is not str or provider_mode not in {
        "initial",
        "configured",
        "configured-slack",
    }:
        raise ConfigError("Unknown runtime provider mount mode.")
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
    # Configuration/target installers + worker/mail main/renewal + scheduler each
    # retain their own reserved SQL slots, independent of interactive headroom.
    # The worker and mail-dispatch logins' limits are rollout_overlap * 3 (two
    # consumer processes each, #336 and the mail consumers), one slot more per
    # overlap than counted here. Compose never runs two generations of a
    # background service at once, so in steady state these logins hold at
    # most 13 installer + 6 worker + 6 mail + 1 scheduler = 26 connections,
    # well inside the 36 this check reserves for them.
    budget.validate_topology(background_processes=1 + len(targets) + 5)
    image = _image(image, configuration.profile)
    # Deliberately `is not DEVELOPMENT`: LOCAL and test are refused here too.
    if checkout is not None and (
        configuration.profile is not DeploymentProfile.DEVELOPMENT
        or not Path(checkout).is_absolute()
    ):
        raise ConfigError("Source mounts require an explicit development checkout.")
    services, documents = {}, {}
    roles = [
        (ServiceRole.WEB, None),
        (ServiceRole.CONFIG_INSTALLER, None),
        (ServiceRole.WORKER, None),
        (ServiceRole.MAIL_DISPATCH, None),
        (ServiceRole.SCHEDULER, None),
    ]
    roles += [(ServiceRole.CREDENTIAL_INSTALLER, target) for target in targets]
    roles += [
        (role, None)
        for role in (
            ServiceRole.BOOTSTRAP,
            ServiceRole.MIGRATION,
            ServiceRole.ADMIN_RECOVERY,
            ServiceRole.DATABASE_PROVISION,
            ServiceRole.BACKUP_WORKER,
        )
    ]
    for role, target in roles:
        selected = _service_config(
            configuration, role, target=target, provider_mode=provider_mode
        )
        # All modes describe the same Compose service identities. Only the
        # individual configuration and provider mounts change on recreation.
        name = (
            "credential-installer-" + target.replace("_", "-") if target else role.value
        )
        documents[selected.configuration_file] = deployment_document(selected)
        service = _application(image, budget)
        if role is ServiceRole.MAIL_DISPATCH:
            # Empty unless the operator's shell exports it when running
            # Compose: "per_message" falls back from batched Family mail
            # (#284) on this service's next recreation.
            service["environment"][FAMILY_MAIL_TRANSPORT_VARIABLE] = (
                "${" + FAMILY_MAIL_TRANSPORT_VARIABLE + ":-}"
            )
            # Likewise "1" falls back from two mail consumer processes to one.
            service["environment"][MAIL_CONSUMERS_VARIABLE] = (
                "${" + MAIL_CONSUMERS_VARIABLE + ":-}"
            )
        if configuration.bulk_family_send and role in {
            ServiceRole.SCHEDULER,
            ServiceRole.WORKER,
            ServiceRole.MAIL_DISPATCH,
        }:
            # Only while the bulk Family send (#430) was rendered on, so a
            # deployment with it off renders exactly what the release before
            # it did: "0" exported for one recreation turns it off on these
            # services without re-rendering (empty keeps the rendered value).
            service["environment"][BULK_FAMILY_SEND_VARIABLE] = (
                "${" + BULK_FAMILY_SEND_VARIABLE + ":-}"
            )
            if role is ServiceRole.MAIL_DISPATCH:
                service["environment"][BULK_SEND_BATCH_VARIABLE] = (
                    "${" + BULK_SEND_BATCH_VARIABLE + ":-}"
                )
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
        elif role is ServiceRole.BACKUP_WORKER:
            from .backup_boundaries import backup_targets

            # One-shot like the offline profiles, but it runs beside the online
            # services under the shared interlock and reads the runtime trees.
            service["profiles"] = [name]
            service["command"] = [
                "backup",
                "--config",
                str(selected.configuration_file),
            ]
            service["volumes"] = [
                bind(path, read_only=ro)
                for path, ro in backup_targets(selected).items()
            ]
            # The off-site copy uploads the sealed set to Google Drive.
            _join_egress(service, configuration)
        else:
            service["command"] = [
                "runtime",
                "--config",
                str(selected.configuration_file),
            ]
            service["volumes"] = _online_mounts(selected)
            service["healthcheck"] = {
                "test": PROBE + ["installer"],
                **PYTHON_HEALTHCHECK,
                "retries": 3,
            }
            if role in {
                ServiceRole.WEB,
                ServiceRole.WORKER,
                ServiceRole.MAIL_DISPATCH,
            } or (
                role is ServiceRole.CREDENTIAL_INSTALLER
                and target in {"parishsoft", "google_workspace", "slack"}
            ):
                _join_egress(service, configuration)
            if role is ServiceRole.WEB:
                service["healthcheck"] = {
                    "test": PROBE + ["web"],
                    **PYTHON_HEALTHCHECK,
                    "retries": 3,
                }
        if checkout is not None:
            service["volumes"].append(bind(Path(checkout) / "src", target="/app/src"))
        services[name] = service
    web = services.pop("web")
    for replica in range(budget.replicas):
        selected = deepcopy(web)
        if configuration.profile.behind_proxy:
            selected["networks"]["proxy"] = {"ipv4_address": network.web(replica)}
        elif replica == 0:
            origin = urlsplit(configuration.public_origin)
            host = "[::1]" if origin.hostname == "::1" else "127.0.0.1"
            selected["ports"] = [f"{host}:{origin.port or 80}:8000"]
        services["web" if replica == 0 else f"web-{replica}"] = selected
    services.update(_infrastructure(configuration))
    if configuration.profile is DeploymentProfile.LOCAL:
        services["mailpit"] = _mailpit(configuration)
        services["fake-parishsoft"] = _fake_parishsoft(configuration, image)
    if configuration.profile.behind_proxy:
        from .runtime_ingress import render_ingress

        services["caddy"] = _caddy(configuration)
        documents[RuntimeLayout(configuration).service_directory / "Caddyfile"] = (
            render_ingress(configuration)
        )
        for service in services.values():
            if "profiles" not in service:
                service["restart"] = "unless-stopped"
    for service in services.values():
        service["logging"] = deepcopy(CONTAINER_LOGGING)
    return {
        "name": _project_name(configuration),
        "services": services,
        "networks": _networks(configuration),
    }, documents


def _join_egress(service, configuration):
    """Give a service its route out; LOCAL has none, so it stays internal-only.

    LOCAL (#476) omits the ``application-egress`` network entirely: only the
    ingress services (Caddy, and the mail catcher's UI) sit on a non-internal
    network, and that one does no source NAT, so even a mistaken real
    credential has no route to Google, ParishSoft or Slack.
    """
    if configuration.profile is not DeploymentProfile.LOCAL:
        service["networks"]["application-egress"] = {}


def render_faketime_override(configuration, compose):
    """The LOCAL fake-clock Compose override (#476, "Fake clock").

    Rendered beside the three topologies as ``compose.faketime.json`` and
    applied by the operator script as a second ``-f`` file when the clock-mode
    marker says ``fake``. For every service that runs the application image,
    and for ``postgres`` and ``valkey``, it swaps in the libfaketime-derived
    local image, sets the four libfaketime variables and adds exactly one
    mount: the clock directory, read-only, at the target the mount policy
    admits only for LOCAL. Caddy, Mailpit and the fake ParishSoft are left on
    real time. Compose merges ``environment`` by key and ``volumes`` by target,
    so the base topology is otherwise unchanged; normal mode is the base
    topology alone. Every other profile is refused: libfaketime is never
    rendered, and so never loaded, outside LOCAL.
    """
    from .local.clock import (
        CLOCK_MOUNT_TARGET,
        FAKE_PARISHSOFT_SERVICE,
        FAKETIME_ENVIRONMENT,
        FAKETIME_STORES,
        clock_directory,
        faketime_image,
    )

    if configuration.profile is not DeploymentProfile.LOCAL:
        raise ConfigError("The fake clock is rendered only for the local profile.")
    application = _image(compose["services"]["web"]["image"], configuration.profile)
    services = {}
    for name, service in compose["services"].items():
        if service["image"] != application and name not in FAKETIME_STORES:
            continue
        if name == FAKE_PARISHSOFT_SERVICE:
            # Runs the application image but stays on real time: it compares
            # release_at against the real clock (specification, "Not faked").
            continue
        services[name] = {
            "image": faketime_image(service["image"]),
            "environment": dict(FAKETIME_ENVIRONMENT),
            "volumes": [
                bind(clock_directory(configuration), target=CLOCK_MOUNT_TARGET)
            ],
        }
    return {"services": services}


def _project_name(configuration):
    """Production's Compose project keeps its name; LOCAL is ``parishkit-local``."""
    if configuration.profile is DeploymentProfile.LOCAL:
        return "parishkit-local"
    return "parishkit-stewardship"


def _networks(configuration):
    """The Compose networks: two internal everywhere; egress and ingress by profile.

    ``backend`` and ``proxy`` are internal in every profile. Outbound traffic
    leaves through the non-internal ``application-egress`` network, and Caddy
    publishes through ``ingress``. LOCAL has no ``application-egress`` and
    creates ``ingress`` without IP masquerade: Docker still forwards inbound
    connections to the published loopback ports, but a container on
    ``ingress`` gets no source NAT and so no route to the internet.
    """
    network = configuration.runtime_network
    result = {
        "backend": {
            "internal": True,
            "ipam": {"config": [{"subnet": network.backend}]},
        },
        "proxy": {
            "internal": True,
            "ipam": {"config": [{"subnet": network.proxy}]},
        },
    }
    if configuration.profile is DeploymentProfile.LOCAL:
        result["ingress"] = {
            "driver_opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"}
        }
    else:
        result["application-egress"] = {}
        result["ingress"] = {}
    return result


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


def mailpit_store(configuration):
    """The LOCAL mail catcher's message store, beside the other persistent stores.

    It is not a configurable path of its own: adding one to ``RuntimePaths``
    would add a key to every rendered service document, including
    Production's, which must stay byte-identical. ``persistent_root`` itself
    is still overridable.
    """
    return configuration.paths["persistent_root"] / "mailpit"


def _mailpit(configuration):
    """The LOCAL mail catcher (#476): plain SMTP in, a loopback-only UI out.

    Only LOCAL renders it. It joins ``backend``, where the application reaches
    it at ``LOCAL_SMTP_ENDPOINT`` (``mailpit:1025``), and ``ingress``, so its
    UI can be published; the publication is on the VM's loopback only. It is
    run like the other stock images (unprivileged, read-only root) with the
    one writable store below. It can never relay: Mailpit forwards mail only
    when its relay settings are configured, and none is rendered, so every
    message it receives stays inside it.
    """
    if configuration.profile is not DeploymentProfile.LOCAL:
        raise ConfigError("The mail catcher is rendered only for the local profile.")
    from .mail_catcher import LOCAL_SMTP_ENDPOINT

    _, smtp_port = LOCAL_SMTP_ENDPOINT
    result = _application(MAILPIT_IMAGE, configuration.runtime_budget)
    result.update(
        environment={
            "MP_SMTP_BIND_ADDR": f"0.0.0.0:{smtp_port}",
            "MP_UI_BIND_ADDR": "0.0.0.0:8025",
            "MP_DATABASE": "/data/mailpit.db",
            "MP_MAX_MESSAGES": str(MAILPIT_MAX_MESSAGES),
            # No reverse DNS on connecting containers: there is no egress and
            # nothing to learn from it.
            "MP_SMTP_DISABLE_RDNS": "true",
            # Mailpit relays only when told to; say so explicitly, and render
            # no relay, forward, webhook or POP3 setting (a test pins this).
            "MP_SMTP_RELAY_ALL": "false",
            # The UI is reached as http://localhost:8025 from the laptop (and
            # by its own readiness probe); refusing any other Host header
            # keeps a web page the developer visits from reading captured
            # mail through DNS rebinding.
            "MP_ALLOWED_HOSTS": "localhost,127.0.0.1",
        },
        healthcheck={
            # The image's own readiness probe, against its UI port.
            "test": ["CMD", "/mailpit", "readyz"],
            "interval": "10s",
            "timeout": "3s",
            "retries": 3,
        },
        ports=["127.0.0.1:8025:8025"],
        networks={"backend": {}, "ingress": {}},
        volumes=[bind(mailpit_store(configuration), target="/data", read_only=False)],
        # Mailpit holds no application work to drain; it flushes its store at once.
        stop_grace_period="10s",
    )
    return result


def fake_parishsoft_configuration(configuration):
    """The fake ParishSoft service's one input: ``run/local/fake-parishsoft.json``.

    The operator script's ``up`` writes it (seed, Family count, anchor date and
    release instant), owned by the application uid with mode 0600, before the
    services start; the fake reads it once at start. It is the service's only
    mount, so the renderer and the script must agree on this path.
    """
    return configuration.paths["run"] / "local" / "fake-parishsoft.json"


def _fake_parishsoft(configuration, image):
    """The LOCAL stand-in for ParishSoft's v2 API (#476), from the application image.

    Only LOCAL renders it. It runs the ``fake-parishsoft`` command like any
    other application container (unprivileged, read-only root, backend network
    only) and answers at ``LOCAL_SOURCE_BASE_URL``, so the worker and the
    ParishSoft credential installer reach it by its service name over
    ``backend``. It receives no credential and exactly one mount, its own
    configuration file, read-only; it publishes no port and never joins
    ``ingress``. The command refuses any profile but LOCAL before binding, and
    the profile is passed explicitly because the service mounts no deployment
    document.
    """
    if configuration.profile is not DeploymentProfile.LOCAL:
        raise ConfigError("The fake ParishSoft is rendered only for the local profile.")
    from parishkit.parishsoft_http_worker import LOCAL_SOURCE_BASE_URL

    target = urlsplit(LOCAL_SOURCE_BASE_URL)
    path = fake_parishsoft_configuration(configuration)
    result = _application(image, configuration.runtime_budget)
    result.update(
        command=[
            "fake-parishsoft",
            "--profile",
            DeploymentProfile.LOCAL.value,
            "--fake-config",
            str(path),
            "--port",
            str(target.port),
        ],
        healthcheck={
            # Listening on its port; the image has Python but no nc.
            "test": [
                "CMD",
                "python",
                "-c",
                "import socket; socket.create_connection(('127.0.0.1', "
                f"{target.port}), timeout=2).close()",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
        volumes=[bind(path)],
    )
    return result


def _caddy(configuration):
    """The stock proxy, which keeps running while offline work upgrades the app.

    Unlike every other online service, Caddy holds no startup-interlock lease
    (#162). It reads no database, credential or runtime state: it serves the
    static tree and proxies to web, and web still holds the lease, so nothing
    behind Caddy can start during offline work. Staying up lets it answer with
    its own maintenance page (runtime_ingress) instead of a refused connection
    while an upgrade has web stopped.
    """
    layout = RuntimeLayout(configuration)
    result = _application(CADDY_IMAGE, configuration.runtime_budget)
    if configuration.profile is DeploymentProfile.LOCAL:
        # The local Caddyfile has no HTTP listener (runtime_ingress), and the
        # one HTTPS port is published on the VM's loopback only; nothing in
        # LOCAL binds 0.0.0.0.
        listening = "nc -z -w 2 127.0.0.1 8443"
        ports = ["127.0.0.1:8443:8443"]
    else:
        listening = "nc -z -w 2 127.0.0.1 8080 && nc -z -w 2 127.0.0.1 8443"
        ports = ["80:8080", "443:8443"]
    result.update(
        # Stock Caddy carries a NET_BIND_SERVICE file capability. Linux refuses
        # exec when that capability is absent from the bounding set, even when
        # this deployment uses high internal ports. Retain only that capability.
        cap_add=["NET_BIND_SERVICE"],
        healthcheck={
            "test": ["CMD-SHELL", listening],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
        entrypoint=["/bin/sh", "-c"],
        command=["exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile"],
        ports=ports,
        networks={
            "proxy": {"ipv4_address": configuration.runtime_network.caddy},
            "ingress": {},
        },
        volumes=[
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
