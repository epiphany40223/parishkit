"""Authorized Ministry follow-up edits; never a Family answer or a roster write."""

from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID, uuid5

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import check_version

from .models import (
    CONTACT_CHANNELS,
    OPEN_STATES,
    RESOLVED_OUTCOMES,
    STAFF_STATES,
    MinistryRequest,
    MinistryWorkflowRevision,
)

MAX_NOTES, MAX_CONTACT_NOTES, MAX_BULK = 5000, 2000, 200


@dataclass(frozen=True)
class WorkflowChange:
    """The complete resulting workflow of one edit, validated without a database."""

    assignee_id: UUID | None
    state: str
    outcome: str | None
    notes: str
    contact_channel: str | None = None
    contact_at: datetime | None = None
    contact_notes: str = ""

    def __post_init__(self):
        """Mirror the revision constraints so a bad form is a 400, not a 503."""
        # An open state has no outcome; each closed state admits only its own.
        outcomes = {
            "resolved": RESOLVED_OUTCOMES,
            "closed_no_response": ("no_response",),
        }
        contact = (self.contact_channel, self.contact_at)
        if (
            not isinstance(self.assignee_id, UUID | None)
            or not isinstance(self.notes, str)
            or not isinstance(self.contact_notes, str)
            or len(self.notes) > MAX_NOTES
            or len(self.contact_notes) > MAX_CONTACT_NOTES
            or self.state not in STAFF_STATES
            or self.outcome not in outcomes.get(self.state, (None,))
            or (self.state == "new" and self.assignee_id is not None)
            or (self.state == "assigned" and self.assignee_id is None)
            or (self.outcome == "other" and not self.notes.strip())
            or (contact == (None, None)) != (None in contact)
            or (contact == (None, None) and self.contact_notes)
            or (self.contact_channel not in (None, *CONTACT_CHANNELS))
            or (
                self.contact_at is not None
                and (
                    not isinstance(self.contact_at, datetime)
                    or self.contact_at.utcoffset() is None
                )
            )
        ):
            raise ValueError("Invalid Ministry follow-up change.")
        bounded_text(self.notes)
        bounded_text(self.contact_notes)


def authorize_ministry(store, actor_id, ministry_duid):
    """Reload coherent policy; old sessions and a known request UUID are not grants."""
    principal = current_principal(store, actor_id)
    if not allows(principal, Capability.MINISTRY_FOLLOWUP, ministry_id=ministry_duid):
        raise PermissionError("Ministry follow-up access is unavailable.")
    return principal


def latest_revision(request_id):
    """Newest Staff edit across same-intent Family resubmissions, or None.

    A resubmission replaces the request row; its notes remain with the
    superseded predecessor until a later edit chains a revision here.
    """
    rows = MinistryWorkflowRevision.objects.raw(
        "SELECT r.* FROM stewardship_ministry_workflow_chain_v1(%s) c "
        "JOIN stewardship_ministry_revision r ON r.request_id=c.request_id "
        "ORDER BY c.depth,r.expected_version DESC LIMIT 1",
        [request_id],
    )
    # A RawQuerySet is always truthy and cannot be indexed when empty.
    return next(iter(rows), None)


def _revise(store, actor_id, target, expected_version, request_key, change, system):
    """Append one revision, its exact projection and audit for a locked request.

    SQL independently checks authority, scope, replay uniqueness and the paired
    history/projection/audit, and stamps the attribution and resolution times.
    """
    intent = dict(
        request_id=target.pk, expected_version=expected_version, **asdict(change)
    )
    previous = MinistryWorkflowRevision.objects.filter(
        actor_id=actor_id, request_key=request_key
    ).first()
    if previous is not None:
        if any(getattr(previous, field) != value for field, value in intent.items()):
            raise ValueError("This request key is already bound.")
        return previous
    check_version(target, expected_version)
    # Closing always advances the version, so this catches only a form that was
    # rendered from an already closed, cancelled or superseded request.
    if target.state not in OPEN_STATES:
        raise StaleRecordError("This Ministry request is no longer open.")
    required = {"joined": "join", "leave_confirmed": "leave"}
    if required.get(change.outcome, target.action) != target.action:
        raise ValueError("This outcome does not match the requested action.")
    if change.contact_at is not None and change.contact_at > database_now():
        raise ValueError("A contact attempt cannot be in the future.")
    if change.assignee_id is not None:
        try:
            authorize_ministry(store, change.assignee_id, target.ministry_duid)
        except (PermissionError, ObjectDoesNotExist):
            raise ValueError("The assignee cannot follow up this Ministry.") from None
    revision = MinistryWorkflowRevision.objects.create(
        actor_id=actor_id, request_key=request_key, **intent
    )
    MinistryRequest.objects.filter(pk=target.pk).update(
        state=change.state,
        outcome=change.outcome,
        assignee_id=change.assignee_id,
        version=F("version") + 1,
    )
    record_action(
        Action.MINISTRY_REQUEST_UPDATED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=revision.pk,
        parish_id=system.active_configuration.parish.pk,
        campaign_id=target.submission.campaign_id,
        context={
            "outcome": Outcome.CHANGED,
            "before_version": expected_version,
            "after_version": expected_version + 1,
            "ministry_duid": target.ministry_duid,
        },
    )
    return revision


def _targets(store, actor_id, versions):
    """Lock live requests in one stable order and authorize each Ministry."""
    rows = list(
        MinistryRequest.objects.select_for_update(of=("self",))
        .select_related("submission")
        .filter(pk__in=versions, submission__mode="live")
        .order_by("pk")
    )
    if len(rows) != len(versions):
        raise PermissionError("A Ministry request is unavailable.")
    for ministry in {row.ministry_duid for row in rows}:
        authorize_ministry(store, actor_id, ministry)
    for campaign in {row.submission.campaign_id for row in rows}:
        admit_campaign(campaign, mutating=True)
    return rows


def _system():
    """The active parish owns every audit event of this workflow."""
    return SystemConfiguration.objects.select_related(
        "active_configuration__parish"
    ).get()


def update_request(
    store, actor_id, request_id, *, expected_version, request_key, change
):
    """Apply one replay-safe Staff edit under the shared work order."""
    identities = (actor_id, request_id, request_key)
    if (
        any(not isinstance(value, UUID) for value in identities)
        or not isinstance(change, WorkflowChange)
        or type(expected_version) is not int
    ):
        raise ValueError("Invalid Ministry follow-up change.")
    with work_transaction():
        (target,) = _targets(store, actor_id, {request_id: expected_version})
        return _revise(
            store, actor_id, target, expected_version, request_key, change, _system()
        )


def assign_requests(store, actor_id, *, request_key, assignee_id, versions):
    """Assign or unassign an exact request/version set: all of it or none of it.

    Each row keeps its notes and open state, except that `new` and `assigned`
    follow whether it now has an assignee. One stale, closed, out-of-scope or
    unknown row rolls back every other row rather than updating a silent subset.
    """
    if (
        any(not isinstance(value, UUID) for value in (actor_id, request_key))
        or not isinstance(assignee_id, UUID | None)
        or type(versions) is not dict
        or not 1 <= len(versions) <= MAX_BULK
        or any(
            not isinstance(key, UUID) or type(value) is not int
            for key, value in versions.items()
        )
    ):
        raise ValueError("Invalid Ministry bulk assignment.")
    with work_transaction():
        system, revisions = _system(), []
        for target in _targets(store, actor_id, versions):
            latest = latest_revision(target.pk)
            state = target.state
            if state in {"new", "assigned"}:
                state = "new" if assignee_id is None else "assigned"
            revisions.append(
                _revise(
                    store,
                    actor_id,
                    target,
                    versions[target.pk],
                    # One form key binds the whole set; each row replays alone.
                    uuid5(request_key, str(target.pk)),
                    WorkflowChange(
                        assignee_id=assignee_id,
                        state=state,
                        outcome=None,
                        notes=latest.notes if latest else "",
                    ),
                    system,
                )
            )
        return revisions
