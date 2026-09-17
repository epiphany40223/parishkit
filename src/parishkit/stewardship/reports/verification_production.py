"""Bounded daily verification allocation; schedulers never read report values."""

from datetime import UTC
from uuid import uuid4

from django.db import connection
from django.db.models import BooleanField, Exists, Func, OuterRef, Value

from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .export_services import admit_campaign
from .models import CampaignDailyFactSet
from .verification_models import FactVerificationRequest

TASK_TYPE = "report_fact_verification"
INPUT_FIELDS = (
    "campaign_id",
    "population_scope",
    "source_id",
    "source_generation",
    "submission_watermark",
    "timezone_configuration_id",
    "through_date",
)


def produce_verifications(guard, *, limit=20):
    """One owner per generation/day, never reset a nonterminal owner's retry budget."""
    if (
        not isinstance(guard, SchedulerGuard)
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise TypeError("Fact verification requires an owned bounded scheduler.")
    if connection.in_atomic_block:
        raise StorageInvariantError(
            "Fact verification production owns its transaction."
        )
    guard.check()
    with work_transaction():
        today = _now().astimezone(UTC).date()
        requests = FactVerificationRequest.objects.filter(fact_set_id=OuterRef("pk"))
        candidates = (
            CampaignDailyFactSet.objects.alias(
                admitted=Func(
                    "campaign_id",
                    Value(True),
                    function="stewardship_export_admitted_v1",
                    output_field=BooleanField(),
                ),
                disposable=Func(
                    "pk",
                    function="stewardship_fact_disposable",
                    output_field=BooleanField(),
                ),
            )
            .filter(state="ready", admitted=True, disposable=False)
            .filter(~Exists(requests.filter(scheduled_day__gte=today)))
            .filter(
                ~Exists(requests.filter(task__chain_runs__state__in=NONTERMINAL_STATES))
            )
            .order_by("created_at", "pk")[:limit]
        )
        result = []
        for facts in candidates:
            guard.check()
            admit_campaign(facts.campaign_id, mutating=True)
            identifier = uuid4()

            def admit(action, status, request_id=identifier):
                """The producer owns only this root; SQL binds its exact request."""
                return (
                    action == "enqueue"
                    and status.task_type == TASK_TYPE
                    and status.domain_request_id == request_id
                )

            # The SQL request guard atomically protects/reselects the generation.
            # Compaction winning selection is a failed transaction, not permission
            # to allocate an owner for some newer generation instead.
            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=identifier,
                idempotency_key=identifier,
                actor_id=None,
                correlation_id=identifier,
                admit=admit,
            )
            FactVerificationRequest.objects.create(
                id=identifier,
                task_id=task.root_id,
                fact_set_id=facts.pk,
                scheduled_day=today,
                correlation_id=identifier,
                **{field: getattr(facts, field) for field in INPUT_FIELDS},
            )
            result.append(task.root_id)
        guard.check()
        return tuple(result)
