"""An Administrator's review of Automatic census changes (#528, ADM-09).

An Automatic change (a Member field the handling registry marks
API-writable) reaches ParishSoft only through publication, and publication
plans only changes an Administrator approved (``responses/publication.py``).
Here an Administrator records **Approve**, **Ignore** or **Back to To
review** on one change, with an optional note. Each is one immutable
``ProposalReview`` row (who, when, the note and the resulting review state)
plus the proposal's new decision, written under the shared work order. SQL
independently checks the actor, the campaign, the version, the starting
state and the pairing with the proposal and the audit event
(``schema/migrations/0096_proposal_review.sql``), so a forged or stale
request is refused even if this code were bypassed.

Every review advances the proposal's version, so a publication plan that
included the change no longer matches once it is reviewed again
(``publication_plans.matches_confirmed`` and the confirmation guard).

An Administrator may also give several changes one decision at once
(``review_census_changes``): each is reviewed exactly as one is, all or
none, under one work order.

An Administrator may also edit the value publication writes for one change,
or clear that edit (``edit_census_change``, ADM-09.04): the same kind of
review row, which keeps the decision and changes only the edit fields
(``schema/migrations/0107_proposal_review_edit.sql``). The Family's answer
is never changed. Edit value is per change only, never in bulk.
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

from .capabilities import Handling, capability
from .census_resolution import MAX_NOTE, by_hand
from .comparison import ValueKind
from .contact_smoke import PUT_SCHEMA, sendable
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
    """Whether an Administrator may give the proposal ``decision`` now.

    Only an Automatic change (the complement of ``by_hand``, without the
    report-only rows the worklist leaves out), only while it is open, and
    only to a decision different from its current one. Approving waits
    while ParishSoft is in conflict: the planner blocks an approved conflict
    anyway, and resolving it comes first.
    """
    if (
        by_hand(proposal)
        or proposal.execution not in REVIEWABLE
        or decision not in REVIEW_DECISIONS
        or decision == proposal.decision
    ):
        return False
    return decision != "approved" or proposal.execution != "conflict"


# Each Member field's census definition, by name.
FIELDS = {field.name: field for field in (*MEMBER_FIELDS, *REQUEST_FIELDS)}


def editable(proposal):
    """Whether an Administrator may edit the change's value, or clear an edit, now.

    The same changes a Review is offered on: Automatic, and open (never
    queued for publication or final), on a Member field the census defines.
    An edit keeps the decision, so a Conflict may be edited too.
    """
    return (
        not by_hand(proposal)
        and proposal.execution in REVIEWABLE
        and proposal.entity_kind == "member"
        and proposal.field in FIELDS
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
    """


def _civil_date(raw, today):
    """An ISO civil date no later than ``today`` (a birth or death date)."""
    try:
        if not ISO_DATE.fullmatch(raw) or date.fromisoformat(raw) > today:
            raise ValueError
    except ValueError:
        raise InvalidEdit("Enter a whole date no later than today.") from None
    return raw


def edit_limit(field):
    """The most characters ParishSoft's request schema takes for ``field``.

    None for a date or a field publication never writes. Gender's limit (one
    character) is not used: its provider encoding is still unsettled
    (``publication``), so a gender edit is held to the census choices only.
    """
    definition = FIELDS.get(field)
    known = capability("member", field) if definition else None
    if (
        known is None
        or known.handling is not Handling.API
        or definition.kind in {ValueKind.DATE, ValueKind.ENUM}
    ):
        return None
    (provider,) = known.provider_fields
    return min(definition.max_length, PUT_SCHEMA[provider][1])


def edited_value(field, raw, *, today):
    """The census form of an Administrator's edited value for ``field``.

    The value is read as the Family form reads its own answer
    (``member_census.validate_member_value``; a date as a civil date no
    later than ``today``), so it is stored in the same shape as the Family's
    answer: a phone keeps its display and comparison number, email addresses
    are joined as the form joins them. It must then encode for ParishSoft
    exactly as publication will send it and fit the recorded request
    schema, checked as the smoke tool checks a value
    (``contact_smoke.sendable``), so an edit publication would refuse is
    never stored. A blank value is refused: Ignore leaves ParishSoft as it
    is. Raises ``InvalidEdit`` with a static message.
    """
    definition = FIELDS.get(field)
    if (
        definition is None
        or capability("member", field).handling is not Handling.API
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
    if definition.kind is not ValueKind.ENUM:
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
):
    """Record one locked proposal's review, its result and its audit event.

    The caller holds the work order and the proposal's row lock, and has
    admitted its campaign. ``edit`` is None for a decision; for an Edit
    value or Clear edit it is the resulting ``(admin_value_set,
    admin_value)``, and ``decision`` is then None (the decision stays). The
    same ``request_key`` with the same intent returns the first review
    unchanged (a repeated submission); with a different intent it is
    refused. A proposal changed since the page was drawn is refused by
    ``check_version``, before the review rules, so the Administrator is told
    the row changed rather than that it cannot be reviewed.
    """
    intent = dict(
        proposal_id=proposal.pk,
        expected_version=expected_version,
        note=note,
    )
    if edit is None:
        intent["decision"] = decision
    else:
        intent["admin_value_set"], intent["admin_value"] = edit
    previous = ProposalReview.objects.filter(
        actor_id=actor_id, request_key=request_key
    ).first()
    if previous is not None:
        if any(getattr(previous, key) != value for key, value in intent.items()):
            raise KeyRebound("This request key is already bound.")
        return previous
    check_version(proposal, expected_version)
    if edit is None and not reviewable(proposal, decision):
        raise ValueError("This census change cannot be reviewed that way now.")
    if edit is not None:
        if not editable(proposal):
            raise ValueError("This census change cannot be edited now.")
        if edit == (proposal.admin_value_set, proposal.admin_value):
            raise Unchanged("The edited value is unchanged.")
    # The row records the whole resulting review state: a decision keeps
    # the edit fields as the proposal has them, an edit keeps the decision.
    state = {
        "decision": proposal.decision if edit is not None else decision,
        "admin_value_set": proposal.admin_value_set,
        "admin_value": proposal.admin_value,
    } | intent
    review = ProposalReview.objects.create(
        actor_id=actor_id, request_key=request_key, **state
    )
    changed = (
        {"decision": decision}
        if edit is None
        else {"admin_value_set": edit[0], "admin_value": edit[1]}
    )
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
    """Only a current Administrator reviews; checked inside the work order."""
    if not allows(current_principal(store, actor_id), Capability.PUBLISH_CENSUS):
        raise PermissionError("Census change review is unavailable.")


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
    """Record an Administrator's edited value, or clear it (ADM-09.04).

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
        edit = (False, None)
        if value is not None:
            # Dates are checked against the campaign's own today, as the
            # Family form checks a birth date (responses/submission.py).
            edited = edited_value(proposal.field, value, today=campaign_today(campaign))
            if not _same_as_family(proposal, edited):
                edit = (True, edited)
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
