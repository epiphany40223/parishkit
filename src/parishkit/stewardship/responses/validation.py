"""Trusted baseline reconstruction and relevant-input concurrency comparison."""

from dataclasses import dataclass
from uuid import UUID

from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceSnapshot,
    SourceSnapshotPin,
)
from parishkit.stewardship.storage import StorageInvariantError

from .baselines import effective_submission
from .inputs import FORM_SCHEMA, PROJECTION_VERSION, CensusInputs, FormInputsUnavailable
from .models import FamilyFormBaseline, Submission
from .source_inputs import load_census_inputs


class BaselineUnavailable(ValueError):
    """Missing, expired or cross-session references cannot prove reviewed inputs."""


@dataclass(frozen=True)
class BaselineValidation:
    """Transient comparison result; the final writer must keep the work lock held."""

    baseline: FamilyFormBaseline
    reviewed: CensusInputs
    current: CensusInputs
    validation_snapshot_id: UUID
    prior_submission: Submission | None
    review_required: bool


def _fresh_required():
    """Wrong-owner and missing references have the same non-enumerating error."""
    raise BaselineUnavailable("A fresh authorized Family form is required.")


def validate_baseline(identifier, *, family, session, campaign):
    """Rebuild retained/current inputs while the final writer owns serialization.

    The caller must just have obtained Family/session/Campaign from
    admitted_family in this same transaction. It must perform complete answer
    validation and all writes before releasing the common lock. No browser
    digest, field list or claimed snapshot is accepted by this interface.
    """
    require_work_order()
    if not isinstance(identifier, UUID):
        _fresh_required()
    baseline = (
        FamilyFormBaseline.objects.select_for_update()
        .filter(
            pk=identifier,
            family=family,
            family_session_id=session.pk,
            mode="test" if session.mode == "testing" else "live",
            rehearsal_epoch_id=session.rehearsal_epoch_id,
            state="open",
            expires_at__gt=database_now(),
        )
        .first()
    )
    if (
        baseline is None
        or baseline.form_schema != FORM_SCHEMA
        or baseline.projection_version != PROJECTION_VERSION
    ):
        _fresh_required()
    retained = (
        SourceSnapshot.objects.select_for_update()
        .only("id", "state", "compacted_at")
        .get(pk=baseline.source_id)
    )
    if (
        retained.state != "promoted"
        or retained.compacted_at is not None
        or not SourceSnapshotPin.objects.filter(
            snapshot_id=retained.pk,
            parent_kind="form_baseline",
            parent_id=baseline.pk,
            expires_at=baseline.expires_at,
        ).exists()
    ):
        _fresh_required()
    definition = CampaignConfiguration.objects.get(
        configuration_id=baseline.configuration_id, record_id=campaign.pk
    )
    reviewed = load_census_inputs(
        retained.pk, family.family_duid, configuration=definition.values
    )
    if (
        reviewed.projection_digest != baseline.projection_digest
        or reviewed.definition_digest != baseline.definition_digest
    ):
        raise StorageInvariantError("The retained form inputs cannot be verified.")
    selected = SourceCurrent.objects.select_for_update().get()
    if selected.snapshot_id is None:
        raise FormInputsUnavailable("The Family form inputs are unavailable.")
    current_snapshot = (
        SourceSnapshot.objects.select_for_update()
        .only("id", "state", "compacted_at")
        .get(pk=selected.snapshot_id)
    )
    if (
        current_snapshot.state != "promoted"
        or current_snapshot.compacted_at is not None
    ):
        raise FormInputsUnavailable("The Family form inputs are unavailable.")
    current = load_census_inputs(
        current_snapshot.pk,
        family.family_duid,
        configuration=campaign.active_configuration.values,
    )
    prior = effective_submission(family, session)
    return BaselineValidation(
        baseline,
        reviewed,
        current,
        current_snapshot.pk,
        prior,
        current.projection_digest != reviewed.projection_digest
        or baseline.prior_submission_id != (prior.pk if prior else None),
    )
