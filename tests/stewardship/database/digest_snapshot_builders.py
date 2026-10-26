"""Retained Daily and Weekly digest snapshots, seeded for the saved-page tests.

Report emails replaced the digests (#1786), so no release writes a digest
snapshot any more; a database upgraded to this release still holds the ones
sent before. These builders write such snapshots the way an upgraded database
holds them: the rows are inserted with the digest guards off, as
``report_builders.digest_history`` writes occurrences and fulfillments. What
the rows hold is real: the Daily's facts are the fact-set storage's own,
calculated from the campaign's source by the participation calculation, and
its statistics inputs are the live capture, and the Weekly's observation is
the database's own projection (``stewardship_weekly_observation_v1``, kept
for the saved pages when the Weekly pipeline retired, #1834). Nothing here
calls a provider or sends mail.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from uuid import uuid4

from django.db import connection
from PIL import Image

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestReady,
    DailyDigestSnapshot,
)
from parishkit.stewardship.reports.facts import begin_fact_set
from parishkit.stewardship.reports.inputs import FactInputs
from parishkit.stewardship.reports.materialization import materialize_fact_set
from parishkit.stewardship.reports.statistics import calculate_statistics
from parishkit.stewardship.reports.statistics_selection import capture_statistics
from parishkit.stewardship.reports.weekly_digest import WeeklyInformation
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestSnapshot,
)
from parishkit.stewardship.reports.weekly_observation import decode_observation

from .report_builders import digest_kinds_admitted, seed_digests, triggers_off
from .source_builders import running_source_task
from .test_source_snapshots_postgresql import permit

# The instants the digests were captured at: a morning of the fixture
# campaign (October 1 to 31, 2054) and a Monday of it.
DAILY_AT = datetime(2054, 10, 10, 7, tzinfo=UTC)
WEEKLY_AT = datetime(2054, 10, 12, 8, tzinfo=UTC)
ADMINS = ["admin@example.org"]
WEEKLY_CANARY = "PRIVATE-WEEKLY-DISPATCH-CANARY"


def png(width=1760, height=640):
    """A plain PNG standing in for the participation chart the digest drew."""
    stream = BytesIO()
    Image.new("RGB", (width, height), "white").save(stream, format="PNG")
    return stream.getvalue()


def mode_of(campaign):
    """The namespace a digest of ``campaign`` was sent in, by its state."""
    campaign.refresh_from_db()
    return "testing" if campaign.state == "draft" else "production"


def preparation_values(campaign, definition_id, claim, *, phase):
    """The finished preparation's columns, for either digest kind."""
    definition = ScheduleDefinition.objects.get(pk=definition_id)
    mode = mode_of(campaign)
    return dict(
        campaign_id=campaign.pk,
        definition_id=definition.pk,
        revision_id=definition.current_revision_id,
        campaign_configuration_id=campaign.active_configuration_id,
        task_id=claim.run_id,
        mode=mode,
        rehearsal_epoch_id=uuid4() if mode == "testing" else None,
        cutoff=DAILY_AT,
        phase=phase,
        run_id=claim.run_id,
        task_fence=claim.fence,
        worker_id=claim.worker_id,
        version=1,
        actor_id=claim.worker_id,
        correlation_id=uuid4(),
    )


def claim_of(task):
    """The ownership claim of one synthetic running task."""
    return TaskClaim(task["task_id"], task["task_fence"], task["worker_id"])


def digest_definitions(harness, monkeypatch):
    """The campaign's Daily and Weekly definition IDs, added on first use.

    A database past the digest retirement (#1834) holds them converted to
    report emails, with the digest kind only on their immutable revisions,
    so they are seeded with the digest kinds admitted and retyped as
    reports afterwards (``digest_kinds_admitted``), and found again by
    their revisions' kind.
    """
    found = {
        row.current_revision.kind: row.pk
        for row in ScheduleDefinition.objects.filter(
            campaign=harness.campaign,
            current_revision__kind__in=("daily_digest", "weekly_digest"),
        ).select_related("current_revision")
    }
    if found:
        return found["daily_digest"], found["weekly_digest"]
    with digest_kinds_admitted():
        return seed_digests(harness.service.store, harness.campaign, monkeypatch)


@dataclass(frozen=True)
class SeededDaily:
    """A retained Daily digest: its snapshot, ready row (None if unbuilt)."""

    snapshot: DailyDigestSnapshot
    ready: DailyDigestReady | None
    chart: bytes

    @property
    def snapshot_id(self):
        """The id the saved page's address carries."""
        return self.snapshot.pk


def seed_daily(
    harness,
    monkeypatch,
    *,
    through=date(2054, 10, 9),
    built=True,
    comparison=None,
    comparison_chart=None,
):
    """Retain one Daily digest of ``harness``'s campaign through ``through``.

    ``built`` False leaves the ready row out, as a digest captured and never
    compiled. ``comparison`` is the retained Compared with past campaigns
    section (and ``comparison_chart`` its Families chart PNG).
    """
    campaign = harness.campaign
    daily, _ = digest_definitions(harness, monkeypatch)
    task = running_source_task()
    claim = claim_of(task)
    inputs = capture_statistics(campaign.pk)
    statistics = calculate_statistics(inputs)
    # The facts the digest was compiled from: the real calculation, stored
    # through the fact-set storage under a historical scope.
    facts = begin_fact_set(
        FactInputs(
            campaign.pk,
            "historical",
            statistics.source_id,
            statistics.submission_watermark,
            statistics.configuration_id,
            through,
            review_watermark=0,
        ),
        claim,
        admit=permit,
    )
    if facts.state != "ready":
        materialize_fact_set(facts.pk, claim, admit=permit)
    days = [
        facts.first_date + timedelta(days=offset)
        for offset in range((through - facts.first_date).days + 1)
    ]
    chart = png()
    with triggers_off():
        preparation = DailyDigestPreparation.objects.create(
            **preparation_values(campaign, daily, claim, phase="complete")
        )
        snapshot = DailyDigestSnapshot.objects.create(
            preparation=preparation,
            campaign_id=campaign.pk,
            configuration_id=_runtime_configuration_id(),
            source_id=statistics.source_id,
            population_scope="historical",
            submission_watermark=statistics.submission_watermark,
            timezone_configuration_id=statistics.configuration_id,
            through_date=through,
            observed_at=statistics.observed_at,
            statistics_inputs=inputs.canonical,
            covered_dates=[day.isoformat() for day in days],
            run_id=claim.run_id,
            fence=claim.fence,
            worker_id=claim.worker_id,
            actor_id=claim.worker_id,
            correlation_id=preparation.pk,
        )
        ready = None
        if built:
            ready = DailyDigestReady.objects.create(
                snapshot=snapshot,
                fact_set_id=facts.pk,
                recipient_configuration_id=snapshot.configuration_id,
                recipients=ADMINS,
                subject="Daily report",
                html="<p>Daily report</p>",
                text="Daily report",
                chart=chart,
                run_id=claim.run_id,
                fence=claim.fence,
                worker_id=claim.worker_id,
                comparison=comparison,
                comparison_families_chart=comparison_chart,
                actor_id=claim.worker_id,
                correlation_id=preparation.pk,
            )
    return SeededDaily(snapshot, ready, chart)


def _runtime_configuration_id():
    """The applied configuration a digest sent now would pin."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    return SystemConfiguration.objects.get().active_configuration_id


def weekly_selected(observation, *, after, reported, corrected):
    """The items a Weekly digest of ``observation`` sent, in page order.

    The rule the retired Weekly selection applied: a live request submitted
    after the earlier report's sequence ``after``, and a correction of a
    request already ``reported`` whose disposition is not yet ``corrected``
    (pairs of item and disposition), each in submission-time order.
    """
    information, corrections = [], []
    for item in observation.items:
        value = item.value
        if type(value) is WeeklyInformation:
            if item.sequence > after:
                information.append(value)
        elif (
            value.item_id in reported
            and (value.item_id, value.disposition) not in corrected
        ):
            corrections.append(value)

    def order(value):
        """The page's stable submission-time and identity order."""
        return value.submitted_at, value.item_id.int

    return sorted(information, key=order), sorted(corrections, key=order)


def seed_weekly(
    harness,
    monkeypatch,
    *,
    corrected=frozenset(),
    reported=frozenset(),
    after=0,
):
    """Retain one Weekly digest of the campaign's live requests.

    The observation is the database's live projection, retained as the
    digest retained it, and the selection is ``weekly_selected`` over it;
    ``after`` (the submission sequence the earlier report covered),
    ``reported`` and ``corrected`` are the history it follows, as an
    earlier report's coverage would have left it.
    """
    campaign = harness.campaign
    _, weekly = digest_definitions(harness, monkeypatch)
    claim = claim_of(running_source_task())
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_weekly_observation_v1(%s)::text", [campaign.pk]
        )
        document = json.loads(cursor.fetchone()[0])
    observation = decode_observation(document)
    information, corrections = weekly_selected(
        observation, after=after, reported=reported, corrected=corrected
    )
    with triggers_off():
        preparation = WeeklyDigestPreparation.objects.create(
            **preparation_values(campaign, weekly, claim, phase="complete")
        )
        snapshot = WeeklyDigestSnapshot.objects.create(
            preparation=preparation,
            campaign_id=campaign.pk,
            configuration_id=_runtime_configuration_id(),
            timezone_configuration_id=observation.configuration_id,
            source_id=observation.source_id,
            observed_at=observation.observed_at,
            submission_watermark=observation.watermark,
            after_watermark=after,
            observation=document,
            item_versions={},
            information=[str(item.item_id) for item in information],
            corrections=[[str(item.item_id), item.disposition] for item in corrections],
            recipients=ADMINS,
            run_id=claim.run_id,
            fence=claim.fence,
            worker_id=claim.worker_id,
            actor_id=claim.worker_id,
            correlation_id=preparation.pk,
        )
    return snapshot
