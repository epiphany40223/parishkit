"""Answer-free form issuance, trusted reconstruction and expiry/pin ownership.

Final-submit and form issuance share its locked admission path. Accepting a
UUID or a browser digest is never a
substitute for rebuilding inputs from retained source/configuration versions.
"""

from dataclasses import dataclass

from django.db.models import F

from parishkit.stewardship.accounts.authentication import runtime as auth_runtime
from parishkit.stewardship.accounts.family_authentication import (
    _scope,
    authenticated_family,
)
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.source.pins import pin_snapshot, release_snapshot_pin
from parishkit.stewardship.source.snapshot_models import (
    SourceCurrent,
    SourceSnapshot,
    SourceSnapshotPin,
)
from parishkit.stewardship.storage import StorageInvariantError

from .inputs import FORM_SCHEMA, PROJECTION_VERSION, CensusInputs, FormInputsUnavailable
from .models import FamilyFormBaseline, Submission
from .source_inputs import load_census_inputs


class FamilyAdmissionDenied(PermissionError):
    """Expired/lost Family access must not return refreshed private form data."""


class RehearsalAcknowledgmentRequired(PermissionError):
    """Testing entry requires a deliberate acknowledgment before showing answers."""


@dataclass(frozen=True)
class FormBaseline:
    """Transient materialized form inputs alongside the retained metadata record."""

    baseline: FamilyFormBaseline
    inputs: CensusInputs


def admitted_family(request, service):
    """Join the common work order before current runtime/Family/session row locks."""
    require_work_order()
    SystemConfiguration.objects.select_for_update().get()
    if not auth_runtime().configured():
        raise FamilyAdmissionDenied("Family access is unavailable.")
    if authenticated_family(request, service=service) is None:
        raise FamilyAdmissionDenied("Family access is unavailable.")
    current = _scope(service)
    if current is None:
        raise FamilyAdmissionDenied("Family access is unavailable.")
    configuration, campaign, _, _ = current
    family = FamilyCampaign.objects.select_for_update().get(
        pk=request.family_session.family_id
    )
    return configuration, campaign, family, request.family_session


def effective_submission(family, session):
    """Select the exact namespace; a prior rehearsal can never prefill live data."""
    require_work_order()
    return (
        Submission.objects.filter(
            family=family,
            mode="test" if session.mode == "testing" else "live",
            rehearsal_epoch_id=session.rehearsal_epoch_id,
        )
        .order_by("-family_version")
        .first()
    )


def _pin_admission(action, value):
    """Pins are internal to an already admitted, ordered local response mutation."""
    require_work_order()
    if action not in {"pin", "unpin"}:
        raise StorageInvariantError("Unexpected form source protection action.")
    return True


def end_baseline(baseline, *, state):
    """End an exact open baseline and release only its own expiring source pin."""
    require_work_order()
    if state not in {"replaced", "submitted", "cancelled", "expired"}:
        raise ValueError("A terminal form baseline state is required.")
    baseline = FamilyFormBaseline.objects.select_for_update().get(pk=baseline.pk)
    if baseline.state != "open":
        return False
    FamilyFormBaseline.objects.filter(pk=baseline.pk).update(
        state=state, ended_at=database_now(), version=F("version") + 1
    )
    pin = SourceSnapshotPin.objects.filter(
        snapshot_id=baseline.source_id,
        parent_kind="form_baseline",
        parent_id=baseline.pk,
    ).first()
    if pin is None:
        raise StorageInvariantError("The form source protection is missing.")
    release_snapshot_pin(
        pin.pk, parent_kind="form_baseline", parent_id=baseline.pk, admit=_pin_admission
    )
    return True


def cancel_session_baselines(session_ids):
    """Release unfinished form inputs before their owning sessions are removed."""
    require_work_order()
    rows = FamilyFormBaseline.objects.filter(
        family_session_id__in=session_ids, state="open"
    ).order_by("pk")
    for row in rows:
        end_baseline(row, state="cancelled")


def issue_baseline(request, service, *, testing_acknowledged=False):
    """Atomically bind current reviewed inputs to this session, never to a draft.

    In Testing, the first baseline is issued only after the CSRF-protected entry
    acknowledgment. Its metadata is the acknowledgment evidence for that exact
    session/epoch; it contains no answers. Later refreshes in the same session
    may reuse that acknowledgment. Final Submit requires its separate explicit
    test acknowledgment and never infers it from this record.
    """
    if type(testing_acknowledged) is not bool:
        raise TypeError("Testing acknowledgment must be explicit.")
    with work_transaction():
        configuration, campaign, family, session = admitted_family(request, service)
        mode = "test" if session.mode == "testing" else "live"
        owned = FamilyFormBaseline.objects.filter(
            family=family,
            family_session_id=session.pk,
            mode=mode,
            rehearsal_epoch_id=session.rehearsal_epoch_id,
        )
        if mode == "test" and not testing_acknowledged and not owned.exists():
            raise RehearsalAcknowledgmentRequired(
                "Confirm Testing mode before continuing."
            )
        current = SourceCurrent.objects.select_for_update().get()
        if current.snapshot_id is None:
            raise FormInputsUnavailable("The Family form inputs are unavailable.")
        snapshot = (
            SourceSnapshot.objects.select_for_update()
            .only("id", "state", "compacted_at")
            .get(pk=current.snapshot_id)
        )
        if snapshot.state != "promoted" or snapshot.compacted_at is not None:
            raise FormInputsUnavailable("The Family form inputs are unavailable.")
        inputs = load_census_inputs(
            snapshot.pk,
            family.family_duid,
            configuration=campaign.active_configuration.values,
        )
        prior = effective_submission(family, session)
        # Pin the replacement before releasing the old protection. Compaction
        # cannot interleave with either this snapshot lock or the work order.
        baseline = FamilyFormBaseline.objects.create(
            family=family,
            family_session_id=session.pk,
            mode=mode,
            rehearsal_epoch_id=session.rehearsal_epoch_id,
            source=snapshot,
            configuration=configuration.active_configuration,
            prior_submission=prior,
            form_schema=FORM_SCHEMA,
            projection_version=PROJECTION_VERSION,
            projection_digest=inputs.projection_digest,
            definition_digest=inputs.definition_digest,
            expires_at=session.expires_at,
            actor_id=family.pk,
        )
        pin_snapshot(
            snapshot.pk,
            parent_kind="form_baseline",
            parent_id=baseline.pk,
            admit=_pin_admission,
            expires_at=baseline.expires_at,
        )
        for previous in owned.filter(state="open").exclude(pk=baseline.pk):
            end_baseline(previous, state="replaced")
        return FormBaseline(baseline, inputs)
