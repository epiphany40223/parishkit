"""Current post-cleanup readiness, separate from expensive impact enumeration."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from parishkit.stewardship.campaigns.activation_inputs import TokenPreparationInputs
from parishkit.stewardship.campaigns.activation_models import ProductionTokenPreparation
from parishkit.stewardship.campaigns.activation_tokens import (
    prepared_generation,
    require_current,
)
from parishkit.stewardship.campaigns.confirmation_models import ActivationImpactRevision
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.readiness import SourceReadiness, source_readiness
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration
from .campaign_mail_models import CampaignMailTest
from .go_live_configuration import ConfigurationReadiness, configuration_readiness
from .go_live_inputs import _catalogs, _configuration_pending, _receipts
from .go_live_progress import _current
from .integration_selection import integration_records


@dataclass(frozen=True)
class ConfirmationReadiness:
    """Bounded current metadata; never a reusable permission or enumerated plan."""

    actor_id: UUID
    transition: ProductionTransitionRequest
    campaign: Campaign
    observed_at: datetime
    target_state: str
    configuration: ConfigurationReadiness
    source: SourceReadiness
    generation_id: UUID
    impact_revision: int
    problems: tuple[str, ...]
    digest: str


def impact_revision():
    """Read constant-size, SQL-maintained evidence under the caller's work order."""
    require_work_order()
    value = ActivationImpactRevision.objects.values_list("version", flat=True).first()
    if value is None:
        raise StaleRecordError("Campaign impact evidence is unavailable.")
    return value


def collect_readiness(request, service, campaign_id, transition_id, preparation_id):
    """Re-read all nonenumerated prerequisites, including after a lock wait.

    A sealed completed cleanup request proves its original readiness admission.
    Only the deleted Testing details are represented by that immutable aggregate;
    configuration, source, integrations, population and prepared links are current.
    Impact counts are separately bound through the transactional revision.
    """
    require_work_order()
    actor, transition = _current(
        request, service, campaign_id, transition_id, passive=True
    )
    preparation = ProductionTokenPreparation.objects.get(
        pk=preparation_id, transition=transition
    )
    require_current(preparation)
    inputs = TokenPreparationInputs.retained(preparation)
    generation = prepared_generation(preparation)
    task = (
        TaskRun.objects.filter(root_id=preparation.task_id)
        .order_by("-retry_sequence")
        .first()
    )
    if (
        generation is None
        or not inputs.covers(generation)
        or task is None
        or task.state != "succeeded"
    ):
        raise StaleRecordError("Current inactive links must finish preparation first.")
    scope = _scope(campaign_id)
    applied = editable_configuration(service)
    document = applied.active_configuration.canonical_document
    source = source_readiness(scope)
    ministries, funds = _catalogs(source)
    configuration = configuration_readiness(
        document, campaign_id, ministries=ministries, funds=funds
    )
    problems = list(configuration.problems)
    if not source.ready:
        problems.append(source.reason)
    if _configuration_pending(applied.active_configuration_id):
        problems.append("configuration_pending")
    if CampaignMailTest.objects.filter(
        campaign_id=campaign_id, state__in=("queued", "submitting")
    ).exists():
        problems.append("family_test_mail_pending")
    if (
        Campaign.objects.exclude(pk=campaign_id)
        .filter(state__in=("scheduled", "active"))
        .exists()
    ):
        problems.append("other_campaign_live")
    receipts = _receipts(integration_records(document), problems)
    interval = scope.campaign.active_configuration
    target = "scheduled" if scope.instant < interval.starts_at else "active"
    if scope.instant >= interval.ends_at:
        problems.append("campaign_closed")
    revision = impact_revision()
    binding = {
        "configuration": applied.active_configuration.digest,
        "runtime": scope.runtime.version,
        "campaign": (str(campaign_id), scope.campaign.version),
        "transition": (str(transition.pk), transition.version),
        "testing_proof": (
            str(transition.aggregate_id),
            transition.aggregate.readiness_digest,
            str(transition.cleanup_manifest.pk),
        ),
        "preparation": (str(preparation.pk), asdict(inputs)),
        "generation": (str(generation.pk), generation.version),
        "task": (str(task.pk), task.version),
        "source": asdict(source),
        "receipts": receipts,
        "impact_revision": revision,
        "target": target,
        "problems": problems,
    }
    digest = hashlib.sha256(
        json.dumps(binding, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return ConfirmationReadiness(
        actor.identity,
        transition,
        scope.campaign,
        scope.instant,
        target,
        configuration,
        source,
        generation.pk,
        revision,
        tuple(problems),
        digest,
    )
