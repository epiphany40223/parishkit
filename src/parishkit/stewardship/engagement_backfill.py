"""Fill the durable Family engagement table from what Production retained (#477).

Before the engagement table existed, "link followed" survived only as the
retained ``family_login`` audit events and "form opened" only as the Family
form baselines; Family session rows, the one place progress was visible, are
deleted an hour after the last activity. This command replays those two
durable sources through the same monotonic upsert the live paths use, so a
Family's first link and first form instants are the earliest on record and
nothing already recorded moves. Progress before the table existed is gone and
is not invented. The upsert skips rows it would not change, so running the
command again writes nothing but its own audit event. Writes commit in small
batches so a long backfill never holds many Families' locks against live
sign-ins (see ``campaigns/engagement.py`` on the deadlock this avoids).

It covers Production only: the events since the latest activation of
Production mode, for the current campaign's Families. Testing logins before
activation belonged to a rehearsal whose detail the Production transition
deleted, and audit events do not say which epoch they were.

It runs inside an admitted web container like ``pk-stewardship
source-form-check``: under the web's own restricted SQL login, which is the
one login the engagement guard lets write.
"""

import json
import sys

from parishkit.config import ConfigError

from .deployment import ServiceRole, load_deployment
from .observability import Event, configure_logging, emit_failure
from .runtime_paths import RuntimeLayout
from .startup_interlock import StartupBusy, StartupLease


class BackfillRefused(ConfigError):
    """The backfill cannot start: not in Production, or no current campaign."""


def production_activation():
    """When Production mode last began, or None while the deployment is in Testing.

    Every runtime mode change is an immutable ``RuntimeTransition``; the
    latest one that entered Production bounds the audit events whose logins
    were live rather than rehearsal.
    """
    from .campaigns.runtime_models import RuntimeTransition

    return (
        RuntimeTransition.objects.filter(after_mode="production")
        .exclude(before_mode="production")
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )


BATCH_FAMILIES = 50


def backfill_observations(logins, baselines, family_ids):
    """Turn grouped history into one upsert observation per Family.

    ``logins`` and ``baselines`` are ``(family_id, first_at, last_at)`` rows;
    ``family_ids`` are the current campaign's Families. Events for a Family
    outside the campaign (a stale actor id) are skipped rather than invented.
    The observation's ``seen_at`` is the latest instant either source knows.
    """
    merged = {}
    for key, rows in (("link_at", logins), ("form_at", baselines)):
        for family_id, first_at, last_at in rows:
            if family_id not in family_ids:
                continue
            observation = merged.setdefault(
                family_id, {"family_id": family_id, "seen_at": last_at}
            )
            observation[key] = first_at
            observation["seen_at"] = max(observation["seen_at"], last_at)
    return [merged[family_id] for family_id in sorted(merged, key=str)]


def backfill():
    """Replay retained logins and baselines through the monotonic upsert.

    The history is read once; the writes commit in batches of
    ``BATCH_FAMILIES`` Families, so a long backfill never holds a thousand
    Families' KEY SHARE locks in one transaction against live sign-ins. The
    upsert skips rows it would not change, so a repeated run writes nothing
    but its own audit event, which records how many rows changed.
    """
    from django.db import transaction
    from django.db.models import Max, Min

    from .accounts.runtime_models import SystemConfiguration
    from .audit.models import AuditEvent
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .campaigns.credential_models import FamilyCampaign
    from .campaigns.engagement import record_engagement
    from .responses.models import FamilyFormBaseline

    with transaction.atomic():
        runtime = SystemConfiguration.objects.values(
            "mode", "current_campaign_id"
        ).get()
        activation = production_activation()
        if runtime["mode"] != "production" or activation is None:
            raise BackfillRefused("The backfill covers Production engagement only.")
        if runtime["current_campaign_id"] is None:
            raise BackfillRefused("There is no current campaign.")
        campaign_id = runtime["current_campaign_id"]
        family_ids = set(
            FamilyCampaign.objects.filter(campaign_id=campaign_id).values_list(
                "pk", flat=True
            )
        )
        logins = list(
            AuditEvent.objects.filter(
                event_type="family_login", created_at__gte=activation
            )
            .values_list("actor_id")
            .annotate(first=Min("created_at"), last=Max("created_at"))
            .order_by("actor_id")
        )
        baselines = list(
            FamilyFormBaseline.objects.filter(
                mode="live", family__campaign_id=campaign_id
            )
            .values_list("family_id")
            .annotate(first=Min("created_at"), last=Max("created_at"))
            .order_by("family_id")
        )
    observations = backfill_observations(logins, baselines, family_ids)
    changed = 0
    for start in range(0, len(observations), BATCH_FAMILIES):
        with transaction.atomic():
            for observation in observations[start : start + BATCH_FAMILIES]:
                # No actor: this is the operator's replay of history, not the
                # Family acting; the audit event below names the run.
                changed += record_engagement(
                    mode="live", rehearsal_epoch_id=None, actor_id=None, **observation
                )
    with transaction.atomic():
        record_action(
            Action.FAMILY_ENGAGEMENT_BACKFILLED,
            actor_kind=ActorKind.OPERATOR,
            subject_id=campaign_id,
            context={"count": changed, "outcome": Outcome.SUCCEEDED},
        )
    return {
        "check": "engagement_backfill",
        "activation_at": activation.isoformat(),
        "families_linked": sum(1 for item in observations if "link_at" in item),
        "families_with_form": sum(1 for item in observations if "form_at" in item),
        "rows_changed": changed,
        "result": "backfilled",
    }


def engagement_backfill_command(configuration):
    """Admit as the load check does (web profile, lease, web login), then backfill."""
    from django.db import connections

    from .operator_commands import configure_operator_database
    from .runtime_grants import admit_runtime_database
    from .runtime_web import admit_lifecycle_mounts
    from .service_boundaries import admit_online_service

    if admit_online_service(configuration) is not ServiceRole.WEB:
        raise ConfigError("The engagement backfill requires the admitted web profile.")
    admit_lifecycle_mounts(configuration)
    with StartupLease(RuntimeLayout(configuration).interlock, offline=False):
        configure_operator_database(configuration)
        try:
            admit_runtime_database(configuration)
            return backfill()
        finally:
            connections.close_all()


def execute_engagement_backfill(args):
    """Console entry: one JSON document (exit 0) or one fixed error line (exit 2).

    Errors print fixed wording only; exception text could carry deployment
    paths.
    """
    configure_logging()
    try:
        if args.config is None:
            raise ConfigError("The engagement backfill requires a configuration.")
        document = engagement_backfill_command(load_deployment(args.config))
    except StartupBusy:
        print(
            "ERROR: offline maintenance is in progress; retry the backfill",
            file=sys.stderr,
        )
        return 2
    except (ConfigError, PermissionError) as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print(
            "ERROR: engagement backfill refused; verify the web profile, Production "
            "mode and a current campaign",
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        emit_failure(error, event=Event.TASK_FAILED)
        print(
            "ERROR: engagement backfill stopped by an unexpected error; see the "
            "process log",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(document, sort_keys=True))
    return 0
