"""Authorized Ministry follow-up edits; never a Family answer or a roster write."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from uuid import UUID, uuid5

from django.db import connection
from django.db.models import F

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import bound_correlation, correlation
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import check_version
from parishkit.stewardship.web.tables import PAGE_SIZES

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
# Stored outcomes no person records any more. Duplicate request (#1131): the
# system never creates duplicates (a Family resubmission replaces a request),
# so it was a rarely meaningful extra choice. Rows already resolved that way
# keep it and still read "Duplicate request"; the CHECK still admits it.
RETIRED_OUTCOMES = frozenset({"duplicate"})
# The outcomes the follow-up queue records for many requests at once
# (#1131): Joined for a join, Left for a leave, and declined for either (a
# join's "Declined / no longer interested", a leave's "Stayed in the
# Ministry", #1132).
BULK_OUTCOMES = ("joined", "leave_confirmed", "declined")
# One bulk action covers at most one page of the queue, its largest size.
MAX_BULK = max(PAGE_SIZES)


def outcomes_for(action):
    """The resolved outcomes a join or leave request may record, in order."""
    return tuple(
        outcome
        for outcome in RESOLVED_OUTCOMES
        if OUTCOME_ACTIONS.get(outcome, action) == action
        and outcome not in RETIRED_OUTCOMES
    )


class FollowupRefusal(ValueError):
    """A follow-up edit the person can correct, named by a closed code.

    The request page shows it in place with the submitted values (#553);
    any other ValueError is a malformed form and gets the plain 400 page.
    """

    def __init__(self, code, **details):
        super().__init__(code)
        self.code, self.details = code, details


class BulkStale(StaleRecordError):
    """One request of a bulk action changed or closed since the page was read.

    ``request_id`` names it, so the dialog can say which row to look at;
    nothing of the batch was saved.
    """

    def __init__(self, request_id):
        super().__init__("A selected Ministry request changed.")
        self.request_id = request_id


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


def _latest_notes(request_ids):
    """Each request's current notes: the newest across its same-intent chain.

    A revision stores the complete resulting workflow, notes included, and
    the request page shows the newest one's notes; so a bulk revision carries
    each request's notes forward rather than clearing them. One statement
    reads them all, through the same chain the history uses.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT t.id,(SELECT v.notes "
            "FROM stewardship_ministry_workflow_chain_v1(t.id) c "
            "JOIN stewardship_ministry_revision v ON v.request_id=c.request_id "
            "ORDER BY c.depth,v.expected_version DESC LIMIT 1) "
            "FROM unnest(%s::uuid[]) t(id)",
            [list(request_ids)],
        )
        return {key: notes or "" for key, notes in cursor.fetchall()}


def bulk_key_for(bulk_key, request_id):
    """One request's replay key within a bulk action, derived from the page's.

    The page renders one key per bulk action; each request's revision takes
    its own key from it, so posting the same action again replays each
    request's revision instead of writing a second one.
    """
    return uuid5(bulk_key, str(request_id))


def apply_bulk_outcome(store, actor_id, versions, *, outcome, bulk_key, change):
    """Resolve many requests with one outcome, all or none (#1131).

    ``versions`` maps each request id to the version its row showed; each
    request gets its own revision, projection and audit event through
    ``_revise``, exactly as a single edit does, in one transaction under the
    shared work order, with every request locked in primary-key order first
    (``_targets``). ``change`` holds the optional contact attempt applied to
    each; its state, outcome and notes are set here (each request keeps its
    own notes). Any request that is outside the actor's Ministries refuses
    the whole batch as denied; one that changed or closed since the page was
    read raises ``BulkStale`` naming it; and an outcome that does not apply
    to a request's kind (Joined on a leave, Left on a join) raises the
    ``outcome_kind`` refusal with its ``request_id``. Nothing is saved then.

    Every revision and audit event shares one correlation id (the web
    request's), and one ``ministry_followup_bulk_applied`` event records the
    action itself with how many requests it changed (``count``) of those
    chosen (``matching_count``); the request page marks a revision "(bulk)"
    from that event. Returns the revisions, in request order.
    """
    if (
        not isinstance(actor_id, UUID)
        or not isinstance(bulk_key, UUID)
        or not isinstance(change, WorkflowChange)
        or outcome not in BULK_OUTCOMES
        or not isinstance(versions, dict)
        or not 1 <= len(versions) <= MAX_BULK
        or any(
            not isinstance(key, UUID) or type(value) is not int
            for key, value in versions.items()
        )
    ):
        raise ValueError("Invalid bulk Ministry follow-up change.")
    with correlation(bound_correlation()) as shared, work_transaction():
        targets = _targets(store, actor_id, versions)
        system = _system()
        notes = _latest_notes(versions)
        revisions = []
        for target in targets:
            # The kind is checked before the version, so a request that
            # could never take this outcome is named as such, not as stale.
            if OUTCOME_ACTIONS.get(outcome, target.action) != target.action:
                raise FollowupRefusal(
                    "outcome_kind",
                    outcome=outcome,
                    action=target.action,
                    request_id=target.pk,
                )
            resolved = replace(
                change, state="resolved", outcome=outcome, notes=notes[target.pk]
            )
            try:
                revisions.append(
                    _revise(
                        store,
                        actor_id,
                        target,
                        versions[target.pk],
                        bulk_key_for(bulk_key, target.pk),
                        resolved,
                        system,
                    )
                )
            except StaleRecordError:
                raise BulkStale(target.pk) from None
        # A replayed post returns the earlier revisions and records nothing.
        written = sum(revision.correlation_id == shared for revision in revisions)
        if written:
            record_action(
                Action.MINISTRY_FOLLOWUP_BULK_APPLIED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor_id,
                subject_id=targets[0].submission.campaign_id,
                parish_id=system.active_configuration.parish.pk,
                campaign_id=targets[0].submission.campaign_id,
                context={
                    "outcome": Outcome.CHANGED,
                    "count": written,
                    "matching_count": len(versions),
                },
            )
    return revisions
