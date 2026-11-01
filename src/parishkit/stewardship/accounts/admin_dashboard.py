"""Read-only campaign overview with role-appropriate aggregates and no credentials."""

import json
from datetime import timedelta
from uuid import uuid4

from django.db import connection
from django.db.models import Count, Exists, OuterRef, Q
from django.utils.translation import gettext as _

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.models import ScheduleRevision
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.source.refresh_status import (
    full_refresh_status,
    refresh_schedule,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.web.presentation import number, percentage
from parishkit.stewardship.web.stat_tiles import tile

from . import campaign_wizard
from .campaign_wizard import invitation_revision
from .policy import Capability, allows


def observe(actor, store, *, home=False):
    """Read the home summary in one read-only snapshot, for the page and the CLI.

    Returns ``(configuration, summary, now)``, or None while a restore is
    under review. A read-only snapshot, not the writers' work lock: the
    dashboard only observes, and must not wait behind a source promotion or
    installer. ``now`` is read inside the snapshot, so the page's chrome can
    reuse it instead of another clock query. ``home`` adds what only the
    Home page shows (such as its Review work panel); the command
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
    refreshed_at = promoted_at()
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
    if campaign is None and allows(actor, Capability.CONFIGURE):
        # Just after system setup there is no campaign yet; Home's next step
        # for an Administrator is Create the campaign (#142). It is offered
        # only while the deployment has never had one, after ParishSoft data
        # is loaded, and is shown unavailable while campaign work holds it.
        from parishkit.stewardship.campaigns.single_campaign import (
            first_campaign_offer,
        )

        result["first_campaign"] = first_campaign_offer(configuration)
    if home and campaign is not None:
        # The campaign panel's days left (#1673): the snapshot's clock and
        # the campaign's own dates, so no query.
        result["campaign_facts"] = campaign_facts(campaign, now)
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
            # Home's 24-hour Responses tile (#1673): Families with a response
            # saved in the past 24 hours, in the mode the system is in now
            # (Testing responses while testing). A filter on this statement, so
            # it adds no query; the submission index on (family, mode,
            # submitted_at) serves it. Home only: the command line's status
            # read leaves it out.
            recent = {}
            if home:
                responded = Submission.objects.filter(
                    family=OuterRef("pk"),
                    mode="test" if configuration.mode == "testing" else "live",
                    submitted_at__gte=now - timedelta(hours=24),
                )
                recent["recent"] = Count("id", filter=Q(Exists(responded)))
            # Home's one response population (#1673) is the campaign
            # Families, the rule's ``portal_eligible`` rows: "eligible" and
            # "eligible_responded" (#2331). The command line's status read
            # also reports "active"; it counts the same rule's Families, as
            # ``pk-admin go-live``'s "active" does, so one key names one set.
            # (The row's ``active`` column is ParishSoft's active flag OR the
            # rule, so it also holds Families the rule does not select.)
            counts = FamilyCampaign.objects.filter(campaign=campaign).aggregate(
                **recent,
                active=Count("id", filter=Q(portal_eligible=True)),
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
            if home:
                result["family_tiles"] = family_tiles(
                    counts,
                    recent=counts["recent"],
                    testing=configuration.mode == "testing",
                )
        if (
            home
            and campaign.state == "draft"
            and configuration.mode == "testing"
            and allows(actor, Capability.CONFIGURE)
        ):
            # Home's Next steps offer Preview and test email for the
            # initial invitation's current revision while a Testing draft
            # admits test mail (NAV-18); the same row Pause and resume mail
            # links. One small indexed query, for Administrators only.
            result["test_email"] = invitation_revision(configuration, campaign.pk)
            # Home's Continue campaign setup (#1404): the first step of the
            # Campaign setup stepper not done yet, while the stepper applies.
            # It reuses the revision just read, in this same snapshot.
            wizard = campaign_wizard.current_wizard(
                configuration, revision=result["test_email"]
            )
            result["setup_next"] = campaign_wizard.resume(wizard) if wizard else None
        # Home no longer shows the count (#1673): Data to fix in ParishSoft
        # and the Family directory's "Can't be reached" preset carry it, and
        # the command line's status read still reports it.
        if (
            not home
            and campaign.state in {"draft", "scheduled", "active"}
            and allows(actor, Capability.FAMILY_CODES)
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
        # Read copy outcomes only when off-site copies are set up, and only
        # for the command line: Home leaves the off-site line to System
        # health's Backups panel and problem count (#1673).
        if "backup" not in kinds:
            result["offsite_unset"] = True
        elif not home:
            result["offsite"] = offsite_status()
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
        # Those who may change campaign settings see it. One query.
        result["ministry_catalog"] = catalog_notice(
            configuration, campaign, now, promoted=refreshed_at is not None
        )
        # A hold exists only after a restore, which leaves its id on the
        # runtime row already loaded; a deployment never restored skips the
        # query, so Home's query budget is unchanged for it.
        if home and campaign is not None and configuration.restore_id is not None:
            from parishkit.stewardship.campaigns.restore_review import (
                undecided_emails,
            )

            # Held emails a restore left undecided at release (#757) are
            # never sent, and a held invitation keeps its Family's
            # reminders back, so Home links to Held emails until each is
            # decided. One count, of emails (two restores can hold one), on
            # Home only: the command line's status read leaves it out.
            result["held_emails"] = undecided_emails(campaign.pk)
    if allows(actor, Capability.SYSTEM_LOGS):
        from parishkit.stewardship.system_health import BLOCKS_PAGE, home_problems

        # System health's problems, for those who may open that page
        # (ADM-13); others see none. Home counts them in one line linking
        # the page (#1673), except a problem that keeps the page from
        # opening: Home shows its own sentence instead (``health_lines``).
        problems = home_problems(configuration, now, result["full_refresh"])
        result["health_lines"] = [p for p in problems if p.kind in BLOCKS_PAGE]
        result["health_problems"] = [p for p in problems if p.kind not in BLOCKS_PAGE]
        # The count line takes the error look when any counted problem is
        # an error, else the warning look.
        result["health_severity"] = (
            "error"
            if any(problem.severity == "error" for problem in result["health_problems"])
            else "warning"
        )
    elif home:
        # Without System health, Home's one ParishSoft line says the data
        # may be out of date while the latest full refresh failed or is late
        # (not while a Family send merely holds it), as the refresh block
        # did before #1673. No query: the status is already read.
        status = result["full_refresh"]
        result["refresh_stale"] = bool(
            status.failed_at
            or (
                status.out_of_date
                and not status.held_for_send
                and not status.catching_up
            )
        )
    if home and campaign is not None and "administrator" in actor.roles:
        from .review_work import read_flags

        # Home's Review work panel: the panels Staff and Ministry leaders see
        # on My review work (#1690), plus Ready to update. Home is the
        # Administrator's alone (#1726), so this is the only role that
        # reaches it. The counts are read at render time inside the menu's
        # open-count statement (review_work.with_counts), so the panel adds
        # no statement. An Administrator gets the Staff Ministry cards, never
        # a leader's per-Ministry ones, so there is no leader value.
        result["review"] = {
            "campaign": campaign,
            "flags": read_flags(actor),
            "leader": None,
        }
    if "administrator" in actor.roles:
        from .security_events import open_events

        # Every Administrator's dashboard shows an expansion of who may sign
        # in until an Administrator acknowledges it.
        result["security_events"] = open_events(actor)
        if home:
            from .automation_sessions import pending_count

            # Automation notices (ADM-11) stay counted on each
            # Administrator's Home until that Administrator acknowledges
            # them; the list itself is on Automation access (#1673). The
            # command line's status read counts them itself.
            result["automation_pending"] = pending_count(actor)
    if allows(actor, Capability.BACKGROUND_WORK):
        failed = TaskRun.objects.filter(
            state="failed", updated_at__gte=now - timedelta(hours=24)
        )
        if home:
            # Home counts them in one line linking Background work (#1673).
            result["failed_tasks"] = failed.count()
        else:
            # Imported here like the other optional sections above: jobs.views
            # loads the task API's view stack, which only this list needs.
            from parishkit.stewardship.jobs.views import TASK_NAMES

            result["recent_failures"] = [
                # The plain task name; the internal type stays in the logs.
                {**task, "name": TASK_NAMES.get(task["task_type"], task["task_type"])}
                for task in failed.order_by("-updated_at", "-id").values(
                    "id", "task_type", "updated_at"
                )[:5]
            ]
    return result


def promoted_at():
    """When the ParishSoft data in use now was promoted, or None before any.

    One query: the page that shows it has a fixed query budget, and the
    current-snapshot pointer's own row need not be read first. Home and
    Data to fix in ParishSoft both show it.
    """
    return (
        SourceSnapshot.objects.filter(
            pk__in=SourceCurrent.objects.exclude(snapshot_id=None).values("snapshot_id")
        )
        .values_list("promoted_at", flat=True)
        .first()
    )


def leads_ministries(actor):
    """Whether My review work shows this person one card per Ministry they lead.

    Only a Ministry leader whose follow-up access is scoped to particular
    Ministries (``review_work.read_counts``): someone with campaign-wide
    follow-up (Administrator, Staff) gets the queue's one count instead.
    """
    return (
        "ministry_leader" in actor.roles
        and bool(actor.ministries)
        and not allows(actor, Capability.MINISTRY_FOLLOWUP)
    )


def family_tiles(counts, *, recent=None, testing=False):
    """Home's Responses tiles in the campaign panel (#720, #1540, #1673).

    One population, the campaign Families (the Families rule's
    ``eligible`` count, the Response dashboard's "In this campaign today",
    #2331): how many there are; how many of them have a response
    (``eligible_responded``), not counting Testing, with its share bar;
    and, when ``recent`` is given, the Families who responded in the past
    24 hours (Testing responses while ``testing``, as its explanation says).
    """
    tiles = [
        tile("active", _("Campaign Families"), counts["eligible"]),
        tile(
            "responded",
            _("Campaign Families responded"),
            counts["eligible_responded"],
            details=(
                _("out of %(total)s (%(share)s)")
                % {
                    "total": number(counts["eligible"]),
                    "share": percentage(
                        Percentage(counts["eligible_responded"], counts["eligible"])
                    ),
                },
            ),
            share=(counts["eligible_responded"], counts["eligible"]),
        ),
    ]
    if recent is not None:
        tiles.append(
            tile(
                "recent",
                # Every Family with any submission in the rolling 24 hours,
                # a resubmission too, so the label says submitted (#2331).
                _("Families who submitted in the past 24 hours"),
                recent,
                details=(_("Testing responses, while in Testing mode"),)
                if testing
                else (),
            )
        )
    return tiles


def campaign_facts(campaign, now):
    """The campaign panel's countdown on Home (#1673), or an empty dict.

    Today is the campaign's own date (its time zone), so the count matches
    the dates the strip shows. Before the start date it is ``opens_in``
    days; from then until the end date, ``days_left`` (0 on the last day);
    after the end date, or for a closed campaign, nothing.
    """
    from zoneinfo import ZoneInfo

    settings = campaign.active_configuration
    start, end = settings.start_date, settings.end_date
    if (
        start is None
        or end is None
        or campaign.state
        not in {
            "draft",
            "scheduled",
            "active",
        }
    ):
        return {}
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    if today < start:
        return {"opens_in": (start - today).days}
    if today <= end:
        return {"days_left": (end - today).days}
    return {}


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
