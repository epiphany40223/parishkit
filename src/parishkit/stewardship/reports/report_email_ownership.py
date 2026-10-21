"""Opaque report email preparation ownership (report-emails spec, #1786).

As the digests' was: the scheduler allocates one
finite preparation per report email with sends due, every worker page
binds to that same root and rechecks the current campaign, revision and
Testing epoch, and an obsolete preparation may only cancel itself. Unlike a
digest, a campaign has up to 20 report emails, so a preparation names its
definition, and a report may send before its campaign starts and after it
closes, until it is archived.

Nothing allocates a preparation until the conversion compiles the producer
into the scheduler and admits ``report`` schedules.
"""

from uuid import UUID, uuid4

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
)
from parishkit.stewardship.campaigns.family_schedule_planning import _planning_epoch
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    CampaignConfiguration,
    CampaignWorkGate,
    RestoreDeliveryHold,
)
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.admission import WorkScope, _scope
from parishkit.stewardship.jobs.loop_settings import LoopSettings
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .report_email_models import ReportEmailPreparation

TASK_TYPE = "report_email_prepare"
TERMINAL_PHASES = ("complete", "cancelled")
# A report send's occurrence target, as a digest's.
TARGET = "admins"


def report_held(campaign_id, scope, *, gated, catching_up):
    """Whether report sends are held for ``scope``'s runtime and campaign.

    The Python copy of ``stewardship_report_email_scope_v1``: unlike the
    digests' planning window, a report may send before its campaign starts
    and after it closes, until it is archived (decision 9), so only the
    lifecycle state, the restore review, the work gate and Production's
    activation catch-up hold it; the go-live gate and the Testing epoch are
    checked with the credentials (``_planning_epoch``). ``gated`` and
    ``catching_up`` are read only when the conditions before them pass.
    """
    campaign, runtime = scope.campaign, scope.runtime
    production = runtime.mode == "production"
    return (
        campaign is None
        or runtime.current_campaign_id != campaign_id
        or runtime.restore_review_required
        or (runtime.mode == "testing" and campaign.state != "draft")
        or (production and campaign.state not in {"scheduled", "active", "closed"})
        or gated()
        or (production and catching_up())
    )


def report_scope(campaign_id):
    """The locked current scope and Testing epoch a report send runs in.

    Raises PermissionError where the database's report scope would refuse.
    """
    require_work_order()
    scope = _scope(campaign_id)
    if report_held(
        campaign_id,
        scope,
        gated=lambda: (
            CampaignWorkGate.objects.filter(campaign_id=campaign_id)
            .exclude(state="released")
            .exists()
        ),
        catching_up=lambda: ActivationCatchUpDemand.objects.filter(
            campaign_id=campaign_id, completed_at__isnull=True
        ).exists(),
    ):
        raise PermissionError("Report sends are held.")
    credentials = (
        CampaignCredentialState.objects.select_for_update()
        .filter(campaign_id=campaign_id, go_live_gate=False)
        .first()
    )
    return scope, _planning_epoch(
        scope.runtime, credentials, campaign_id, allow_missing_epoch=False
    )


def snapshot_report_scope(settings):
    """The report scope from one scheduler loop's LoopSettings, before the lock.

    The same decision as ``report_scope``, on rows read without locks, for
    the producer's "anything to do?" read only (#715).
    """
    from parishkit.stewardship.campaigns.runtime import _now

    runtime = settings.runtime
    if runtime is None or runtime.active_configuration_id is None:
        raise PermissionError("Background work requires applied configuration.")
    campaign_id = runtime.current_campaign_id
    scope = WorkScope(runtime, settings.campaign, _now())
    if report_held(
        campaign_id,
        scope,
        gated=lambda: settings.gated,
        catching_up=lambda: settings.catching_up,
    ):
        raise PermissionError("Report sends are held.")
    return scope, _planning_epoch(
        runtime, settings.credentials, campaign_id, allow_missing_epoch=False
    )


def bound_preparation(status):
    """Check persisted root/run metadata instead of trusting a broker payload."""
    require_work_order()
    row = ReportEmailPreparation.objects.filter(
        pk=status.domain_request_id, task_id=status.root_id
    ).first()
    if (
        row is None
        or status.task_type != TASK_TYPE
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=row.task_id,
            task_type=TASK_TYPE,
            domain_request_id=row.pk,
            state=status.state,
            version=status.version,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Report preparation ownership is unavailable.")
    return row


def current_preparation(row):
    """Return the admitted current scope, or reject an obsolete preparation."""
    require_work_order()
    scope, epoch = report_scope(row.campaign_id)
    configuration = scope.campaign.active_configuration
    if (
        row.phase in TERMINAL_PHASES
        or row.mode != scope.runtime.mode
        or row.rehearsal_epoch_id != (epoch.pk if epoch else None)
        or not CampaignConfiguration.objects.filter(
            pk=row.campaign_configuration_id,
            record_id=row.campaign_id,
            timezone=configuration.timezone,
            start_date=configuration.start_date,
            end_date=configuration.end_date,
        ).exists()
        or not ScheduleDefinition.objects.filter(
            pk=row.definition_id,
            campaign_id=row.campaign_id,
            current_revision_id=row.revision_id,
            kind="report",
        ).exists()
    ):
        raise PermissionError("Report preparation no longer matches current scope.")
    return scope


def checkpoint_preparation(claim, *, phase, **changes):
    """Advance one fenced page; the cursor and the chosen send never rewind.

    ``changes`` sets the cutoff, cursor, occurrence or recorded inputs; a
    field left out keeps its value. The database's guard admits only the
    transitions the report pipeline allows.
    """
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    if phase != "cancelled":
        current_preparation(row)
    fields = {name: getattr(row, name) for name in changes} | {
        name: value for name, value in changes.items() if value is not None
    }
    updated = ReportEmailPreparation.objects.filter(
        pk=row.pk, version=row.version
    ).update(
        phase=phase,
        run_id=claim.run_id,
        task_fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=row.pk,
        version=row.version + 1,
        **fields,
    )
    if updated != 1:
        raise StorageInvariantError("Report preparation lost its page version.")
    return ReportEmailPreparation.objects.get(pk=row.pk)


def covered_slots(definition, mode, cycle, keys):
    """The slot keys among ``keys`` that owe ``definition`` no new send.

    A slot is covered by a fulfillment, an unreviewed or assumed-delivered
    restore hold, a post-close resolution, or an occurrence of the current
    revision in ``mode`` and Production ``cycle`` (0 in Testing). The
    scheduler allocates a preparation for any due slot outside this set
    (``ReportEmailProducer._uncovered``), and the Campaign calendar asks
    the same question to show a catch-up still owed (R43). The calendar
    does not ask the scheduler's ``DUE`` question, so a send that question
    alone finds is not shown until the scheduler takes it.
    """
    from parishkit.stewardship.campaigns.postclose_coverage import resolved_slots
    from parishkit.stewardship.campaigns.schedule_models import (
        ScheduleFulfillment,
        ScheduleOccurrence,
    )

    covered = set(
        ScheduleFulfillment.objects.filter(
            definition=definition, mode=mode, target="admins", slot__in=keys
        ).values_list("slot", flat=True)
    )
    covered |= set(
        RestoreDeliveryHold.objects.filter(
            definition=definition,
            mode=mode,
            target="admins",
            slot__in=keys,
            state__in=("unreviewed", "assumed_delivered"),
        ).values_list("slot", flat=True)
    )
    # Only this Production cycle's occurrences cover a slot, as in the dates
    # phase (report_email_planning._excluded): a withdrawn cycle's retired
    # sends are owed again after the next go-live.
    covered |= set(
        ScheduleOccurrence.objects.filter(
            revision_id=definition.current_revision_id,
            mode=mode,
            target="admins",
            slot__in=keys,
            production_cycle=cycle,
        ).values_list("slot", flat=True)
    )
    covered |= {
        item["slot"] for item in resolved_slots(definition.pk, mode, slots=keys)
    }
    return covered


# The scheduler's question for one report: an occurrence (which the
# schedule planner materializes, as it does a digest's) that is due, that
# no preparation owns and nothing covers, or a replaced revision's sends to
# coalesce. Plain reads, so it can run before and again under the lock.
DUE = """SELECT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
    WHERE o.revision_id=%s AND o.mode=%s AND o.target='admins'
      AND o.state='pending' AND o.task_id IS NULL AND o.outbox_id IS NULL
      AND o.due_at<=%s AND o.slot !~ '^manual:'
      AND NOT EXISTS(SELECT 1 FROM stewardship_report_email_preparation p
          WHERE p.occurrence_id=o.id)
      AND NOT stewardship_schedule_slot_excluded_v1(
          o.definition_id,o.mode,o.target,o.slot))
    OR EXISTS(SELECT 1 FROM stewardship_report_email_predecessors_v1(%s,%s,%s))"""


class ReportEmailProducer:
    """Allocate one metadata root for a report with sends due, never render or send.

    One report per call, in definition order, the first that is due and has
    no open preparation; its worker covers every due slot of that report.
    """

    def __init__(self, worker_id):
        """Require an opaque scheduler identity, not an interactive user session."""
        if not isinstance(worker_id, UUID):
            raise ValueError("Report production requires a scheduler identity.")
        self.worker_id = worker_id
        # Per report revision and namespace, the last slot known covered, so
        # a loop pass reads at most one page of slots per report (#715).
        self.cursors = {}

    def _uncovered(self, definition, scope, epoch_id):
        """Whether a send of ``definition`` due by now has no occurrence yet.

        The report's own preparation creates its occurrences (spec, "Report
        pipeline"), so the producer allocates one when the evaluator gives a
        due slot with no fulfillment, restore hold, post-close resolution or
        occurrence of the current revision. Pages of at most 100 candidate
        sends; the cursor passes only fully covered pages, and is forgotten
        whenever the revision, namespace or restore holds change.
        """
        from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan

        mode = scope.runtime.mode
        cycle = scope.campaign.production_cycle if mode == "production" else 0
        holds = RestoreDeliveryHold.objects.filter(
            definition=definition, mode=mode, target="admins"
        )
        key = (
            definition.current_revision_id,
            mode,
            cycle,
            epoch_id,
            scope.campaign.active_configuration_id,
            tuple(holds.order_by("id").values_list("id", "version")),
        )
        plan = SchedulePlan.from_values(
            definition.current_revision.values,
            scope.campaign.active_configuration.values,
        )
        after = self.cursors.get(key)
        while True:
            page = plan.page(through=scope.instant, after=after, limit=100)
            keys = {slot.key for slot in page.slots}
            if keys - covered_slots(definition, mode, cycle, keys):
                return True
            if page.cursor is not None:
                self.cursors[key] = after = page.cursor
            if page.exhausted:
                return False

    def _due(self, campaign_id, scope, epoch):
        """The first report definition with a send to prepare, or None."""
        mode = scope.runtime.mode
        epoch_id = epoch.pk if epoch else None
        open_roots = TaskRun.objects.filter(state__in=NONTERMINAL_STATES).values(
            "root_id"
        )
        busy = (
            ReportEmailPreparation.objects.filter(mode=mode, task_id__in=open_roots)
            .exclude(phase__in=TERMINAL_PHASES)
            .values("definition_id")
        )
        definitions = (
            ScheduleDefinition.objects.select_related("current_revision")
            .filter(
                campaign_id=campaign_id,
                kind="report",
                current_revision__isnull=False,
            )
            .exclude(pk__in=busy)
            .order_by("pk")
        )
        for definition in definitions:
            with connection.cursor() as cursor:
                cursor.execute(
                    DUE,
                    [
                        definition.current_revision_id,
                        mode,
                        scope.instant,
                        definition.pk,
                        mode,
                        epoch_id,
                    ],
                )
                if cursor.fetchone()[0]:
                    return definition
            if self._uncovered(definition, scope, epoch_id):
                return definition
        return None

    def __call__(self, guard, *, settings=None):
        """Decide on the loop's snapshot first, then again under the work lock."""
        if not isinstance(guard, SchedulerGuard):
            raise TypeError("Report production requires scheduler ownership.")
        if connection.in_atomic_block:
            raise StorageInvariantError("Report production must own its transaction.")
        guard.check()
        settings = LoopSettings() if settings is None else settings
        try:
            scope, epoch = snapshot_report_scope(settings)
        except PermissionError:
            return ()
        if self._due(settings.campaign_id, scope, epoch) is None:
            return ()
        guard.check()
        with work_transaction():
            campaign_id = SystemConfiguration.objects.values_list(
                "current_campaign_id", flat=True
            ).first()
            try:
                scope, epoch = report_scope(campaign_id)
            except PermissionError:
                return ()
            definition = self._due(campaign_id, scope, epoch)
            if definition is None:
                return ()
            guard.check()
            identifier = uuid4()
            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=identifier,
                actor_id=self.worker_id,
                correlation_id=identifier,
                idempotency_key=identifier,
                admit=lambda *args: True,
            )
            ReportEmailPreparation.objects.create(
                id=identifier,
                campaign_id=campaign_id,
                definition_id=definition.pk,
                revision_id=definition.current_revision_id,
                campaign_configuration_id=scope.campaign.active_configuration_id,
                task_id=task.run_id,
                mode=scope.runtime.mode,
                rehearsal_epoch_id=epoch.pk if epoch else None,
                cutoff=scope.instant,
                actor_id=self.worker_id,
                correlation_id=identifier,
            )
            guard.check()
            return (task,)
