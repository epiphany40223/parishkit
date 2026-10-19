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
  death and the Family's email opt-out each its own way (#1738).

The comparison is ``pipeline.refresh_outcome`` with each stream's canonical
comparison: the census compare rules (``comparison.canonical_value``) and a
Member's roster state for the Ministry carried forward. A check is written
only when its outcome changes, or, for an item marked Entered by hand, once
per full refresh until Not seen is reached, so a quiet refresh writes
nothing. A Confirmed item is never checked again; an item whose Member or
Ministry left the data gets no check (rule 4: it keeps its stage), except a
move, which leaving the Family is the evidence of.
"""

from dataclasses import dataclass

from django.db.models import F, Q

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.pipeline import (
    CENSUS_CONFIRMABLE,
    RefreshOutcome,
    Stage,
    refresh_outcome,
)
from parishkit.stewardship.workflows.models import (
    ACCEPTED_OUTCOMES,
    MinistryRequest,
    MinistryRosterEntry,
    RefreshCheck,
    RefreshCheckStart,
)

from .census import FAMILY_FIELDS
from .comparison import canonical_value
from .inputs import MEMBER_FIELDS, MemberSourceUnavailable, member_field_value
from .member_requests import REQUEST_FIELDS
from .merge import KnownValue
from .models import ProposedChange

# The census executions a refresh check is for: final in the census guard.
FINAL_EXECUTIONS = ("published", "resolved_external")
# The confirmable Member fields: the ones the Member contact read carries,
# and a move and a death, each read its own way (``census_source``, #1738).
DEFINITIONS = {field.name: field for field in (*MEMBER_FIELDS, *REQUEST_FIELDS)}
CHECKED_FIELDS = frozenset(
    name for name in DEFINITIONS if CENSUS_CONFIRMABLE.get(("member", name))
)
# The Family field a refresh confirms: the email opt-out, against the
# Family's sendNoMail flag (Administrator, 2026-10-18, #1680). The
# addresses wait for #1688's smoke test (#1749).
CHECKED_FAMILY_FIELDS = frozenset({"email_opt_out"})
# A yes-or-no request (moved, deceased) and an opt-out whose baseline was
# never read have no third value to be wrong at: never Entered incorrectly.
NO_THIRD_VALUE = frozenset({"moved_household", "deceased_status", "email_opt_out"})
FAMILY_KINDS = {field.name: field.kind for field in FAMILY_FIELDS}
# The stored outcome of each comparison result. A published change found
# back at A or at a third value would be a Conflict, which a final change
# cannot take yet (#1738), so it records nothing.
STORED = {
    RefreshOutcome.CONFIRMED: "confirmed",
    RefreshOutcome.UNCHANGED: "unchanged",
    RefreshOutcome.NOT_SEEN: "unchanged",
    RefreshOutcome.ENTERED_INCORRECTLY: "entered_incorrectly",
    RefreshOutcome.IGNORED_NOW_MATCHES: "ignored_now_matches",
}
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
      is active.
    """
    if row.entity_kind == "family":
        family = corpus["family"].get(str(row.family_duid))
        if (
            row.field not in CHECKED_FAMILY_FIELDS
            or row.entity_key != str(row.family_duid)
            or family is None
            or family.get("active") is not True
            or type(family.get("sendNoMail")) is not bool
        ):
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


def _census_checks(snapshot, corpus, campaign_id, full, start):
    """Yield the census checks this promotion owes, unsaved.

    For every live census change Entered by hand or Updated in ParishSoft of
    a confirmable Member field or the Family's email opt-out for which the
    data gives evidence (``census_source``). The value recorded at the mark
    is the change's stored ParishSoft value, which the mark froze.
    """
    rows = ProposedChange.objects.filter(
        Q(entity_kind="member", field__in=CHECKED_FIELDS)
        | Q(entity_kind="family", field__in=CHECKED_FAMILY_FIELDS),
        submission__campaign_id=campaign_id,
        submission__mode="live",
        execution__in=FINAL_EXECUTIONS,
    ).annotate(family_duid=F("submission__family__family_duid"))
    rows = list(rows)
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
        if current is None:
            continue
        kind = (
            FAMILY_KINDS[row.field]
            if row.entity_kind == "family"
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
        same = _census_same(kind)
        # The value recorded at the mark; one ParishSoft could not report
        # then is no evidence the entry went wrong, so the refresh's own
        # value stands in for it (unchanged, never Entered incorrectly), as
        # it does for a field with no third value to be wrong at.
        marked = (
            KnownValue(True, row.current_value)
            if row.current_available and row.field not in NO_THIRD_VALUE
            else current
        )
        result = refresh_outcome(
            stage,
            refreshed=current,
            answered_against=KnownValue(row.baseline_available, row.baseline_value),
            carried_forward=KnownValue(True, carried),
            marked_against=marked,
            full_refreshes_unchanged=state.unseen + full,
            not_seen_after=threshold if threshold is not None else float("inf"),
            same=same,
        )
        stored = STORED.get(result)
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
    return len(checks)


def refresh_checks_current(snapshot, corpus, *, campaign_id):
    """Whether a quick update's promotion of ``corpus`` would record no check.

    A quick update is never full, so only a changed outcome would be owed.
    """
    return next(_checks(snapshot, corpus, campaign_id, False), None) is None
