"""Read-only campaign overview with role-appropriate aggregates and no credentials."""

import json
from datetime import timedelta
from uuid import uuid4

from django.db import connection
from django.db.models import Count, Q

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.models import ScheduleRevision
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.refresh_status import (
    full_refresh_status,
    refresh_schedule,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot

from .policy import Capability, allows


def observe(actor, store, *, home=False):
    """Read the home summary in one read-only snapshot, for the page and the CLI.

    Returns ``(configuration, summary, now)``, or None while a restore is
    under review. A read-only snapshot, not the writers' work lock: the
    dashboard only observes, and must not wait behind a source promotion or
    installer. ``now`` is read inside the snapshot, so the page's chrome can
    reuse it instead of another clock query. ``home`` adds what only the
    Home page shows (a Ministry leader's My Ministries panel); the command
    line's status read leaves it out rather than read and drop it.
    """
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.campaigns.work_locks import read_transaction

    from .configuration_installation import coherent_configuration
    from .sessions import database_now

    with read_transaction():
        config = coherent_configuration(store)
        if config.restore_review_required:
            return None
        if config.current_campaign_id is not None:
            # The summary and the page both read the campaign's settings;
            # load them together once rather than lazily twice.
            config.current_campaign = Campaign.objects.select_related(
                "active_configuration"
            ).get(pk=config.current_campaign_id)
        now = database_now()
        return config, summary(actor, config, now, home=home), now


def summary(actor, configuration, now, *, home=False):
    """Read under the caller's work lock, which pins source/configuration promotion."""
    campaign = configuration.current_campaign
    # One query for the promoted snapshot's time: the page has a fixed query
    # budget, and the pointer's own row need not be read first.
    refreshed_at = (
        SourceSnapshot.objects.filter(
            pk__in=SourceCurrent.objects.exclude(snapshot_id=None).values("snapshot_id")
        )
        .values_list("promoted_at", flat=True)
        .first()
    )
    result = {
        "campaign": campaign,
        "refreshed_at": refreshed_at,
        # System health's facts ride on the same statement for those who
        # may open that page (ADM-13), for Home's problem lines below.
        "full_refresh": full_refresh_status(
            refresh_schedule(configuration),
            now,
            health=allows(actor, Capability.SYSTEM_LOGS),
        ),
        # Only an Administrator may request a manual refresh; the link is
        # offered to nobody else.
        "can_refresh": allows(actor, Capability.CONFIGURE),
        # A fresh key for the one-click full refresh button (no query).
        "refresh_key": uuid4() if allows(actor, Capability.CONFIGURE) else None,
        # The failure notice links to the failed task only for users who may
        # open background task pages; others see the notice alone.
        "can_view_task": allows(actor, Capability.BACKGROUND_WORK),
    }
    if campaign is not None:
        result["next_mail"] = (
            ScheduleRevision.objects.filter(
                configuration_id=configuration.active_configuration_id,
                campaign_id=campaign.pk,
                kind__in=["initial", "reminder"],
                due_at__gt=now,
            )
            .order_by("due_at", "record_id")
            .values("kind", "due_at")
            .first()
        )
        if allows(actor, Capability.CAMPAIGN_REPORT):
            counts = FamilyCampaign.objects.filter(campaign=campaign).aggregate(
                active=Count("id", filter=Q(active=True)),
                eligible=Count("id", filter=Q(portal_eligible=True)),
                responded=Count("id", filter=Q(first_live_submission_id__isnull=False)),
                eligible_responded=Count(
                    "id",
                    filter=Q(
                        portal_eligible=True, first_live_submission_id__isnull=False
                    ),
                ),
            )
            counts["participation"] = Percentage(
                counts["eligible_responded"], counts["eligible"]
            )
            result["families"] = counts
        if campaign.state in {"draft", "scheduled", "active"} and allows(
            actor, Capability.FAMILY_CODES
        ):
            result["unreachable"] = unreachable_families(campaign.pk)
    if allows(actor, Capability.CONFIGURE):
        from .backup_destination import offsite_status

        kinds = {
            record["values"]["kind"]
            for record in configuration.active_configuration.canonical_document[
                "sections"
            ].get("integrations", [])
        }
        # Read copy outcomes only when off-site copies are set up, so the
        # page's query budget is unchanged otherwise; until then the page
        # invites the Administrator to set them up.
        if "backup" in kinds:
            result["offsite"] = offsite_status()
        else:
            result["offsite_unset"] = True
        from .integration_credentials import STOPPED, unfinished_switches
        from .integration_forms import LABELS

        # A new key installed but not selected stops its integration (and
        # holds email) until an Administrator finishes the switch (#307).
        result["unfinished_keys"] = [
            {"target": target, "label": LABELS[target], "stopped": STOPPED[target]}
            for target in unfinished_switches(configuration)
        ]
        from parishkit.stewardship.source.ministry_catalog import catalog_notice

        # Ministries ParishSoft added, removed or renamed recently, and the
        # current campaign's Ministries that are gone or look retired (#342).
        # Those who may change Ministry activity see it. One query.
        result["ministry_catalog"] = catalog_notice(
            configuration, campaign, now, promoted=refreshed_at is not None
        )
    if allows(actor, Capability.SYSTEM_LOGS):
        from parishkit.stewardship.system_health import home_problems

        # One line for each System health problem, for those who may open
        # that page (ADM-13); others see none.
        result["health_problems"] = home_problems(
            configuration, now, result["full_refresh"]
        )
    if home and campaign is not None and leads_ministries(actor):
        from parishkit.stewardship.reports.ministry_followup import my_ministries

        # A Ministry leader's own starting point (#533): Administrators and
        # Staff reach every Ministry from the Reports menu instead.
        result["my_ministries"] = my_ministries(campaign.pk, actor)
    if "administrator" in actor.roles:
        from .security_events import open_events

        # Every Administrator's dashboard shows an expansion of who may sign
        # in until an Administrator acknowledges it.
        result["security_events"] = open_events(actor)
        from .automation_sessions import open_notices

        # Automation notices (ADM-11) stay on each Administrator's dashboard
        # until that Administrator acknowledges them.
        result["automation_notices"] = open_notices(actor)
    if allows(actor, Capability.BACKGROUND_WORK):
        # Imported here like the other optional sections above: jobs.views
        # loads the task API's view stack, which only this section needs.
        from parishkit.stewardship.jobs.views import TASK_NAMES

        result["recent_failures"] = [
            # The plain task name leads; the internal type stays for display
            # under Technical details.
            {**task, "name": TASK_NAMES.get(task["task_type"], task["task_type"])}
            for task in TaskRun.objects.filter(
                state="failed", updated_at__gte=now - timedelta(hours=24)
            )
            .order_by("-updated_at", "-id")
            .values("id", "task_type", "updated_at")[:5]
        ]
    return result


def leads_ministries(actor):
    """Whether Home shows this person the My Ministries panel.

    Only a Ministry leader whose follow-up access is scoped to particular
    Ministries: someone with campaign-wide follow-up (Administrator, Staff)
    already has every Ministry one click away.
    """
    return (
        "ministry_leader" in actor.roles
        and bool(actor.ministries)
        and not allows(actor, Capability.MINISTRY_FOLLOWUP)
    )


def unreachable_families(campaign_id):
    """Count Families no campaign mail can reach: no email and no mailing address.

    A readiness figure, from the same SQL selection the Family-code
    directory uses (its "neither" reach filter), so the number matches the list
    its link opens. None when there is no promoted source to count from.
    """
    from parishkit.stewardship.reports.directory_query import DIRECTORY

    parameters = {
        "filters": {
            "search": "",
            "reason": "any",
            "phone": "any",
            "response": "any",
            "sort": "name",
            "reach": "neither",
        },
        "postal": False,
        "exact": False,
        "family_id": None,
    }
    # One statement; the dashboard's own error handling covers a failure.
    with connection.cursor() as cursor:
        cursor.execute(DIRECTORY, (campaign_id, json.dumps(parameters), 1))
        row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return json.loads(row[0])["unreachable_total"]
