"""Exact daily fact ownership and atomic compiled-content/recipient handoff."""

from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.web.digest_content import validate_digest_body

from .daily_digest import DailyDigestContent, DailyDigestDocument
from .digest_capture import retained_dates, retained_statistics
from .digest_models import DailyDigestReady, DailyDigestSnapshot
from .digest_ownership import (
    TASK_TYPE,
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .documents import participation_document
from .facts import FactUnavailable, begin_fact_set, fact_inputs
from .models import CampaignDailyFactSet
from .recovery import recover_fact_set
from .retention import pin_facts


def admit_daily_facts(claim, action, inputs):
    """Recheck the same protected observation at every calculation chunk boundary."""
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    current_preparation(preparation)
    snapshot = DailyDigestSnapshot.objects.get(preparation=preparation)
    return preparation.phase == "facts" and inputs == fact_inputs(snapshot)


def begin_daily_facts(claim):
    """Reuse exact ready data; incomplete foreign roots retain their own recovery."""
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    current_preparation(preparation)
    snapshot = DailyDigestSnapshot.objects.get(preparation=preparation)

    def admit(action, inputs):
        """Bind all calculation primitives to this owned snapshot and live fence."""
        return admit_daily_facts(claim, action, inputs)

    facts = begin_fact_set(fact_inputs(snapshot), claim, admit=admit)
    if facts.state == "ready":
        return facts
    owner = TaskRun.objects.get(pk=facts.task_id)
    if owner.root_id != preparation.task_id and (
        owner.task_type not in {TASK_TYPE, "report_exact_export"}
        or TaskRun.objects.filter(
            root_id=owner.root_id, state__in=NONTERMINAL_STATES
        ).exists()
    ):
        raise FactUnavailable("Another report owner retains these exact inputs.")
    if (facts.task_id, facts.task_fence, facts.worker_id) != (
        claim.run_id,
        claim.fence,
        claim.worker_id,
    ):
        facts = recover_fact_set(facts.pk, claim, admit=admit)
    return facts


def load_daily_document(claim, fact_set_id):
    """Detach data in a guarded effect; expensive chart rendering happens after it."""
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    current_preparation(preparation)
    snapshot = DailyDigestSnapshot.objects.select_related(
        "configuration__parish", "timezone_configuration"
    ).get(preparation=preparation)
    facts = CampaignDailyFactSet.objects.get(pk=fact_set_id, state="ready")
    if preparation.phase != "facts" or fact_inputs(snapshot) != fact_inputs(facts):
        raise FactUnavailable("Daily report facts do not match their retained input.")
    return retained_daily_document(snapshot, facts)


def retained_daily_document(snapshot, facts):
    """Load the same pinned observation for compilation and authorized web reads."""
    if facts.state != "ready" or fact_inputs(snapshot) != fact_inputs(facts):
        raise FactUnavailable("Daily report facts do not match their retained input.")
    document = participation_document(
        facts,
        parish_name=snapshot.configuration.parish.name,
        browser_timezone=snapshot.timezone_configuration.timezone,
        requested_at=snapshot.observed_at,
    )
    return DailyDigestDocument(
        snapshot.pk, document, retained_statistics(snapshot), retained_dates(snapshot)
    )


def retain_daily_content(claim, document, content):
    """Pin exact facts and compiled bytes before any per-Admin outbox allocation."""
    if not isinstance(document, DailyDigestDocument) or not isinstance(
        content, DailyDigestContent
    ):
        raise TypeError("Daily publication requires compiled exact report content.")
    validate_digest_body(content.html, content.text, content.chart.data)
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(preparation)
    snapshot = DailyDigestSnapshot.objects.get(preparation=preparation)
    if snapshot.pk != document.snapshot_id or preparation.phase != "facts":
        raise PermissionError("Daily publication differs from its owned observation.")
    facts = CampaignDailyFactSet.objects.select_for_update().get(
        pk=document.participation.fact_set_id, state="ready"
    )
    if fact_inputs(facts) != fact_inputs(snapshot):
        raise FactUnavailable("Daily publication requires the exact retained facts.")
    recipients = list(
        AddressRule.objects.filter(
            configuration_id=scope.runtime.active_configuration_id,
            roles__contains=["administrator"],
        )
        .order_by("email")
        .values_list("email", flat=True)
    )
    ready = DailyDigestReady.objects.create(
        snapshot=snapshot,
        fact_set=facts,
        recipient_configuration_id=scope.runtime.active_configuration_id,
        recipients=recipients,
        subject=content.subject,
        html=content.html,
        text=content.text,
        chart=content.chart.data,
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=preparation.pk,
    )
    pin_facts(
        facts.pk,
        parent_kind="digest",
        parent_id=snapshot.pk,
        admit=lambda action, inputs: admit_daily_facts(claim, action, inputs),
    )
    checkpoint_preparation(claim, phase="fanout")
    return ready
