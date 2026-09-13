"""Admit isolated task processes before assembling their closed handler registry.

The general worker can read ParishSoft, seal new links and reconcile local source
effects. It cannot decrypt email links or run future delivery/publication tasks.
The scheduler has metadata-only handlers and no executable provider dependency.
"""

from dataclasses import dataclass, field
from threading import Event

from parishkit.config import ConfigError

from .accounts.cryptography import independent_keyrings
from .accounts.key_files import parse_keyring, read_private
from .deployment import ServiceRole


@dataclass(frozen=True)
class BackgroundRuntime:
    """Retain only admitted dependencies; private keys/transport stay out of repr."""

    broker: object = field(repr=False)
    handlers: dict = field(repr=False)
    receipts: dict = field(repr=False)
    store: object = field(repr=False)


def matching_authority(store):
    """Every queue pass/effect holds when YAML selection and SQL truth disagree."""
    from .accounts.runtime_models import SystemConfiguration

    selected = store.active()
    actual = SystemConfiguration.objects.values_list(
        "active_configuration_id", "active_configuration__digest"
    ).first()
    if selected is None or actual != (selected.version_id, selected.digest):
        raise ConfigError("Background configuration requires recovery.")
    if store.manifest_reference() != actual:
        raise ConfigError("Background configuration changed during admission.")


def mail_authority(store):
    """Verify exact public authority without loading unused parish projections.

    The setup mail consumer uses its immutable delivery journal, not campaign or
    parish projection rows. Compare the validated YAML document with the frozen
    SQL document and pointer instead of expanding its database read authority.
    """
    from .accounts.runtime_models import SystemConfiguration

    selected = store.active()
    runtime = SystemConfiguration.objects.select_related("active_configuration").first()
    if (
        selected is None
        or runtime is None
        or runtime.active_configuration_id != selected.version_id
        or runtime.active_configuration.digest != selected.digest
        or runtime.active_configuration.canonical_document != selected.document()
        or store.manifest_reference() != (selected.version_id, selected.digest)
    ):
        raise ConfigError("Mail configuration requires recovery.")
    return runtime


def bind_authority(handlers, store, *, heartbeat=None):
    """Preserve compiled execution while adding fresh file/SQL checks to admission."""
    from dataclasses import replace
    from functools import partial

    def admit(original, action, status):
        """Neither a queued UUID nor startup readiness survives a later mismatch."""
        matching_authority(store)
        return original(action, status)

    return {
        name: replace(handler, admit=partial(admit, handler.admit), pulse=heartbeat)
        for name, handler in handlers.items()
    }


def pre_delivery_suppressions(scope):
    """Testing has no live address refusals before the Phase 4 delivery owner.

    This is an explicit phase admission boundary, not a default success callback:
    Production source effects remain disabled until BG-06 wires durable provider
    suppressions. Testing messages use the single test recipient and cannot
    suppress real Family recipients.
    """
    from .campaigns.work_locks import require_work_order

    require_work_order()
    if scope.runtime.mode != "testing":
        raise ConfigError("Production refresh requires the delivery suppression owner.")
    return frozenset()


def scheduler_handlers():
    """Compiled metadata admission only; accidental provider/file execution refuses."""
    from .accounts.branding_cleanup import TASK_TYPE as BRANDING_CLEANUP
    from .accounts.branding_cleanup import cleanup_handler
    from .accounts.setup_mail import TASK_TYPE as SETUP_MAIL
    from .accounts.setup_mail_tasks import setup_mail_handler
    from .campaigns.work_locks import work_transaction
    from .jobs.dispatch import Handler
    from .jobs.queues import WorkQueue
    from .source.outcomes import admit_refresh_metadata, recovery_plan
    from .source.requests import TASK_TYPE
    from .source.setup_admission import TASK_TYPE as SETUP_LOAD
    from .source.setup_admission import admit_setup_task
    from .source.setup_admission import recovery_plan as setup_recovery
    from .source.setup_cleanup import cleanup_handler as setup_cleanup_handler
    from .source.setup_disposal import TASK_TYPE as SETUP_CLEANUP

    def unavailable(execution):
        """A scheduler cannot become a provider worker by calling a registry value."""
        raise PermissionError("The scheduler cannot execute provider work.")

    return {
        BRANDING_CLEANUP: cleanup_handler(),
        SETUP_CLEANUP: setup_cleanup_handler(scheduler=True),
        SETUP_MAIL: setup_mail_handler(scheduler=True),
        SETUP_LOAD: Handler(
            queue=WorkQueue.GENERAL,
            admit=admit_setup_task,
            execute=unavailable,
            recover=setup_recovery,
            scope=work_transaction,
        ),
        TASK_TYPE: Handler(
            queue=WorkQueue.GENERAL,
            admit=admit_refresh_metadata,
            execute=unavailable,
            recover=recovery_plan,
            scope=work_transaction,
        ),
    }


def configure_background(configuration, *, stop, heartbeat):
    """Assemble in a fresh process only after kernel mounts and real SQL admission."""
    from .accounts.metrics_credentials import credential_receipt
    from .operator_commands import configure_operator_database
    from .runtime_grants import admit_runtime_database
    from .runtime_web import admit_lifecycle_mounts
    from .service_boundaries import admit_online_service

    role = configuration.service_role
    if (
        role
        not in {ServiceRole.WORKER, ServiceRole.SCHEDULER, ServiceRole.MAIL_DISPATCH}
        or not isinstance(stop, Event)
        or not callable(heartbeat)
    ):
        raise ConfigError("An isolated background process and stop event are required.")
    if admit_online_service(configuration) is not role:
        raise ConfigError("Background service admission differs from its profile.")
    admit_lifecycle_mounts(configuration)
    required = {
        ServiceRole.WORKER: {"general_encryption", "family_code_mac", "token_public"},
        ServiceRole.SCHEDULER: {"token_public"},
        ServiceRole.MAIL_DISPATCH: {"token_private", "token_public"},
    }[role]
    if not required <= configuration.secrets.keys():
        raise ConfigError("Background credential mounts are incomplete.")
    loaded = {name: read_private(path) for name, path in configuration.secrets.items()}
    rings = {
        name: parse_keyring(loaded[name], name) for name in required - {"parishsoft"}
    }
    independent_keyrings(*rings.values())
    if role is ServiceRole.MAIL_DISPATCH:
        derived = rings["token_private"].public()
        published = rings["token_public"]
        if derived.active != published.active or any(
            derived.keys.get(name) != key for name, key in published.keys.items()
        ):
            raise ConfigError("Mail private/public key inventories differ.")
    if role is ServiceRole.WORKER and "parishsoft" in loaded:
        from .source.credentials import SourceCredential

        SourceCredential(loaded["parishsoft"])
    try:
        password = (
            read_private(configuration.valkey.password_file)
            .decode("ascii")
            .removesuffix("\n")
        )
    except (TypeError, ValueError, UnicodeError):
        raise ConfigError("An individual broker credential is required.") from None
    configure_operator_database(configuration)
    # A fresh runtime has no Django settings/app registry until this point.
    # Broker/dispatcher imports transitively define storage models.
    from .jobs.broker import build_broker

    admit_runtime_database(configuration)
    from django.db import connections

    from .accounts.authority import AuthorityStore
    from .accounts.configuration_installation import coherent_configuration
    from .accounts.configuration_schema import validate_sections

    store = AuthorityStore(configuration.paths["authority"], validate_sections)
    try:
        active = (
            mail_authority(store)
            if role is ServiceRole.MAIL_DISPATCH
            else coherent_configuration(store)
        )
    except ConfigError:
        from .accounts.setup_startup import initial_setup_hold

        active = initial_setup_hold(
            store, projections=role is not ServiceRole.MAIL_DISPATCH
        )
    if (
        role is ServiceRole.WORKER
        and "parishsoft" not in loaded
        and (
            active is None
            or active.mode != "testing"
            or active.restore_review_required
            or active.current_campaign_id is not None
            or active.active_configuration.validation_schema != "bootstrap-policy-v1"
        )
    ):
        raise ConfigError("An installed ParishSoft credential is required.")

    if role is ServiceRole.SCHEDULER:
        handlers = scheduler_handlers()
    elif role is ServiceRole.MAIL_DISPATCH:
        if "google_workspace" not in loaded and (
            active is None
            or active.mode != "testing"
            or active.restore_review_required
            or active.current_campaign_id is not None
            or active.active_configuration.validation_schema != "bootstrap-policy-v1"
        ):
            raise ConfigError("An installed Workspace credential is required.")
        from .accounts.setup_mail import TASK_TYPE as SETUP_MAIL
        from .accounts.setup_mail_tasks import setup_mail_handler

        handlers = {SETUP_MAIL: setup_mail_handler()}
    else:
        from .accounts.branding_cleanup import TASK_TYPE as BRANDING_CLEANUP
        from .accounts.branding_cleanup import cleanup_handler
        from .source.effects import refresh_reconciler
        from .source.execution import refresh_handler
        from .source.requests import TASK_TYPE
        from .source.setup_admission import TASK_TYPE as SETUP_LOAD
        from .source.setup_cleanup import cleanup_handler as setup_cleanup_handler
        from .source.setup_disposal import TASK_TYPE as SETUP_CLEANUP
        from .source.setup_execution import setup_source_handler

        handlers = {
            BRANDING_CLEANUP: cleanup_handler(configuration.paths["media"]),
            SETUP_CLEANUP: setup_cleanup_handler(),
            SETUP_LOAD: setup_source_handler(),
        }
        if "parishsoft" in loaded:
            handlers[TASK_TYPE] = refresh_handler(
                credential_path=configuration.secrets["parishsoft"],
                reconcile=refresh_reconciler(
                    general=rings["general_encryption"],
                    mac=rings["family_code_mac"],
                    public=rings["token_public"],
                    suppressions=pre_delivery_suppressions,
                ),
            )
    handlers = bind_authority(handlers, store, heartbeat=heartbeat)
    # This is not ordinary selected-YAML authority. The compiled setup owner
    # repeats its exact original receipt/login/fences for every admitted action.
    if role is ServiceRole.SCHEDULER or (
        role is ServiceRole.WORKER and "parishsoft" in loaded
    ):
        from dataclasses import replace

        from .source.setup_final_execution import finalization_handler
        from .source.setup_final_tasks import TASK_TYPE as SETUP_FINALIZE

        options = (
            {"scheduler": True}
            if role is ServiceRole.SCHEDULER
            else {
                "credential_path": configuration.secrets["parishsoft"],
                "general": rings["general_encryption"],
                "mac": rings["family_code_mac"],
                "public": rings["token_public"],
            }
        )
        handlers[SETUP_FINALIZE] = replace(
            finalization_handler(store, **options), pulse=heartbeat
        )
    # This journal must retain observations after a configuration change; its
    # owner checks authority for new effects, not for drained outcome recovery.
    if role is ServiceRole.SCHEDULER or (
        role is ServiceRole.MAIL_DISPATCH and "google_workspace" in loaded
    ):
        from dataclasses import replace

        from .accounts.campaign_mail import TASK_TYPE as CAMPAIGN_MAIL
        from .accounts.campaign_mail_tasks import campaign_mail_handler

        handlers[CAMPAIGN_MAIL] = replace(
            campaign_mail_handler(
                store,
                scheduler=role is ServiceRole.SCHEDULER,
                credential_path=configuration.secrets.get("google_workspace"),
            ),
            pulse=heartbeat,
        )
    broker = build_broker(
        endpoint=configuration.valkey,
        password=password,
        service=role,
        handlers=handlers,
        stop=stop,
    )
    connections.close_all()
    return BackgroundRuntime(
        broker,
        handlers,
        {name: credential_receipt(raw, name) for name, raw in loaded.items()},
        store,
    )
