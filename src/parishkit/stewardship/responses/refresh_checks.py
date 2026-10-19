"""Refresh checks: the refresh re-evaluation of final and decided items (#1689).

Every promoted refresh re-evaluates every open pipeline item against the new
baseline (review pipeline spec, "Baseline refresh re-evaluation"). Items
still open keep today's reconciliation (``reconciliation``,
``ministry_reconciliation``), which stores ParishSoft's value on the census
change or resolves the Ministry request. A census change Entered by hand or
Updated in ParishSoft is final in its guard, and a Ministry request a person
decided changes only by review, so for those this module records a
``RefreshCheck`` instead, in the same promotion transaction ("Refresh
checks"):

- ``record_refresh_checks`` writes the checks one promotion owes, and
  ``refresh_checks_current`` answers whether it would write none (the check
  before an identical quick update skips promotion, #630);
- ``check_states`` reads the latest check of each item for the pages, which
  derive the stage from it (``pipeline.census_placement`` and
  ``ministry_placement``) and the Not seen in ParishSoft flag;
- ``census_source`` reads a census change's value from the promoted data:
  a Member contact field as reconciliation does, and a Member's move or
  death and the Family's email opt-out each its own way (#1738), and the
  Family's home address and a New Member by the ParishSoft Member ID
  recorded at its mark (#1749);
- an Updated in ParishSoft change ParishSoft no longer holds gets a
  ``conflict`` check on a full refresh, and ``record_refresh_checks``
  moves it to Conflict, To review again, in the same promotion (#1749).

The comparison is ``pipeline.refresh_outcome`` with each stream's canonical
comparison: the census compare rules (``comparison.canonical_value``) and a
Member's roster state for the Ministry carried forward. A check is written
only when its outcome changes, or, for an item marked Entered by hand, once
per full refresh until Not seen is reached, so a quiet refresh writes
nothing. A Confirmed item is never checked again; an item whose Member or
Ministry left the data gets no check (rule 4: it keeps its stage), except a
move, which leaving the Family is the evidence of.
"""

import json
import re
from dataclasses import dataclass

from django.db.models import F, Max, Q

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.pipeline import (
    CENSUS_CONFIRMABLE,
    RefreshOutcome,
    Stage,
    refresh_outcome,
)
from parishkit.stewardship.source.pins import pin_snapshot
from parishkit.stewardship.source.version_models import SnapshotAddress
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.workflows.models import (
    ACCEPTED_OUTCOMES,
    MinistryRequest,
    MinistryRosterEntry,
    RefreshCheck,
    RefreshCheckStart,
)

from .baselines import _pin_admission
from .census import FAMILY_FIELDS
from .comparison import ValueKind, canonical_value
from .inputs import MEMBER_FIELDS, MemberSourceUnavailable, member_field_value
from .member_requests import REQUEST_FIELDS
from .merge import KnownValue
from .models import ProposalResolution, ProposedChange
from .reconciliation import _release_unused_source

# The census executions a refresh check is for: final in the census guard.
FINAL_EXECUTIONS = ("published", "resolved_external")
# The confirmable Member fields: the ones the Member contact read carries,
# and a move and a death, each read its own way (``census_source``, #1738).
DEFINITIONS = {field.name: field for field in (*MEMBER_FIELDS, *REQUEST_FIELDS)}
CHECKED_FIELDS = frozenset(
    name for name in DEFINITIONS if CENSUS_CONFIRMABLE.get(("member", name))
)
# The Family fields a refresh confirms: the email opt-out, against the
# Family's sendNoMail flag (Administrator, 2026-10-18, #1680), and the home
# address, against ParishSoft's primary address, which follows the home
# block when home is primary (#1688's smoke test, #1749). The mailing
# address is never read.
CHECKED_FAMILY_FIELDS = frozenset({"email_opt_out", "home_address"})
# The census changes a refresh checks, by entity kind and field: a New
# Member by the ParishSoft Member ID recorded at its mark (#1749).
CHECKED = (
    Q(entity_kind="member", field__in=CHECKED_FIELDS)
    | Q(entity_kind="family", field__in=CHECKED_FAMILY_FIELDS)
    | Q(entity_kind="proposed_member", field="new_member")
)
# A yes-or-no request (moved, deceased), an opt-out whose baseline was never
# read, a New Member (listed or not) and a home address whose ParishSoft
# block is not known (a Family whose primary is the mailing block may hold
# any address there) have no third value to be wrong at: never Entered
# incorrectly, and no value recorded at the mark.
NO_THIRD_VALUE = frozenset(
    {
        "moved_household",
        "deceased_status",
        "email_opt_out",
        "home_address",
        "new_member",
    }
)
FAMILY_KINDS = {field.name: field.kind for field in FAMILY_FIELDS}
# The stored outcome of each comparison result. A published change found
# back at A or at a third value is a Conflict (#1749), which
# ``record_refresh_checks`` then moves the change to.
STORED = {
    RefreshOutcome.CONFIRMED: "confirmed",
    RefreshOutcome.UNCHANGED: "unchanged",
    RefreshOutcome.NOT_SEEN: "unchanged",
    RefreshOutcome.ENTERED_INCORRECTLY: "entered_incorrectly",
    RefreshOutcome.IGNORED_NOW_MATCHES: "ignored_now_matches",
    RefreshOutcome.CONFLICT: "conflict",
}
CONFLICT = "conflict"
# ParishSoft keeps a ZIP+4's four digits apart, so a ZIP+4 is compared by
# its five digits (``_address_key``).
ZIP_PLUS_FOUR = re.compile(r"([0-9]{5})-[0-9]{4}")
UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class CheckState:
    """One item's latest check and its run of unchanged full refreshes.

    ``outcome`` and ``observed`` are the latest check's (``unchanged`` and
    None when there is none). ``unseen`` counts the unchanged full-refresh
    checks since the item's mark, the Not seen start and its last other
    outcome; ``not_seen_after`` is the start row's threshold (None when the
    row is gone, which turns Not seen off).
    """

    outcome: str = UNCHANGED
    observed: object = None
    unseen: int = 0
    not_seen_after: int | None = None

    def not_seen(self, marked):
        """Whether a marked item is flagged Not seen in ParishSoft now."""
        return bool(
            marked
            and self.outcome == UNCHANGED
            and self.not_seen_after is not None
            and self.unseen >= self.not_seen_after
        )


def _start():
    """The Not seen start row, or None (deleting it turns the flag off)."""
    return RefreshCheckStart.objects.filter(singleton=True).first()


def _summaries(checks, start, marks):
    """Each item's ``CheckState`` from its checks, keyed by item id.

    ``checks`` are ``(item_id, created_at, outcome, full_refresh, observed)``
    rows, oldest first; ``marks`` maps an item id to when it was marked
    Entered by hand (a census change's checks all follow its mark).
    """
    found = {}
    for item, created_at, outcome, full, observed in checks:
        previous = found.get(item, CheckState())
        counted = (
            outcome == UNCHANGED
            and full
            and start is not None
            and created_at > start.started_at
            and (marks.get(item) is None or created_at > marks[item])
        )
        unseen = (previous.unseen if outcome == previous.outcome else 0) + counted
        found[item] = CheckState(outcome, observed, unseen)
    threshold = None if start is None else start.not_seen_after
    return {
        item: CheckState(state.outcome, state.observed, state.unseen, threshold)
        for item, state in found.items()
    }


def check_states(*, proposals=(), requests=(), marks=None, versions=None):
    """The latest check state of each census change and Ministry request.

    ``proposals`` and ``requests`` are item ids; ``marks`` maps a request id
    (text) to its current tick (``workflows.roster.roster_marks``), whose
    time starts its Not seen count. ``versions`` maps an id (text) to the
    item's current version: a check made at another version (a request
    decided again since) is about a decision no longer in force and is
    left out. Returns ``{id text: CheckState}``; an item with no check is
    absent. Two queries: the start row and the checks.
    """
    proposals, requests = list(proposals), list(requests)
    if not proposals and not requests:
        return {}
    start = _start()
    rows = (
        RefreshCheck.objects.filter(
            Q(proposal_id__in=proposals) | Q(request_id__in=requests)
        )
        .order_by("created_at", "id")
        .values_list(
            "proposal_id",
            "request_id",
            "created_at",
            "outcome",
            "full_refresh",
            "observed",
            "item_version",
        )
    )
    versions = versions or {}
    checks = [
        (str(proposal or request), created_at, outcome, full, observed)
        for proposal, request, created_at, outcome, full, observed, version in rows
        if versions.get(str(proposal or request), version) == version
    ]
    ticked = {
        key: mark["at"] for key, mark in (marks or {}).items() if mark.get("entered")
    }
    return _summaries(checks, start, ticked)


def _census_same(kind):
    """The census comparison of two ``KnownValue``s of one field kind."""

    def same(left, right):
        """Equal when both are unavailable, or both read and canonically equal."""
        if left.available != right.available:
            return False
        if not left.available:
            return True
        return canonical_value(kind, left.value) == canonical_value(kind, right.value)

    return same


def entered_incorrectly(proposal):
    """Whether a census change is Entered incorrectly now (#1737).

    Only a change Entered by hand can be, and only while its latest refresh
    check at its current version says so: the two fixes (Entered by hand
    again and Back to review) are offered and recorded then alone, as the
    resolution guard (migration 0140, provisional number) also requires. One
    query, for an entered change only.
    """
    if proposal.execution != "resolved_external" or replaced(proposal):
        return False
    key = str(proposal.pk)
    state = check_states(proposals=[proposal.pk], versions={key: proposal.version})
    return key in state and state[key].outcome == "entered_incorrectly"


def replaced(proposal):
    """Whether a later response of the Family has a change for the same field.

    A final change is never superseded (``proposals``): the Family's next
    answer makes a change of its own, and the earlier one is history. Such a
    change gets no refresh check and takes no fix (#1737), as the refresh
    check and resolution guards (migration 0140, provisional) require.
    ``proposal.submission`` must be loaded. One query.
    """
    submission = proposal.submission
    return ProposedChange.objects.filter(
        submission__family_id=submission.family_id,
        submission__campaign_id=submission.campaign_id,
        submission__mode=submission.mode,
        submission__family_version__gt=submission.family_version,
        entity_kind=proposal.entity_kind,
        entity_key=proposal.entity_key,
        field=proposal.field,
    ).exists()


def _remarked(rows):
    """ParishSoft's value at each census change's latest mark made again, by id.

    A final change's version moves only when it is marked Entered by hand
    again over Entered incorrectly (#1737), and then by one. So a check at
    the version before an entered change's current one means that mark was
    made, and the latest such check's ParishSoft value is the value recorded
    at it. The refresh check guard (migration 0140, provisional number)
    reads it the same way. One query, when any change is entered.
    """
    wanted = {
        (row.pk, row.version - 1)
        for row in rows
        if row.execution == "resolved_external"
    }
    if not wanted:
        return {}
    found = {}
    for proposal, version, observed in (
        RefreshCheck.objects.filter(proposal_id__in={pk for pk, _ in wanted})
        .order_by("created_at", "id")
        .values_list("proposal_id", "item_version", "observed")
    ):
        if (proposal, version) in wanted:
            found[proposal] = observed
    return found


def _owed(stored, state, *, full, marked, moved=False):
    """Whether a check with outcome ``stored`` is owed for an item now.

    When the outcome changed (an item with no check reads as unchanged); for
    Entered incorrectly also when ParishSoft ``moved`` to yet another value
    (canonically, so a formatting change is not one); and for a marked item,
    once per full refresh until Not seen is reached.
    """
    if stored != state.outcome:
        return True
    if stored == "entered_incorrectly":
        return moved
    return (
        stored == UNCHANGED
        and full
        and marked
        and state.not_seen_after is not None
        and state.unseen < state.not_seen_after
    )


def census_source(row, corpus):
    """ParishSoft's value for census change ``row`` in ``corpus``, or None.

    ``row`` is annotated with its Family's ``family_duid``. None means the
    data gives no evidence either way, so the change gets no check (rule
    4: it keeps its stage). Migration 0141's
    ``stewardship_refresh_census_source_v1`` reads the same, and the check's
    guard requires it:

    - a Member contact field (and a death date) only while the Member is
      an active, living Member of the change's Family (a death date
      whatever their status), as reconciliation reads it;
    - a move: True unless the data lists the Member as an active Member of
      the Family (not in the data, in another Family, or inactive all read
      True); none for a Member marked deceased, which is not a move;
    - a death: whether the data marks the Member deceased, only while it
      lists them in the Family;
    - the Family's email opt-out: its ``sendNoMail`` flag while the Family
      is active;
    - the Family's home address: ParishSoft's primary address of an active
      Family (``row.primary_address``, the snapshot's address fields;
      ``_primary_address``), migration 0150's (provisional number) reading;
    - a New Member: whether the data lists the Member ID recorded at its
      mark (``row.added_duid``) as an active, living Member of the Family,
      as the shared SQL status says ``current``; none without an ID.
    """
    if row.entity_kind == "proposed_member":
        if row.field != "new_member" or row.added_duid is None:
            return None
        added = corpus["member"].get(str(row.added_duid))
        return KnownValue(
            True,
            added is not None
            and added["family_key"] == str(row.family_duid)
            and added["active"] is not False
            and added["deceased"] is not True,
        )
    if row.entity_kind == "family":
        family = corpus["family"].get(str(row.family_duid))
        if (
            row.field not in CHECKED_FAMILY_FIELDS
            or row.entity_key != str(row.family_duid)
            or family is None
            or family.get("active") is not True
        ):
            return None
        if row.field == "home_address":
            return _primary_address(row.primary_address)
        if type(family.get("sendNoMail")) is not bool:
            return None
        return KnownValue(True, family["sendNoMail"])
    member = corpus["member"].get(row.entity_key)
    in_family = member is not None and member["family_key"] == str(row.family_duid)
    if row.field == "moved_household":
        if member is not None and member["deceased"]:
            return None
        return KnownValue(True, not (in_family and member["active"]))
    if row.field == "deceased_status":
        return KnownValue(True, bool(member["deceased"])) if in_family else None
    if not in_family or (
        row.field != "death_date" and (not member["active"] or member["deceased"])
    ):
        return None
    try:
        current = member_field_value(
            member,
            corpus["contact"].get("member:" + row.entity_key),
            DEFINITIONS[row.field],
        )
    except MemberSourceUnavailable:
        # Not evidence of anything: reconciliation records the unusable
        # value for the open changes; a final one simply waits.
        return None
    return current if current.available else None


def _primary_address(found):
    """ParishSoft's primary address, in the census address shape.

    ``found`` is the snapshot's primary address record of the Family (its
    ``fields``), or None when the data has none. No read returns the
    country, so it is blank; ParishSoft's second and third street lines are
    joined into the second. Migration 0150's
    ``stewardship_refresh_census_source_v1`` builds the same. A field
    ParishSoft returned as null reads as blank, as SQL's ``coalesce`` does.
    """
    if found is None or type(found.get("fields")) is not dict:
        return None
    fields = found["fields"]

    def part(name):
        """One primary-address field, blank when absent or null."""
        return fields.get(name) or ""

    return KnownValue(
        True,
        {
            "line1": part("primaryAddress1"),
            "line2": " ".join(
                line
                for line in (part("primaryAddress2"), part("primaryAddress3"))
                if line
            ),
            "city": part("primaryCity"),
            "region": part("primaryState"),
            "postal_code": part("primaryPostalCode"),
            "country": "",
        },
    )


def _address_key(value):
    """An address's confirmation key: the census comparison without the
    country (no read returns ParishSoft's) and with a ZIP+4 read as its
    five digits. Migration 0150's ``stewardship_refresh_address_key_v1``."""
    if value is None:
        return None
    postal = value.get("postal_code") or ""
    found = ZIP_PLUS_FOUR.fullmatch(postal)
    return canonical_value(
        ValueKind.ADDRESS,
        value | {"country": "", "postal_code": found[1] if found else postal},
    )


def _same(field, kind):
    """The comparison of two ``KnownValue``s of one census field.

    A home address compares by ``_address_key``; a New Member's value is
    whether the Member is listed, so plain equality; every other field by
    the census compare rules.
    """
    if field == "new_member":
        return lambda left, right: left == right
    if field != "home_address":
        return _census_same(kind)

    def same(left, right):
        """Equal when both are unavailable, or both read with one key."""
        if left.available != right.available:
            return False
        return not left.available or _address_key(left.value) == _address_key(
            right.value
        )

    return same


def _annotate_marks(snapshot, corpus, rows):
    """Give each change what ``census_source`` reads besides the corpus.

    ``added_duid``: a New Member's ParishSoft Member ID, from the
    resolution that made its current mark (the one at the version before
    its current one). For a home address, ``mailing_answer`` (the Family's
    mailing address answer in the same response, its own census change,
    since the source worker may not read a response's answers) and
    ``primary_address`` (the Family's primary address record: the
    corpus's when it carries addresses, as a quick update's base does,
    else ``snapshot``'s rows, since the promotion's decoded corpus carries
    none). One query each, only when some change needs it.
    """
    members = [row for row in rows if row.field == "new_member"]
    found = {}
    if members:
        found = {
            (proposal, version): duid
            for proposal, version, duid in ProposalResolution.objects.filter(
                proposal_id__in=[row.pk for row in members], action="entered"
            ).values_list("proposal_id", "expected_version", "member_duid")
        }
    homes = {row.submission_id for row in rows if row.field == "home_address"}
    answers = {}
    if homes:
        answers = dict(
            ProposedChange.objects.filter(
                submission_id__in=homes, entity_kind="family", field="mailing_address"
            ).values_list("submission_id", "submitted_value")
        )
    addresses = {}
    keys = {
        f"family:{row.family_duid}:primary"
        for row in rows
        if row.field == "home_address"
    }
    if keys and "address" in corpus:
        addresses = {key: corpus["address"].get(key) for key in keys}
    elif keys:
        addresses = {
            key: json.loads(canonical)
            for key, canonical in SnapshotAddress.objects.filter(
                snapshot_id=snapshot.pk, source_key__in=keys
            ).values_list("source_key", "payload__canonical")
        }
    for row in rows:
        row.added_duid = found.get((row.pk, row.version - 1))
        row.mailing_answer = answers.get(row.submission_id)
        row.primary_address = addresses.get(f"family:{row.family_duid}:primary")


def _mailing_primary(row, current):
    """Whether ParishSoft's primary address is evidently the mailing block.

    It is when it holds the Family's mailing answer and not its home one:
    the home address then has no evidence and gets no check (#1749).
    """
    carried = row.admin_value if row.admin_value_set else row.submitted_value
    mailing = row.mailing_answer
    return (
        mailing is not None
        and _address_key(current.value) == _address_key(mailing)
        and _address_key(current.value) != _address_key(carried)
    )


def _census_checks(snapshot, corpus, campaign_id, full, start):
    """Yield the census checks this promotion owes, unsaved.

    Not for a change a later response of the Family replaced (``replaced``).

    For every live census change Entered by hand or Updated in ParishSoft of
    a confirmable Member field, the Family's email opt-out or home address,
    or a New Member (``CHECKED``), for which the data gives evidence
    (``census_source``). The value recorded at the mark
    is the change's stored ParishSoft value, which the mark froze, or for a
    mark made again over Entered incorrectly the ParishSoft value the check
    before it found (``_remarked``, #1737).
    """
    rows = ProposedChange.objects.filter(
        CHECKED,
        submission__campaign_id=campaign_id,
        submission__mode="live",
        execution__in=FINAL_EXECUTIONS,
    ).annotate(
        family_duid=F("submission__family__family_duid"),
        family_id=F("submission__family_id"),
        family_version=F("submission__family_version"),
    )
    # A change a later response of the Family replaced (its field answered
    # again) is history and gets no check (``replaced``, #1737).
    latest = dict(
        (
            ((family, kind, key, field), version)
            for family, kind, key, field, version in ProposedChange.objects.filter(
                CHECKED,
                submission__campaign_id=campaign_id,
                submission__mode="live",
            )
            .values_list("submission__family_id", "entity_kind", "entity_key", "field")
            .annotate(latest=Max("submission__family_version"))
        )
    )
    rows = [
        row
        for row in rows
        if row.family_version
        >= latest[(row.family_id, row.entity_kind, row.entity_key, row.field)]
    ]
    remarked = _remarked(rows)
    _annotate_marks(snapshot, corpus, rows)
    states = (
        check_states(
            proposals=[row.pk for row in rows],
            versions={str(row.pk): row.version for row in rows},
        )
        if rows
        else {}
    )
    threshold = None if start is None else start.not_seen_after
    for row in rows:
        state = states.get(str(row.pk), CheckState(not_seen_after=threshold))
        if state.outcome == "confirmed":
            continue
        current = census_source(row, corpus)
        if current is None or (
            row.field == "home_address" and _mailing_primary(row, current)
        ):
            continue
        kind = (
            FAMILY_KINDS[row.field]
            if row.entity_kind == "family"
            else ValueKind.BOOLEAN
            if row.field == "new_member"
            else DEFINITIONS[row.field].kind
        )
        entered = row.execution == "resolved_external"
        stage = (
            (
                Stage.ENTERED_INCORRECTLY
                if state.outcome == "entered_incorrectly"
                else Stage.ENTERED_BY_HAND
            )
            if entered
            else Stage.UPDATED
        )
        carried = row.admin_value if row.admin_value_set else row.submitted_value
        answered = KnownValue(row.baseline_available, row.baseline_value)
        if row.field == "new_member":
            # A New Member's value is whether the data lists them: wanted
            # listed, answered against not listed (#1749).
            carried, answered = True, KnownValue(True, False)
        same = _same(row.field, kind)
        # The value recorded at the mark: ParishSoft's at a mark made again
        # (#1737), else the change's own, which the first mark froze. One
        # ParishSoft could not report then is no evidence the entry went
        # wrong, so the refresh's own value stands in for it (unchanged,
        # never Entered incorrectly), as it does for a field with no third
        # value to be wrong at.
        again = remarked.get(row.pk)
        if row.field in NO_THIRD_VALUE:
            marked = current
        elif again is not None:
            marked = KnownValue(True, again.get("value"))
        elif row.current_available:
            marked = KnownValue(True, row.current_value)
        else:
            marked = current
        result = refresh_outcome(
            stage,
            refreshed=current,
            answered_against=answered,
            carried_forward=KnownValue(True, carried),
            marked_against=marked,
            full_refreshes_unchanged=state.unseen + full,
            not_seen_after=threshold if threshold is not None else float("inf"),
            same=same,
        )
        stored = STORED.get(result)
        if stored == CONFLICT and not full:
            # Only a full refresh moves an Updated in ParishSoft change to
            # Conflict: a quick update's change list could lag the write,
            # and its base still hold the old value (#1749).
            continue
        if stored == UNCHANGED and state.outcome == "entered_incorrectly":
            # Rule 2: ParishSoft back at the value recorded at the mark is
            # no new evidence; the item stays where it is (#1737's fixes,
            # or ParishSoft holding the reviewer's value, move it on).
            continue
        observed = {"available": True, "value": current.value}
        moved = state.observed is None or not same(
            KnownValue(True, state.observed.get("value")), current
        )
        if stored is None or not _owed(
            stored, state, full=full, marked=entered, moved=moved
        ):
            continue
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            proposal_id=row.pk,
            item_version=row.version,
            outcome=stored,
            observed=observed,
        )


def _ministry_checks(snapshot, corpus, campaign_id, full, start):
    """Yield the Ministry checks this promotion owes, unsaved.

    For every live request a person resolved or closed (a refresh-resolved
    one is Confirmed already). An accepted request is judged by the
    Ministry carried forward (the Edit's, else the Family's) and is
    Confirmed when the roster shows it; an ignored one by the Family's, and
    is noted when ParishSoft now matches the Family's answer. A Member no
    longer an active, living Member of the Family, or a Ministry the data no
    longer lists, gives no evidence and no check.
    """
    memberships = {
        (row["member_key"], row["ministry_key"])
        for row in corpus["roster"].values()
        if row["current"]
    }
    rows = list(
        MinistryRequest.objects.filter(
            submission__campaign_id=campaign_id,
            submission__mode="live",
            entity_kind="member",
            state__in=("resolved", "closed_no_response"),
            resolution_source__isnull=True,
        ).annotate(family_duid=F("submission__family__family_duid"))
    )
    if not rows:
        return
    ticks = {}
    for request_id, entered, created_at in (
        MinistryRosterEntry.objects.filter(request_id__in=[row.pk for row in rows])
        .order_by("request_id", "sequence")
        .values_list("request_id", "entered", "created_at")
    ):
        ticks[str(request_id)] = {"entered": entered, "at": created_at}
    states = check_states(
        requests=[row.pk for row in rows],
        marks=ticks,
        versions={str(row.pk): row.version for row in rows},
    )
    threshold = None if start is None else start.not_seen_after
    for row in rows:
        key = str(row.pk)
        state = states.get(key, CheckState(not_seen_after=threshold))
        if state.outcome == "confirmed":
            continue
        accepted = row.state == "resolved" and row.outcome in ACCEPTED_OUTCOMES
        ministry_duid = (
            row.reviewed_ministry_duid or row.ministry_duid
            if accepted
            else row.ministry_duid
        )
        member = corpus["member"].get(row.entity_key)
        ministry = corpus["ministry"].get(str(ministry_duid))
        if (
            member is None
            or member["family_key"] != str(row.family_duid)
            or not member["active"]
            or member["deceased"]
            or ministry is None
            or not ministry["catalog_present"]
        ):
            continue
        current = (row.entity_key, str(ministry_duid)) in memberships
        wanted = row.action == "join"
        ticked = accepted and ticks.get(key, {}).get("entered", False)
        stage = (
            (Stage.ENTERED_BY_HAND if ticked else Stage.TO_ENTER_BY_HAND)
            if accepted
            else Stage.IGNORED
        )
        result = refresh_outcome(
            stage,
            refreshed=current,
            # A roster state is in or not: a request was answered against
            # the opposite of what it asks, so rule 3 never applies.
            answered_against=not wanted,
            carried_forward=wanted,
            full_refreshes_unchanged=state.unseen + full,
            not_seen_after=threshold if threshold is not None else float("inf"),
        )
        stored = STORED[result]
        if not _owed(stored, state, full=full, marked=ticked):
            continue
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            request_id=row.pk,
            item_version=row.version,
            outcome=stored,
        )


def _checks(snapshot, corpus, campaign_id, full):
    """Every check one promotion of ``corpus`` owes, census then Ministry."""
    start = _start()
    yield from _census_checks(snapshot, corpus, campaign_id, full, start)
    yield from _ministry_checks(snapshot, corpus, campaign_id, full, start)


def record_refresh_checks(snapshot, corpus, *, campaign_id, full):
    """Record the checks the promotion of ``snapshot`` owes; how many.

    Runs in the promotion's work order, after reconciliation, so a request
    reconciliation just resolved is Confirmed already and gets none.
    ``full`` is whether this is a full refresh (only those count towards
    Not seen in ParishSoft). The guard checks the worker, the promotion,
    each item's state and version and the snapshot's own evidence.
    """
    require_work_order()
    checks = list(_checks(snapshot, corpus, campaign_id, full))
    RefreshCheck.objects.bulk_create(checks)
    for check in checks:
        if check.outcome == CONFLICT:
            _to_conflict(snapshot, check)
    return len(checks)


def _to_conflict(snapshot, check):
    """Move the Updated in ParishSoft change ``check`` found changed to Conflict.

    As reconciliation moves an open change (#1749): ParishSoft's value
    from this snapshot, pinned for the change's response, at the next
    version, and its decision back to To review, so neither a later
    refresh nor the Family's same answer carries the approval back into
    force over the undo in ParishSoft (an Administrator's accepted value at
    the earlier data is no longer current either, since the change's data
    reference moves on). The census guard admits it only with this check,
    recorded just before at the change's version.
    """
    row = ProposedChange.objects.only("submission_id", "current_source_id").get(
        pk=check.proposal_id
    )
    pin_snapshot(
        snapshot.pk,
        parent_kind="submission",
        parent_id=row.submission_id,
        admit=_pin_admission,
    )
    moved = ProposedChange.objects.filter(
        pk=check.proposal_id, execution="published", version=check.item_version
    ).update(
        current_available=True,
        current_value=check.observed["value"],
        current_source_id=snapshot.pk,
        execution="conflict",
        decision="unreviewed",
        version=F("version") + 1,
    )
    if moved != 1:
        # The guard locked and checked this change at that version; any
        # other count means the check would stand alone, so the promotion
        # rolls back instead.
        raise StorageInvariantError("The updated change moved under its check.")
    _release_unused_source(row.submission_id, row.current_source_id)


def refresh_checks_current(snapshot, corpus, *, campaign_id):
    """Whether a quick update's promotion of ``corpus`` would record no check.

    A quick update is never full, so only a changed outcome would be owed.
    """
    return next(_checks(snapshot, corpus, campaign_id, False), None) is None
