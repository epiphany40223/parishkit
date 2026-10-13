"""Download campaign archive (#1018 part (a), slice A2).

The Administrator's **Download campaign archive** on Past campaigns, and
``pk-admin export archive``, queue the shared export lifecycle's
``campaign_archive`` kind: one XLSX workbook in the campaign archive format
(campaign-history spec, "Archive export"). The request pins a ready
historical fact set (and with it the submission watermark it read) and a
cutoff day; the general worker reads the submissions itself under those pins
(``archive.export.read_source``) inside its campaign read guard, writes the
workbook and reads it back through the import's reader, so no Family-level
capture table exists.

- ``archive_offer`` is what a download would be now: final or partial, the
  cutoff day, or the reason it is unavailable (no current campaign, before
  day 1 has ended, a closed campaign with a response after the cutoff, no
  Family responded by the cutoff, no stewardship year, or no fact set that
  reaches the cutoff yet).
- ``create_archive_export`` allocates the request, its task and its fact
  pin, as the Participation export does, and records
  ``campaign_archive_exported`` with the cutoff's campaign day as ``count``.
  Only an Administrator (``CONFIGURE`` and ``FINANCIAL_DETAIL``) may; the
  view and the command add the five-minute fresh sign-in.
- ``archive_document`` is the worker's load: the written file and its row
  count, which ``render_archive`` copies to the stored file.

No figure, Family value or file content reaches the audit log: the shared
publication row holds the file's SHA-256 and row count (the ``families``
rows), and the file's own ``count.*`` rows carry the per-sheet counts.
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.work_locks import export_transaction
from parishkit.stewardship.jobs.storage import enqueue

from .export_models import ExportRequest
from .export_services import (
    TASK_TYPE,
    ExportRequestBound,
    admit_campaign,
    audit,
    authorize,
)
from .facts import FactUnavailable
from .retention import pin_facts

REPORT = "campaign_archive"
FORMAT = "xlsx"
# The archive's request time is UTC; the file itself carries no local time
# but the campaign's own (its metadata ``timezone``).
TIMEZONE = "UTC"

# Why a download is unavailable now; the page words each one.
NO_CAMPAIGN = "no_campaign"
BEFORE_DAY_1 = "before_day_1"
CLOSED_TODAY = "closed_today"
NO_RESPONSE = "no_response"
NO_YEAR = "no_year"
NOT_READY = "not_ready"


@dataclass(frozen=True)
class ArchiveOffer:
    """What Download campaign archive would make now, or why it cannot.

    ``reason`` is None when a download is available. ``final``,
    ``cutoff_date`` and ``day`` describe the file (``day`` is the cutoff's
    campaign day); ``fact_set_id`` is the ready historical fact set it would
    be checked against.
    """

    reason: str | None
    campaign_id: UUID | None = None
    campaign_name: str = ""
    year: int | None = None
    final: bool = False
    cutoff_date: date | None = None
    day: int | None = None
    end_date: date | None = None
    fact_set_id: UUID | None = None

    @property
    def available(self):
        """A download can be queued now."""
        return self.reason is None

    @property
    def file_name(self):
        """The file name the archive format's convention gives this file."""
        from parishkit.stewardship.archive.export import archive_name

        return archive_name(self.year, self.final) if self.year else ""


def campaign_now():
    """The campaign clock's instant (``stewardship_campaign_now_v1``).

    The same instant the comparison report's comparison day and the
    campaign's transitions use, read in any transaction or none.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_campaign_now_v1()")
        return cursor.fetchone()[0]


def require_administrator(store, user_id):
    """The current principal, refused unless an Administrator.

    The archive holds each Family's giving, so it is Past campaigns' access:
    ``CONFIGURE`` and ``FINANCIAL_DETAIL`` (only an Administrator holds both).
    """
    principal = authorize(store, user_id)
    if not (
        allows(principal, Capability.CONFIGURE)
        and allows(principal, Capability.FINANCIAL_DETAIL)
    ):
        raise PermissionError("The campaign archive is the Administrator's.")
    return principal


def _fact_set(campaign_id, cutoff_date, cutoff):
    """The newest ready historical fact set that covers the cutoff, or None.

    A set covers it when it holds the cutoff day's row and its submission
    watermark reaches every live version submitted before the cutoff
    instant. A set built during the cutoff day can hold that day's row yet
    miss a later response; pinning it would make every worker attempt
    refuse (``read_source``), so such a set is never offered.
    """
    from django.db.models import Exists, Max, OuterRef

    from parishkit.stewardship.responses.models import Submission

    from .models import CampaignDailyFact, CampaignDailyFactSet

    latest = (
        Submission.objects.filter(
            campaign_id=campaign_id, mode="live", submitted_at__lt=cutoff
        ).aggregate(latest=Max("campaign_sequence"))["latest"]
        or 0
    )
    return (
        CampaignDailyFactSet.objects.filter(
            campaign_id=campaign_id,
            population_scope="historical",
            state="ready",
            submission_watermark__gte=latest,
        )
        .filter(
            Exists(
                CampaignDailyFact.objects.filter(
                    fact_set_id=OuterRef("pk"), local_date=cutoff_date
                )
            )
        )
        .order_by("-submission_watermark", "-ready_at")
        .values_list("id", flat=True)
        .first()
    )


def archive_offer(campaign_id, *, now):
    """What Download campaign archive would make for ``campaign_id`` at ``now``.

    The cutoff is ``archive.export.archive_cutoff``'s: the last complete
    local day, capped at the end date; final for a closed or archived
    campaign, otherwise partial. A campaign closed before its end date is
    offered its final file through its last full day without waiting for
    the end date (Administrator decision, 2026-10-13, #1018), but only once
    no live submission falls after the cutoff: a campaign closed today with
    a response today would otherwise get a "final" file without that day,
    one that changes tomorrow. No close time is stored, so this test is the
    exact rule; a campaign closed with no response that day is final at
    once, and any other waits at most until the next day (the review's
    refinement of that decision, 2026-10-13). No Family responded by the
    cutoff is unavailable too.
    """
    from parishkit.stewardship.archive.export import archive_cutoff
    from parishkit.stewardship.archive.imports import ArchiveRefusal, year_from_values
    from parishkit.stewardship.campaigns.intervals import local_day
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.responses.models import Submission

    if campaign_id is None:
        return ArchiveOffer(NO_CAMPAIGN)
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    projection = campaign.active_configuration
    base = {
        "campaign_id": campaign_id,
        "campaign_name": projection.name,
        "end_date": projection.end_date,
    }
    try:
        base["year"] = year_from_values(projection.values)
    except ArchiveRefusal:
        return ArchiveOffer(NO_YEAR, **base)
    cutoff = archive_cutoff(
        campaign.state,
        projection.start_date,
        projection.end_date,
        projection.timezone,
        now,
    )
    if cutoff is None:
        return ArchiveOffer(BEFORE_DAY_1, **base)
    cutoff_date, final = cutoff
    base.update(
        final=final,
        cutoff_date=cutoff_date,
        day=(cutoff_date - projection.start_date).days + 1,
    )
    cutoff_instant = local_day(cutoff_date, projection.timezone).interval.end
    if (
        final
        and Submission.objects.filter(
            campaign_id=campaign_id, mode="live", submitted_at__gte=cutoff_instant
        ).exists()
    ):
        return ArchiveOffer(CLOSED_TODAY, **base)
    # A Family must have been in the historical cohort by the cutoff, as
    # the archive's Families are; the read applies the exact rule.
    if not Submission.objects.filter(
        campaign_id=campaign_id,
        mode="live",
        submitted_at__lt=cutoff_instant,
        family__first_eligible_at__lt=cutoff_instant,
    ).exists():
        return ArchiveOffer(NO_RESPONSE, **base)
    fact_set = _fact_set(campaign_id, cutoff_date, cutoff_instant)
    if fact_set is None:
        return ArchiveOffer(NOT_READY, **base)
    return ArchiveOffer(None, fact_set_id=fact_set, **base)


def create_archive_export(
    store, user_id, *, campaign_id, request_key, offer=None, retained=None
):
    """Queue one campaign archive workbook, as Download campaign archive does.

    The request pins the offer's fact set and cutoff (``archive_offer``,
    worked out inside the campaign's export lock when ``offer`` is omitted);
    the fact pin keeps that generation from compaction for the request's
    retention. ``retained`` is instead an expired archive request being
    regenerated: its own pins (fact set, cutoff, final mark and year) are
    reused, so the file holds the same records with a new request time
    (``converted_at``) and so a new checksum. A request key already used for
    the same export returns it; for another it is ``ExportRequestBound``
    (the page's 409). Records ``campaign_archive_exported`` with the
    cutoff's campaign day as ``count``, no figure.
    """
    from .models import CampaignDailyFactSet

    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if retained is not None and (
        retained.report != REPORT or retained.campaign_id != campaign_id
    ):
        raise ValueError("Regeneration needs an archive of this campaign.")

    def admit(*args):
        """Both enqueue and its owning transaction recheck current authorization."""
        require_administrator(store, user_id)
        admit_campaign(campaign_id, mutating=True)
        return True

    with export_transaction(campaign_id):
        admit()
        previous = ExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            if (previous.report, previous.campaign_id) != (REPORT, campaign_id):
                raise ExportRequestBound("Export request identity is already bound.")
            return previous
        if retained is not None:
            fact_set_id = retained.fact_set_id
            parameters = dict(retained.parameters)
        else:
            offer = offer or archive_offer(campaign_id, now=campaign_now())
            if not offer.available or offer.campaign_id != campaign_id:
                raise FactUnavailable("The campaign archive is unavailable now.")
            fact_set_id = offer.fact_set_id
            parameters = {
                "cutoff_date": offer.cutoff_date.isoformat(),
                "final": offer.final,
                "year": offer.year,
            }
        facts = (
            CampaignDailyFactSet.objects.select_for_update()
            .filter(
                pk=fact_set_id,
                campaign_id=campaign_id,
                state="ready",
                population_scope="historical",
            )
            .first()
        )
        if facts is None:
            raise FactUnavailable("The archive's fact set is not ready.")
        identifier, correlation_id = uuid4(), uuid4()
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=user_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=admit,
        )
        request = ExportRequest.objects.create(
            id=identifier,
            campaign_id=campaign_id,
            requester_id=user_id,
            request_key=request_key,
            task_id=task.run_id,
            fact_set=facts,
            configuration_id=SystemConfiguration.objects.get().active_configuration_id,
            report=REPORT,
            format=FORMAT,
            browser_timezone=TIMEZONE,
            parameters=parameters,
            authorization_scope={"capability": Capability.CAMPAIGN_REPORT.value},
            actor_id=user_id,
            correlation_id=correlation_id,
        )
        pin_facts(facts.pk, parent_kind="export", parent_id=request.pk, admit=admit)
        start = request.campaign.active_configuration.start_date
        day = (date.fromisoformat(parameters["cutoff_date"]) - start).days + 1
        audit(
            Action.CAMPAIGN_ARCHIVE_EXPORTED,
            request,
            user_id,
            outcome=Outcome.STARTED,
            count=day,
        )
        return request


@dataclass(frozen=True)
class ArchiveDocument:
    """The worker's written, read-back archive, ready to store."""

    data: bytes
    item_count: int
    file_name: str


def archive_document(request, *, on_timeout=None):
    """Read, build, check, write and read back one archive request's file.

    Runs inside the worker's campaign read guard (a repeatable-read,
    read-only transaction): the read sees one snapshot, under the request's
    fact set and cutoff. The fact set's pin must still be held. A fact
    disagreement or a version the facts have not absorbed raises
    ``FactUnavailable``, so the run fails and is retried like any fact
    failure; a read-back refusal raises ``ArchiveWriteRefused``.
    """
    from parishkit.stewardship.archive.check import check_archive
    from parishkit.stewardship.archive.export import read_source, write_source
    from parishkit.stewardship.storage import StorageInvariantError

    from .models import CampaignFactPin

    if not CampaignFactPin.objects.filter(
        fact_set_id=request.fact_set_id, parent_kind="export", parent_id=request.pk
    ).exists():
        raise StorageInvariantError("Export calculation pin is unavailable.")
    source = read_source(
        request.fact_set_id,
        cutoff_date=date.fromisoformat(request.parameters["cutoff_date"]),
        final=request.parameters["final"],
        converted_at=request.created_at,
    )
    written = write_source(source, check=check_archive, on_timeout=on_timeout)
    return ArchiveDocument(written.data, written.families, written.name)


def render_archive(document, stream):
    """Copy the written archive to the stored file."""
    stream.write(document.data)
