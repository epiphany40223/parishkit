"""The review of census changes (#528, ADM-09; the review pipeline, #1683).

An Automatic change (a Member field the handling registry marks
API-writable) reaches ParishSoft only through publication, and publication
plans only changes someone accepted (``responses/publication.py``); a By hand
change, once accepted, is entered by hand (``census_resolution``). Since the
review pipeline (#1683) Staff and Administrators review every census change
the worklist lists, By hand ones too, and only an Administrator publishes.
Here a reviewer records **Approve** (the worklist's Accept), **Ignore** or
**Back to To review** on one change, with an optional note; sending an
ignored change back to review needs one. Each is one immutable
``ProposalReview`` row (who, when, the note and the resulting review state)
plus the proposal's new decision, written under the shared work order. SQL
independently checks the actor, the campaign, the version, the starting
state and the pairing with the proposal and the audit event
(``schema/migrations/0096_proposal_review.sql``), so a forged or stale
request is refused even if this code were bypassed.

Every review advances the proposal's version, so a publication plan that
included the change no longer matches once it is reviewed again
(``publication_plans.matches_confirmed`` and the confirmation guard).

A reviewer may also give several changes one decision at once
(``review_census_changes``): each is reviewed exactly as one is, all or
none, under one work order.

A reviewer may also edit the value carried forward for one change, or clear
that edit (``edit_census_change``, ADM-09.04): the same kind of review row,
which keeps the decision and changes only the edit fields
(``schema/migrations/0107_proposal_review_edit.sql``). The worklist's Edit
is Accept with a value: one review row that approves and edits together
(``accept_census_change``, ``schema/migrations/0131_census_pipeline_review.sql``).
The Family's answer is never changed. An edit is per change only, never in
bulk.

On a Conflict (ParishSoft changed after the Family answered), a reviewer
may choose "Use the Family's value anyway" (the worklist's Accept on a
Conflict)
(``resolve_census_conflict``, ADM-09.03): a review row that accepts
ParishSoft's current value as the base publication merges against, approves
the change and moves it from conflict to pending
(``schema/migrations/0108_conflict_resolution.sql``; ``accepted_base``).
The Family's baseline is never changed. It is per change only, never in
bulk, since each row needs its own comparison.
"""

import json
import re
from datetime import date
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models import F

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import check_version
from parishkit.stewardship.web.tables import PAGE_SIZES

from .accepted_base import has_active_base
from .capabilities import Handling, capability
from .census import InvalidHousehold, validate_address
from .census_resolution import MAX_NOTE, record_resolution
from .comparison import ADDRESS_COMPONENTS, ValueKind
from .contact_schema import PUT_SCHEMA, sendable
from .member_census import InvalidMemberValue, validate_member_value
from .member_requests import REQUEST_FIELDS
from .models import REVIEW_DECISIONS, ProposalReview, ProposedChange
from .publication import MEMBER_FIELDS, encode_member_value

# A review may start only while the change is open: never while it is queued
# for publication (the publish worker owns it then) or once it is final.
REVIEWABLE = frozenset({"pending", "conflict", "failed"})
# The most changes one bulk review may cover: the largest page of the list
# (its selection is one page), as for the Ministry follow-up queue's bulk
# outcomes. It also keeps the post well inside DATA_UPLOAD_MAX_NUMBER_FIELDS.
# A page showing All rows offers bulk review only while it holds no more.
MAX_BULK = max(PAGE_SIZES)


def reviewable(proposal, decision):
    """Whether a reviewer may give the proposal ``decision`` now.

    Any census change the worklist lists, Automatic or By hand (the review
    pipeline's decision 2; never a report-only row, which belongs to another
    stream), only while it is open, and only to a decision different from
    its current one. Approving waits while ParishSoft is in conflict: the
    planner blocks an approved conflict anyway, and resolving it (the
    worklist's Accept on a Conflict) comes first.
    """
    if (
        proposal.handling == "report-only"
        or proposal.execution not in REVIEWABLE
        or decision not in REVIEW_DECISIONS
        or decision == proposal.decision
    ):
        return False
    return decision != "approved" or proposal.execution != "conflict"


def resolvable(proposal):
    """Whether a reviewer may resolve the change's conflict now.

    Any census change in conflict whose current ParishSoft value is known:
    accepting it as the base needs a value to accept (a change ParishSoft
    could not be read for waits for a refresh). It may already be approved
    (an approval that predates the conflict) and may carry an edited value,
    which is what publication then writes, or what Staff type in by hand.
    """
    return (
        proposal.handling != "report-only"
        and proposal.execution == "conflict"
        and proposal.current_available
    )


# Each Member field's census definition, by name.
FIELDS = {field.name: field for field in (*MEMBER_FIELDS, *REQUEST_FIELDS)}
# The Family's address fields, which take an address of their parts.
ADDRESS_FIELDS = frozenset({"home_address", "mailing_address"})
# The Member fields no value can be typed for: a yes-or-no request is
# accepted or ignored as the Family gave it (the SQL guard refuses an edit
# of them, and of the email opt-out and a whole New Member, the same way).
NOT_EDITABLE = frozenset({"moved_household", "deceased_status"})


def edits_field(proposal):
    """Whether a value can be typed for the change's field at all.

    A Member's names, prefix, suffix, dates, gender, marital status, email,
    phones and language, and a Family's home and mailing address. Not the
    yes-or-no answers (moved, deceased, email opt-out) or a New Member.
    """
    if proposal.entity_kind == "member":
        return proposal.field in FIELDS and proposal.field not in NOT_EDITABLE
    return proposal.entity_kind == "family" and proposal.field in ADDRESS_FIELDS


def editable(proposal):
    """Whether a reviewer may edit the change's value, or clear an edit, now.

    The same changes a review is offered on (any census change the worklist
    lists, open: never queued for publication or final), on a field a value
    can be typed for (``edits_field``). An edit keeps the decision, so a
    Conflict may be edited too.
    """
    return (
        proposal.handling != "report-only"
        and proposal.execution in REVIEWABLE
        and edits_field(proposal)
    )


ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
BLANK_EDIT = "Enter the value to publish. To leave ParishSoft as it is, choose Ignore."
# The Family form's messages that read wrong for an edit, in Edit value's
# own words: a blank required name is a blank edit like any other, and an
# edit cannot be left blank.
EDIT_WORDING = {
    "Complete this field before continuing.": BLANK_EDIT,
    "Enter valid email addresses or leave this field blank.": (
        "Enter valid email addresses, separated by commas or semicolons."
    ),
}


def campaign_today(campaign):
    """Today in the campaign's own timezone, as the Family form reads it.

    An edited birth or death date may be no later than this; the page caps
    its date field with the same day (``census_change_views``).
    """
    zone = ZoneInfo(campaign.active_configuration.timezone)
    return _now().astimezone(zone).date()


class InvalidEdit(ValueError):
    """An edited value the change cannot take; the message is static and safe.

    It never contains the value, so the page may show it as the refusal.
    ``fields`` maps an address part to its own reason (the page shows each
    at its part); empty for a one-field value.
    """

    def __init__(self, message, *, fields=None):
        super().__init__(message)
        self.fields = dict(fields or {})


def _civil_date(raw, today):
    """An ISO civil date no later than ``today`` (a birth or death date)."""
    try:
        if not ISO_DATE.fullmatch(raw) or date.fromisoformat(raw) > today:
            raise ValueError
    except ValueError:
        raise InvalidEdit("Enter a whole date no later than today.") from None
    return raw


def edit_limit(field, handling="api"):
    """The most characters an edited value of the Member ``field`` may hold.

    For an Automatic change (``handling`` api), the shorter of the census
    field's own limit and ParishSoft's request schema's; for a By hand one,
    the census field's own, since nothing is sent to ParishSoft. None for a
    date or a field with closed choices (held to its choices), and for an
    Automatic change of a field publication never writes, such as gender
    (By hand, #1603).
    """
    definition = FIELDS.get(field)
    if definition is None or definition.kind in {ValueKind.DATE, ValueKind.ENUM}:
        return None
    if handling != "api":
        return definition.max_length
    known = capability("member", field)
    if known.handling is not Handling.API:
        return None
    (provider,) = known.provider_fields
    return min(definition.max_length, PUT_SCHEMA[provider][1])


def edited_address(raw):
    """The census form of an edited Family address, read as the Family form reads one.

    ``raw`` maps each address part to its text (the editor's
    ``value_<part>`` fields). A blank address is refused, as any blank edit
    is; each part's reason is kept by its part's name, so the page marks
    the part in error (``InvalidEdit.fields``).
    """
    if type(raw) is not dict or set(raw) != set(ADDRESS_COMPONENTS):
        raise ValueError("This census change cannot take an edited value.")
    try:
        value = validate_address(raw)
    except InvalidHousehold as error:
        # The first part's reason is the dialog's one error line; every part
        # with a reason is marked.
        reasons = dict(error.fields)
        raise InvalidEdit(next(iter(reasons.values())), fields=reasons) from None
    if value is None:
        raise InvalidEdit(BLANK_EDIT)
    return value


def edited_value(field, raw, *, today, handling="api", entity="member"):
    """The census form of a reviewer's edited value for ``field``.

    The value is read as the Family form reads its own answer
    (``member_census.validate_member_value``; a date as a civil date no
    later than ``today``; a Family address by ``census.validate_address``),
    so it is stored in the same shape as the Family's answer: a phone keeps
    its display and comparison number, email addresses are joined as the
    form joins them. For an Automatic change (``handling`` api) it must
    then encode for ParishSoft exactly as publication will send it and fit
    the recorded request schema, checked as the smoke tool checks a value
    (``contact_schema.sendable``), so an edit publication would refuse is
    never stored; a By hand change's value is typed in by a person, so the
    census field's own limits are enough. A blank value is refused: Ignore
    leaves ParishSoft as it is. Raises ``InvalidEdit`` with a static
    message.
    """
    if entity == "family" and field in ADDRESS_FIELDS:
        return edited_address(raw)
    definition = FIELDS.get(field)
    automatic = handling == "api"
    if (
        entity != "member"
        or definition is None
        or field in NOT_EDITABLE
        or (automatic and capability("member", field).handling is not Handling.API)
        or type(raw) is not str
    ):
        raise ValueError("This census change cannot take an edited value.")
    try:
        if definition.kind is ValueKind.DATE:
            value = _civil_date(raw.strip(), today)
        else:
            value = validate_member_value(definition, raw, today=today)
    except InvalidMemberValue as error:
        raise InvalidEdit(EDIT_WORDING.get(str(error), str(error))) from None
    if value in (None, ""):
        raise InvalidEdit(BLANK_EDIT)
    if automatic and definition.kind is not ValueKind.ENUM:
        (provider,) = capability("member", field).provider_fields
        encoded = encode_member_value(field, value)
        if not sendable(provider, encoded):
            maximum = PUT_SCHEMA[provider][1]
            if maximum is not None and len(encoded) > maximum:
                raise InvalidEdit(
                    f"ParishSoft takes at most {maximum} characters in this field."
                )
            # Only a phone has a minimum (ten characters).
            raise InvalidEdit("Enter the whole phone number, with its area code.")
    return value


def _same_as_family(proposal, value):
    """Whether ``value`` compares equal to the Family's submitted answer.

    Read with the database's own comparison, the one the review guard
    applies, so an edit the guard would call a no-op is never sent to it.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_response_comparison_v1(field,%s::jsonb)"
            " IS NOT DISTINCT FROM"
            " stewardship_response_comparison_v1(field,submitted_value)"
            " FROM stewardship_proposed_change WHERE id=%s",
            [json.dumps(value), proposal.pk],
        )
        return cursor.fetchone()[0]


class Unchanged(ValueError):
    """The edit would leave the change's value as it is; nothing was saved."""


class KeyRebound(ValueError):
    """A request key already recorded a different review; nothing was saved.

    It happens only when a page's answer was lost and the same drawn form
    (or bulk key) is sent again with other choices, so the page offers a
    reload rather than a generic refusal.
    """


def one_campaign(proposals):
    """The one campaign every proposal of a bulk review belongs to.

    A bulk review is admitted and audited against a single campaign, so a
    batch mixing campaigns (only a crafted post) is refused.
    """
    campaigns = {proposal.submission.campaign_id for proposal in proposals}
    if len(campaigns) != 1:
        raise ValueError("A bulk review covers one campaign.")
    return campaigns.pop()


def _valid_note(note):
    """Refuse a note that is not bounded plain text."""
    if not isinstance(note, str) or len(note) > MAX_NOTE:
        raise ValueError("Invalid census change review.")
    bounded_text(note)


def _record(
    proposal,
    actor_id,
    expected_version,
    request_key,
    decision,
    note,
    parish,
    *,
    edit=None,
    resolve=False,
):
    """Record one locked proposal's review, its result and its audit event.

    The caller holds the work order and the proposal's row lock, and has
    admitted its campaign. ``decision`` is the decision recorded, or None
    to keep the change's; ``edit`` is the resulting ``(admin_value_set,
    admin_value)`` of an edit, or None to keep the edit fields. So a review
    is a decision (the edit fields stay), an edit (Edit value or Clear
    edit: the decision stays), or both when the decision is approved: the
    worklist's Edit is Accept with a value (#1683). ``resolve`` records a
    conflict resolution (ADM-09.03; the worklist's Accept on a Conflict),
    with an edit or without: the decision becomes approved, ParishSoft's
    current value and its source are recorded as the accepted base, and the
    change becomes pending; a later decision away from approved puts it
    back to conflict. Sending an ignored change back to review needs a
    note. The same ``request_key`` with the same intent returns the first
    review unchanged (a repeated submission); with a different intent it is
    refused. A proposal changed since the page was drawn is refused by
    ``check_version``, before the review rules, so the reviewer is told the
    row changed rather than that it cannot be reviewed.
    """
    # The kind is part of the intent, so a key bound to one kind of review
    # is never replayed as another.
    intent = dict(
        proposal_id=proposal.pk,
        expected_version=expected_version,
        note=note,
        accepted_base=resolve,
    )
    if edit is not None:
        intent["admin_value_set"], intent["admin_value"] = edit
    if decision is not None and not resolve:
        intent["decision"] = decision
    previous = ProposalReview.objects.filter(
        actor_id=actor_id, request_key=request_key
    ).first()
    if previous is not None:
        if any(getattr(previous, key) != value for key, value in intent.items()):
            raise KeyRebound("This request key is already bound.")
        return previous
    check_version(proposal, expected_version)
    if resolve and not resolvable(proposal):
        raise ValueError("This census change has no conflict to resolve now.")
    if not resolve and decision is not None and not reviewable(proposal, decision):
        raise ValueError("This census change cannot be reviewed that way now.")
    if not resolve and decision is None and edit is None:
        raise ValueError("A census change review records something.")
    if edit is not None:
        if not editable(proposal) or (decision not in (None, "approved")):
            raise ValueError("This census change cannot be edited now.")
        if decision is None and edit == (
            proposal.admin_value_set,
            proposal.admin_value,
        ):
            raise Unchanged("The edited value is unchanged.")
    if decision == "unreviewed" and proposal.decision == "ignored" and not note.strip():
        # Reopening an ignored change says why (the review pipeline's
        # "Reviewed stage"); the SQL guard requires the same.
        raise ValueError("Sending an ignored census change back needs a note.")
    # The row records the whole resulting review state: a decision keeps
    # the edit fields as the proposal has them, an edit keeps the decision.
    # A resolution accepts exactly the value and source the proposal holds
    # now, which the review guard checks against the locked row.
    state = {
        "decision": proposal.decision if decision is None else decision,
        "admin_value_set": proposal.admin_value_set,
        "admin_value": proposal.admin_value,
    } | intent
    # A decision that takes a resolved change (an active accepted base) away
    # from approved undoes the resolution: the change is a Conflict again,
    # so a later Approve, one by one or in bulk, cannot publish over
    # ParishSoft's value; Use the Family's value anyway may be chosen again.
    # Read before this review is recorded, since recording it ends the base.
    undo = (
        decision is not None
        and not resolve
        and decision != "approved"
        and has_active_base(proposal)
    )
    if resolve:
        state |= {
            "decision": "approved",
            "accepted_value": proposal.current_value,
            "accepted_source_id": proposal.current_source_id,
        }
    review = ProposalReview.objects.create(
        actor_id=actor_id, request_key=request_key, **state
    )
    changed = {}
    if resolve:
        changed = {"decision": "approved", "execution": "pending"}
    elif decision is not None:
        changed = {"decision": decision}
        if undo:
            changed["execution"] = "conflict"
    if edit is not None:
        changed |= {"admin_value_set": edit[0], "admin_value": edit[1]}
    ProposedChange.objects.filter(pk=proposal.pk).update(
        version=F("version") + 1, **changed
    )
    # The audit names the review and the versions, never the note.
    record_action(
        Action.CENSUS_CHANGE_UPDATED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=review.pk,
        parish_id=parish,
        campaign_id=proposal.submission.campaign_id,
        context={
            "outcome": Outcome.CHANGED,
            "before_version": expected_version,
            "after_version": expected_version + 1,
        },
    )
    return review


def _parish_id():
    """The active parish, which every review's audit event names."""
    return (
        SystemConfiguration.objects.select_related("active_configuration__parish")
        .get()
        .active_configuration.parish.pk
    )


def _authorize(store, actor_id):
    """A current Staff member or Administrator reviews (the review pipeline's
    decision 1); checked inside the work order. Only an Administrator
    publishes, which publication checks itself."""
    if not allows(current_principal(store, actor_id), Capability.MANUAL_CENSUS):
        raise PermissionError("Census change review is unavailable.")


def _edit_for(proposal, value, campaign):
    """The edit fields an edited ``value`` gives the locked proposal.

    ``value`` is the editor's raw text (an address: its parts), read by
    ``edited_value`` for the change's field and handling, with dates checked
    against the campaign's own today, as the Family form checks a birth
    date (responses/submission.py). A value equal to the Family's answer
    clears the edit: Reviewer's value never shows a no-op.
    """
    edited = edited_value(
        proposal.field,
        value,
        today=campaign_today(campaign),
        handling=proposal.handling,
        entity=proposal.entity_kind,
    )
    if _same_as_family(proposal, edited):
        return (False, None)
    return (True, edited)


def review_census_change(
    store, actor_id, proposal_id, *, expected_version, request_key, decision, note
):
    """Record one review and its result, replay-safe, under the work order.

    See ``_record`` for replays and stale versions.
    """
    if (
        any(
            not isinstance(value, UUID)
            for value in (actor_id, proposal_id, request_key)
        )
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or decision not in REVIEW_DECISIONS
    ):
        raise ValueError("Invalid census change review.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        proposal = (
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=proposal_id, submission__mode="live")
        )
        admit_campaign(proposal.submission.campaign_id, mutating=True)
        return _record(
            proposal,
            actor_id,
            expected_version,
            request_key,
            decision,
            note,
            _parish_id(),
        )


def edit_census_change(
    store, actor_id, proposal_id, *, expected_version, request_key, value, note
):
    """Record a reviewer's edited value, or clear it (ADM-09.04).

    ``value`` is the raw text the page sent, or None for Clear edit. The
    edit keeps the change's decision, whoever made it, and advances its
    version, so a confirmed publication plan that included the change no
    longer matches and must be confirmed again with the edited value in
    view. An edit equal to the Family's answer is Clear edit: Edited value
    never shows a no-op. Raises ``InvalidEdit`` for a value the change
    cannot take and ``Unchanged`` when nothing would change; see ``_record``
    for replays and stale versions. Only one change at a time: Edit value
    is never part of a bulk review.
    """
    if (
        any(not isinstance(item, UUID) for item in (actor_id, proposal_id, request_key))
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or (value is not None and (type(value) is not str or len(value) > 1000))
    ):
        raise ValueError("Invalid census change edit.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        proposal = (
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission__campaign__active_configuration")
            .get(pk=proposal_id, submission__mode="live")
        )
        campaign = proposal.submission.campaign
        admit_campaign(campaign.pk, mutating=True)
        edit = (False, None) if value is None else _edit_for(proposal, value, campaign)
        return _record(
            proposal,
            actor_id,
            expected_version,
            request_key,
            None,
            note,
            _parish_id(),
            edit=edit,
        )


def resolve_census_conflict(
    store, actor_id, proposal_id, *, expected_version, request_key, note
):
    """Use the Family's value anyway on one Conflict (#528, ADM-09.03).

    Records a review that accepts ParishSoft's current value as the base
    publication merges against and approves the change, which becomes
    pending (To do). Publication then writes the Family's answer, or the
    Administrator's edited value, while ParishSoft still holds the accepted
    value, and blocks it as a conflict again if ParishSoft has changed
    since; a refresh that brings a new ParishSoft value also sends the
    approval back to To review (``reconciliation``). The version advances,
    so a confirmed plan that included the change no longer matches. Per
    change only, never in bulk; see ``_record`` for replays and stale
    versions.
    """
    if (
        any(not isinstance(item, UUID) for item in (actor_id, proposal_id, request_key))
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
    ):
        raise ValueError("Invalid census conflict resolution.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        proposal = (
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=proposal_id, submission__mode="live")
        )
        admit_campaign(proposal.submission.campaign_id, mutating=True)
        return _record(
            proposal,
            actor_id,
            expected_version,
            request_key,
            None,
            note,
            _parish_id(),
            resolve=True,
        )


class BulkStale(Exception):
    """A chosen change was updated since the page was drawn; nothing was saved."""

    def __init__(self, proposal_id):
        super().__init__("A chosen census change was updated by someone else.")
        self.proposal_id = proposal_id


def bulk_key_for(bulk_key, proposal_id):
    """One change's request key within a bulk review, derived from the page's.

    The page draws one key for its bulk bar; each change's review takes its
    own key from it, so posting the same bulk review again replays each
    change's review instead of writing a second one (as the Ministry
    follow-up queue's bulk outcomes do, #1131).
    """
    return uuid5(bulk_key, str(proposal_id))


def review_census_changes(store, actor_id, versions, *, decision, note, bulk_key):
    """Give several changes one decision, all or none (#528, ADM-09).

    ``versions`` maps each proposal id to the version its row showed. Every
    change is reviewed exactly as ``review_census_change`` reviews one (its
    own review row, result and ``census_change_updated`` event, which 0096's
    SQL checks one by one), in one work transaction with every proposal
    locked in primary-key order first, so a bulk review never deadlocks
    with another or with the publish worker, which locks in the same order.
    The chosen changes must all belong to one campaign. One that changed
    since the page was drawn raises ``BulkStale`` naming it; any other
    refusal is a ValueError. Nothing is saved then. Returns the reviews in
    primary-key order.
    """
    if (
        not isinstance(actor_id, UUID)
        or not isinstance(bulk_key, UUID)
        or decision not in REVIEW_DECISIONS
        or not isinstance(versions, dict)
        or not 1 <= len(versions) <= MAX_BULK
        or any(
            not isinstance(key, UUID)
            or type(value) is not int
            or not 1 <= value < 2**63 - 1
            for key, value in versions.items()
        )
    ):
        raise ValueError("Invalid bulk census change review.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        proposals = list(
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .filter(pk__in=list(versions), submission__mode="live")
            .order_by("pk")
        )
        if len(proposals) != len(versions):
            raise ProposedChange.DoesNotExist("A chosen census change is unavailable.")
        admit_campaign(one_campaign(proposals), mutating=True)
        parish = _parish_id()
        reviews = []
        for proposal in proposals:
            try:
                reviews.append(
                    _record(
                        proposal,
                        actor_id,
                        versions[proposal.pk],
                        bulk_key_for(bulk_key, proposal.pk),
                        decision,
                        note,
                        parish,
                    )
                )
            except StaleRecordError:
                raise BulkStale(proposal.pk) from None
        return reviews


# The review worklist's decisions on census changes (#1683): its action
# values (``pipeline.Action``), each recorded as one review or resolution.
ACCEPT, EDIT, IGNORE, BACK, ENTERED = (
    "accept",
    "edit",
    "ignore",
    "back_to_review",
    "entered_by_hand",
)
WORKLIST_ACTIONS = frozenset({ACCEPT, EDIT, IGNORE, BACK, ENTERED})


def _decide(proposal, action, actor_id, version, key, note, parish, value):
    """Record one worklist decision on one locked proposal.

    Accept approves; on a Conflict it resolves it against ParishSoft's
    current value (the accepted base), as "Use the Family's value anyway"
    does. Edit is Accept with a value, the same way, and an edit equal to
    the fields the change already has is a plain Accept. Ignore and Back to
    review are decisions. Entered by hand is a resolution
    (``census_resolution``), which on a To review By hand change accepts and
    marks it in one step (decision 2).
    """
    if action == ENTERED:
        return record_resolution(
            proposal, actor_id, version, key, "entered", note, parish
        )
    if action in (ACCEPT, EDIT):
        edit = None
        if action == EDIT:
            edit = _edit_for(proposal, value, proposal.submission.campaign)
            if edit == (proposal.admin_value_set, proposal.admin_value):
                edit = None
            elif not editable(proposal):
                raise ValueError("This census change cannot be edited now.")
        conflict = proposal.execution == "conflict"
        return _record(
            proposal,
            actor_id,
            version,
            key,
            None if conflict else "approved",
            note,
            parish,
            edit=edit,
            resolve=conflict,
        )
    decision = "ignored" if action == IGNORE else "unreviewed"
    return _record(proposal, actor_id, version, key, decision, note, parish)


def decide_census_changes(
    store, actor_id, action, versions, *, note, request_key, bulk=False, value=None
):
    """Record one worklist decision on one or several census changes, all or none.

    ``versions`` maps each proposal id to the version its row showed;
    ``request_key`` is the row's key, or for ``bulk`` the page's bulk key,
    from which each change's own key is derived (``bulk_key_for``), so a
    repeated post records nothing more. ``value`` is Edit's raw value, one
    change only. Every change is locked in primary-key order first, in one
    work transaction (as the publish worker locks), and recorded exactly as
    its own icon would record it (its own review or resolution row, version
    and audit event). They must all belong to one campaign. One that changed
    since the page was drawn raises ``BulkStale`` naming it; any other
    refusal is a ValueError, or ``InvalidEdit`` for a value the change
    cannot take. Nothing is saved then. Returns the rows in primary-key
    order.
    """
    if (
        not isinstance(actor_id, UUID)
        or not isinstance(request_key, UUID)
        or action not in WORKLIST_ACTIONS
        or not isinstance(versions, dict)
        or not 1 <= len(versions) <= MAX_BULK
        or (action == EDIT and (bulk or len(versions) != 1 or value is None))
        or (action != EDIT and value is not None)
        or any(
            not isinstance(key, UUID)
            or type(version) is not int
            or not 1 <= version < 2**63 - 1
            for key, version in versions.items()
        )
    ):
        raise ValueError("Invalid census change decision.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        proposals = list(
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission__campaign__active_configuration")
            .filter(pk__in=list(versions), submission__mode="live")
            .order_by("pk")
        )
        if len(proposals) != len(versions):
            raise ProposedChange.DoesNotExist("A chosen census change is unavailable.")
        admit_campaign(one_campaign(proposals), mutating=True)
        parish = _parish_id()
        recorded = []
        for proposal in proposals:
            key = bulk_key_for(request_key, proposal.pk) if bulk else request_key
            try:
                recorded.append(
                    _decide(
                        proposal,
                        action,
                        actor_id,
                        versions[proposal.pk],
                        key,
                        note,
                        parish,
                        value,
                    )
                )
            except StaleRecordError:
                raise BulkStale(proposal.pk) from None
        return recorded
