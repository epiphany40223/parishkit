"""Staff and Admin resolution of By-hand census changes (#528, step 3).

A census change that publication cannot carry into ParishSoft is entered by
hand. Staff and Administrators record that here (**Entered in ParishSoft**),
or that it will not be entered (**Ignore**), each with an optional note; an
Administrator may reopen an ignored change. Each action is one immutable
``ProposalResolution`` row (who, when, the note) plus the proposal's
resulting state, written under the shared work order. SQL independently
checks the actor, the campaign, the version, the starting state and the
pairing with the proposal and the audit event
(``schema/migrations/0060_census_resolution.sql``), so a forged or stale
request is refused even if this code were bypassed.

Since the review pipeline (#1683) the Census changes worklist records only
**Entered by hand** here (on a To review By hand change it accepts and marks
in one step); its Ignore and Back to review are reviews
(``census_review``). The ``ignored`` and ``reopened`` actions remain for
``pk-admin census resolve``.

A change a refresh found Entered incorrectly (#1689) takes the same two
actions as its fixes (#1737): ``entered`` again marks it Entered by hand
once more, and ``reopened`` sends it Back to review (pending, no decision).
On the worklist both are Staff's as well as an Administrator's
(``pk-admin census resolve`` keeps Reopen the Administrator's, with a
note). SQL admits them only while its latest refresh check says Entered
incorrectly and no later response of the Family answered the field again
(``schema/migrations/0140_entered_incorrectly_fixes.sql``, provisional).

Marking a New Member entered may name the **ParishSoft Member ID** of the
Member Staff added (#1749, ``member_ids``), which the refresh confirms the
change by; SQL admits it only on such a mark, never a Member the Family's
response listed (``schema/migrations/0150_published_refresh_conflicts.sql``,
provisional). While no refresh has confirmed such a mark, Staff and
Administrators may make it again with another Member ID (#1773,
``member_correctable``): a mistyped ID is corrected, or a mark made without
one gains one (``schema/migrations/0273_new_member_id_correction.sql``,
provisional).
"""

from uuid import UUID

from django.db.models import F

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import check_version

from .models import RESOLUTION_ACTIONS, ProposalResolution, ProposedChange
from .refresh_checks import check_states, entered_incorrectly, replaced

MAX_NOTE = 2000
# The largest ParishSoft Member ID the column holds (a PostgreSQL bigint).
MAX_MEMBER_ID = 2**63 - 1


class InvalidMemberId(ValueError):
    """A ParishSoft Member ID a New Member's mark cannot take (#1749).

    The message is static and safe, so the page may show it as the refusal
    at the Member ID field. ``correcting`` is whether it refused a
    correction of a mark in force (#1773), whose field cannot be left blank.
    """

    def __init__(self, message, *, correcting=False):
        super().__init__(message)
        self.correcting = correcting


def member_id(proposal, raw):
    """The ParishSoft Member ID ``raw`` gives for marking ``proposal`` entered.

    ``raw`` is the dialog's text (blank for none, which is None). Only a New
    Member takes one: a whole number, never one of the Members the Family's
    response listed (a Member Staff added cannot be), and never the ID
    another New Member of the Family is entered as now (two proposed
    Members cannot be one ParishSoft Member; the same proposed Member
    answered again may keep its own). Raises ``InvalidMemberId`` otherwise.
    SQL checks the same. ``proposal.submission`` must be loaded.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if proposal.entity_kind != "proposed_member" or proposal.field != "new_member":
        raise ValueError("Only a New Member takes a ParishSoft Member ID.")
    # A New Member already entered is having its ID corrected (#1773).
    correcting = proposal.execution == "resolved_external"
    digits = text.lstrip("0") or "0"
    if not (text.isascii() and text.isdecimal()) or len(digits) > 19:
        raise InvalidMemberId(
            "Enter the ParishSoft Member ID as a whole number.", correcting=correcting
        )
    value = int(digits)
    if not 1 <= value <= MAX_MEMBER_ID:
        raise InvalidMemberId(
            "Enter the ParishSoft Member ID as a whole number.", correcting=correcting
        )
    submission = proposal.submission
    if str(value) in (submission.answers.get("members") or {}):
        raise InvalidMemberId(
            "That ParishSoft Member ID is a Member the Family already listed. "
            "Enter the ID of the Member you added.",
            correcting=correcting,
        )
    taken = ProposalResolution.objects.filter(
        member_duid=value,
        action="entered",
        expected_version=F("proposal__version") - 1,
        proposal__execution="resolved_external",
        proposal__submission__family_id=submission.family_id,
        proposal__submission__campaign_id=submission.campaign_id,
        proposal__submission__mode=submission.mode,
    ).exclude(proposal__entity_key=proposal.entity_key)
    if taken.exists():
        raise InvalidMemberId(
            "Another New Member of this Family is already entered with that "
            "ParishSoft Member ID. Enter the ID of this Member.",
            correcting=correcting,
        )
    return value


def marked_member(proposal):
    """The ParishSoft Member ID the mark in force on ``proposal`` names, or None.

    The mark in force is the resolution that advanced the change to its
    current version: a final change's version moves only by a mark. None
    also when that mark named no ID.
    """
    return (
        ProposalResolution.objects.filter(
            proposal_id=proposal.pk,
            expected_version=proposal.version - 1,
            action="entered",
        )
        .values_list("member_duid", flat=True)
        .first()
    )


def member_correctable(proposal):
    """Whether a New Member's mark may be made again with another Member ID.

    Only while the New Member is Entered by hand, no refresh has confirmed
    it at its current version (#1773), and no later response of the Family
    replaced it (``refresh_checks.replaced``). The resolution guard (migration
    0273, provisional number) requires the same. ``proposal.submission``
    must be loaded. At most three queries, for an entered New Member only.
    """
    if (
        proposal.entity_kind != "proposed_member"
        or proposal.field != "new_member"
        or proposal.execution != "resolved_external"
        or replaced(proposal)
    ):
        return False
    key = str(proposal.pk)
    state = check_states(proposals=[proposal.pk], versions={key: proposal.version})
    return key not in state or state[key].outcome != "confirmed"


class KeyRebound(ValueError):
    """A request key already recorded a different review or resolution.

    Nothing was saved. It happens only when a page's answer was lost and the
    same drawn form (or bulk key) is sent again with other choices, so the
    page offers a reload rather than a generic refusal (a 409).
    """


OPEN_EXECUTIONS = frozenset({"pending", "conflict"})


def by_hand(proposal):
    """Whether a person, not publication, carries this change into ParishSoft.

    Every change the handling registry does not mark API-writable, and every
    Family field: no verified ParishSoft address read exists yet. The same
    rule is the worklist's By hand (``reports.census_changes.automatic``).
    """
    return proposal.handling != "api" or proposal.entity_kind == "family"


def allowed(proposal, action, *, incorrect=False):
    """Whether ``action`` may start from the proposal's current state.

    ``incorrect`` is whether the change is Entered incorrectly now
    (``refresh_checks.entered_incorrectly``): it then takes only its two
    fixes, Entered by hand again and Back to review (#1737).
    """
    if incorrect:
        return proposal.execution == "resolved_external" and action in {
            "entered",
            "reopened",
        }
    if proposal.execution not in OPEN_EXECUTIONS or proposal.handling == "report-only":
        return False
    if action == "reopened":
        return proposal.decision == "ignored"
    return by_hand(proposal) and proposal.decision != "ignored"


def capability(action):
    """Reopening is the Administrator's; the tick and Ignore are Staff's too."""
    return (
        Capability.PUBLISH_CENSUS if action == "reopened" else Capability.MANUAL_CENSUS
    )


def _valid(action, note):
    """Whether ``action`` and ``note`` make a resolution; a reopen says why."""
    return (
        action in RESOLUTION_ACTIONS
        and isinstance(note, str)
        and len(note) <= MAX_NOTE
        # Undoing an Ignore says why; SQL requires the same.
        and not (action == "reopened" and not note.strip())
    )


def record_resolution(
    proposal,
    actor_id,
    expected_version,
    request_key,
    action,
    note,
    parish,
    *,
    member_duid=None,
):
    """Record one locked proposal's resolution, its result and its audit event.

    The caller holds the work order and the proposal's row lock, has
    checked the actor's capability and admitted the campaign; ``parish``
    is the active parish's id, which the audit event names. The same
    ``request_key`` with the same intent returns the first resolution
    unchanged (a repeated submission); with a different intent it is
    refused. A proposal changed since the page was drawn is refused by
    ``check_version``, so the person sees its current state first. The
    review worklist's bulk Entered by hand records each change through here
    (``census_review.decide_census_changes``, #1683). ``member_duid`` is a
    New Member's ParishSoft Member ID (``member_id``), only with ``entered``;
    on a New Member already Entered by hand it corrects the ID its mark
    names (``member_correctable``, #1773), and the same ID again is refused
    as ``InvalidMemberId``.
    """
    intent = dict(
        proposal_id=proposal.pk,
        expected_version=expected_version,
        action=action,
        note=note,
        member_duid=member_duid,
    )
    previous = ProposalResolution.objects.filter(
        actor_id=actor_id, request_key=request_key
    ).first()
    if previous is not None:
        if any(getattr(previous, key) != value for key, value in intent.items()):
            raise KeyRebound("This request key is already bound.")
        return previous
    check_version(proposal, expected_version)
    incorrect = entered_incorrectly(proposal)
    # A New Member's Member ID corrected (#1773): a mark made again over the
    # one in force, with an ID. The cheap tests come first (and
    # ``member_correctable`` tests the kind before it queries), so other
    # resolutions pay no query for it.
    correctable = action == "entered" and not incorrect and member_correctable(proposal)
    if correctable and member_duid is None:
        raise InvalidMemberId(
            "Enter the ParishSoft Member ID of the Member you added.", correcting=True
        )
    if correctable and member_duid == marked_member(proposal):
        raise InvalidMemberId(
            "That is the ParishSoft Member ID already recorded. "
            "Enter the corrected ID.",
            correcting=True,
        )
    if not (correctable or allowed(proposal, action, incorrect=incorrect)) or (
        member_duid is not None and action != "entered"
    ):
        raise ValueError("This census change cannot be resolved that way now.")
    resolution = ProposalResolution.objects.create(
        actor_id=actor_id, request_key=request_key, **intent
    )
    if action == "entered":
        change = {"execution": "resolved_external"}
    elif incorrect:
        # Back to review from Entered incorrectly (#1737): the change is open
        # again, with no decision, for reconciliation and an Edit.
        change = {"decision": "unreviewed", "execution": "pending"}
    else:
        change = {"decision": "ignored" if action == "ignored" else "unreviewed"}
    ProposedChange.objects.filter(pk=proposal.pk).update(
        version=F("version") + 1, **change
    )
    # The audit names the resolution and the versions, never the note.
    record_action(
        Action.CENSUS_CHANGE_UPDATED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=resolution.pk,
        parish_id=parish,
        campaign_id=proposal.submission.campaign_id,
        context={
            "outcome": Outcome.CHANGED,
            "before_version": expected_version,
            "after_version": expected_version + 1,
        },
    )
    return resolution


def resolve_census_change(
    store, actor_id, proposal_id, *, expected_version, request_key, action, note
):
    """Record one resolution and its result, replay-safe, under the work order.

    See ``record_resolution`` for replays and stale versions.
    """
    if (
        any(
            not isinstance(value, UUID)
            for value in (actor_id, proposal_id, request_key)
        )
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or not _valid(action, note)
    ):
        raise ValueError("Invalid census change resolution.")
    bounded_text(note)
    with work_transaction():
        if not allows(current_principal(store, actor_id), capability(action)):
            raise PermissionError("Census change resolution is unavailable.")
        proposal = (
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=proposal_id, submission__mode="live")
        )
        admit_campaign(proposal.submission.campaign_id, mutating=True)
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        return record_resolution(
            proposal,
            actor_id,
            expected_version,
            request_key,
            action,
            note,
            system.active_configuration.parish.pk,
        )
