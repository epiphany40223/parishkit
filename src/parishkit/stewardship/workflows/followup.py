"""Authorized Ministry follow-up edits; never a Family answer or a roster write."""

from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

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

MAX_NOTES, MAX_CONTACT_NOTES = 5000, 2000
# An outcome that records a roster change applies only to its own kind of
# request; every other resolved outcome applies to both. The request form
# offers outcomes_for(action), and _revise refuses anything else, so the two
# cannot disagree. The revision guard (schema/ministry_followup.sql) enforces
# the same pairing in SQL.
OUTCOME_ACTIONS = {"joined": "join", "leave_confirmed": "leave"}


def outcomes_for(action):
    """The resolved outcomes a join or leave request may record, in order."""
    return tuple(
        outcome
        for outcome in RESOLVED_OUTCOMES
        if OUTCOME_ACTIONS.get(outcome, action) == action
    )


class FollowupRefusal(ValueError):
    """A follow-up edit the person can correct, named by a closed code.

    The request page shows it in place with the submitted values (#553);
    any other ValueError is a malformed form and gets the plain 400 page.
    """

    def __init__(self, code, **details):
        super().__init__(code)
        self.code, self.details = code, details


@dataclass(frozen=True)
class WorkflowChange:
    """The complete resulting workflow of one edit, validated without a database.

    Follow-up has no assignee (#552): a Ministry's leader handles its requests,
    so every edit stores none, which also clears one recorded before then.
    """

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
        if not isinstance(self.notes, str) or not isinstance(self.contact_notes, str):
            raise ValueError("Invalid Ministry follow-up change.")
        # The mistakes a person can make on the form get their own code.
        if self.state == "resolved" and self.outcome is None:
            raise FollowupRefusal("outcome_required")
        if self.outcome == "other" and not self.notes.strip():
            raise FollowupRefusal("other_needs_notes")
        if (
            len(self.notes) > MAX_NOTES
            or len(self.contact_notes) > MAX_CONTACT_NOTES
            or self.state not in STAFF_STATES
            or self.outcome not in outcomes.get(self.state, (None,))
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
    if change.state == "resolved" and change.outcome not in outcomes_for(target.action):
        raise FollowupRefusal(
            "outcome_kind", outcome=change.outcome, action=target.action
        )
    if change.contact_at is not None and change.contact_at > database_now():
        raise FollowupRefusal("contact_future")
    # No edit stores an assignee (#552), and one recorded before then is
    # cleared: the CHECKs pair `new` with none, and nothing offers `assigned`.
    revision = MinistryWorkflowRevision.objects.create(
        actor_id=actor_id, request_key=request_key, assignee_id=None, **intent
    )
    MinistryRequest.objects.filter(pk=target.pk).update(
        state=change.state,
        outcome=change.outcome,
        assignee_id=None,
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
