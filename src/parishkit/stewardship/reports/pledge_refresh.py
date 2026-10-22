"""Pledge refresh checks: the refresh re-evaluation of pledges (#1704, #1747).

Every promoted refresh re-evaluates the financial stream's pledges not yet
Confirmed against ParishSoft's pledge for the upcoming stewardship year
(review pipeline spec, "Financial stream", Pledge confirmation), as
``responses.refresh_checks`` does for census changes and Ministry requests.
A pledge's stage is derived from its ``PledgeReview`` rows, so the refresh
records what it found in a check row, in the same promotion transaction: a
decided pledge's in a ``PledgeRefreshCheck`` naming its decision row (frozen
migration 0134, provisional number), a To review pledge's in a
``PledgeOpenCheck`` naming the Family's current response and the item's
version (0135, provisional). The shared selection
``stewardship_pledge_stages_v1`` places the pledge from its latest check.

- ``pledge_checks`` yields the checks one promotion owes, which
  ``refresh_checks.record_refresh_checks`` writes with the others;
- ``pledge_check_states`` reads each decided item's check run for the
  page's Not seen in ParishSoft flag;
- ``marked_pledge`` is ParishSoft's amount an Entered by hand mark records,
  and ``answered_pledge`` the amount a Family's response records (#1753).

R is the Family's ParishSoft pledge for the campaign's upcoming financial
period in the snapshot (``stewardship_pledge_upcoming_v1``, which the guards
read too, so both see the same evidence), read only with the snapshot's
proven giving read (``giving_observation``, the Family form's rule). No
pledge reads as $0.00. A is ParishSoft's amount recorded on the response
where the Family's current pledge answer began (``answered_pledge``, #1753;
$0.00 when it recorded none), or the amount of the Conflict the decision in
force was taken on; C is the annual pledge carried forward. Frequency is not
compared until its smoke check, and share methods have no read.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import RefreshOutcome, Stage, refresh_outcome
from parishkit.stewardship.responses.financial_inputs import (
    InvalidFinancialSource,
    financial_definition,
    giving_observation,
)
from parishkit.stewardship.responses.refresh_checks import (
    STORED,
    UNCHANGED,
    CheckState,
    owed,
    start_row,
    summaries,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot

from .money import MoneyAmount
from .pledge_models import PledgeOpenCheck, PledgeRefreshCheck

# The stages (0127's, from the decision rows alone) whose check names a
# decision row; a To review pledge's check names its response (#1747).
CHECKED_STAGES = frozenset({"to_enter_by_hand", "entered_by_hand", "ignored"})
OPEN = "to_review"
# What a refresh outcome stores: the shared names, and a Conflict (#1747).
# ParishSoft holding C before the mark (``ALREADY_HELD``, #1890) is stored
# as ``confirmed`` too: the check ends a Conflict and the page says
# ParishSoft already holds the pledge, but the stage selection
# (``stewardship_pledge_stages_v1``, migration 0159) places only a marked
# pledge's confirmed check as Confirmed.
PLEDGE_STORED = {
    **STORED,
    RefreshOutcome.CONFLICT: "conflict",
    RefreshOutcome.ALREADY_HELD: "confirmed",
}
CONFLICT = "conflict"
# The largest amount a mark can record (``PledgeReview.marked_pledge``).
MARK_LIMIT = Decimal("1e12")
# The largest amount a response can record (``Submission.upcoming_pledge``,
# numeric(24,2)): it only mirrors the column type, since the selection's own
# numeric(24,2) cast already raises on a larger sum (absorbed as none).
ANSWER_LIMIT = Decimal("1e22")
UPCOMING = (
    "SELECT duid, stewardship_pledge_upcoming_v1(%s, %s, duid) "
    "FROM unnest(%s::bigint[]) AS duid"
)
# The pledges a refresh re-evaluates, read through the source worker's
# definer selection (0135), which the guards read too: the worker reads no
# response answers or decision rows itself.
ITEMS = (
    "SELECT family_id, family_duid, submission_id, review_id, sequence, "
    "base_stage, decision, entered, carried, marked, baseline, check_outcome, "
    "observed FROM stewardship_pledge_refresh_items_v2(%s, NULL, NULL) "
    "ORDER BY family_duid"
)


@dataclass(frozen=True, slots=True)
class Pending:
    """One pledge a refresh re-evaluates, as the definer selection returns it.

    ``base_stage`` is the stage its decision rows give (``to_review`` for a
    pledge no decision is in force for, whose check names ``submission_id``
    at ``sequence``; a decided one's names ``review_id``). ``carried`` is C
    (the Edit's annual pledge, else the Family's), ``marked`` the amount
    recorded at the mark (A without one) and ``baseline`` A.
    ``check`` and ``observed`` are its latest counting check's outcome and
    amount (None without one).
    """

    family_id: UUID
    family_duid: int
    submission_id: UUID
    review_id: UUID | None
    sequence: int
    base_stage: str
    decision: str | None
    entered: bool
    carried: Decimal | None
    marked: Decimal
    baseline: Decimal
    check: str | None
    observed: Decimal | None


def display(amount):
    """An amount as the page shows money, cents kept ("$1,200.00")."""
    return MoneyAmount(int(amount * 100)).display


def checked(item):
    """Whether a ``pledge_review.Item``'s checks name its decision row in force."""
    review = item.review
    return bool(
        item.in_force
        and review is not None
        and review.decision is not None
        and item.base_stage in CHECKED_STAGES
    )


def check_states(reviews):
    """The latest check state under each decision row, keyed as ``reviews`` say.

    ``reviews`` maps a decision row's id to the key to return its state
    under. A check names the row it was made under, so only the checks of
    an item's latest row in force count: a later decision or a changed
    pledge leaves them behind. Two queries (the Not seen start and the
    checks), none without a row.
    """
    if not reviews:
        return {}
    rows = (
        PledgeRefreshCheck.objects.filter(review_id__in=list(reviews))
        .order_by("created_at", "id")
        .values_list("review_id", "created_at", "outcome", "full_refresh", "observed")
    )
    # Every check of a mark's row follows the mark, so no mark time is needed.
    states = summaries(
        [
            (str(review), at, outcome, full, observed)
            for review, at, outcome, full, observed in rows
        ],
        start_row(),
        {},
    )
    return {
        key: states[str(review)]
        for review, key in reviews.items()
        if str(review) in states
    }


def pledge_check_states(items):
    """Each decided ``Item``'s check run (Not seen), by its submission id."""
    return check_states(
        {item.review.pk: item.submission_id for item in items if checked(item)}
    )


def _definition(campaign_id):
    """The campaign's financial definition, or None without a financial period."""
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    try:
        return financial_definition(
            campaign.active_configuration.values, campaign_id=campaign_id
        )
    except InvalidFinancialSource:
        return None


def _upcoming(snapshot_id, campaign_id, duids):
    """Each Family's ParishSoft upcoming-year pledge in the snapshot, by DUID.

    One statement, through the SQL selection the guard reads; a Family the
    selection cannot read (no giving read) maps to None.
    """
    with connection.cursor() as cursor:
        cursor.execute(UPCOMING, [snapshot_id, campaign_id, sorted(duids)])
        return dict(cursor.fetchall())


def _cursor(snapshot_id):
    """A snapshot's stored cursor, read afresh.

    Only the cursor: web reads no other snapshot column (as ``giving_proof``
    does), and a caller's snapshot object may predate its finished cursor
    (the quick update's check is handed the staging snapshot).
    """
    return (
        SourceSnapshot.objects.filter(pk=snapshot_id)
        .values_list("cursor", flat=True)
        .first()
    )


def _pending(campaign_id):
    """The campaign's pledges a refresh re-evaluates (``Pending``), one statement."""
    with connection.cursor() as cursor:
        cursor.execute(ITEMS, [campaign_id])
        return [Pending(*row) for row in cursor.fetchall()]


def _stage(item, outcome):
    """The pipeline stage a refresh judges the pledge at, given its latest check."""
    if outcome == CONFLICT:
        return Stage.CONFLICT
    if item.base_stage == OPEN:
        return Stage.TO_REVIEW
    if item.decision == "ignored":
        return Stage.IGNORED
    if not item.entered:
        return Stage.TO_ENTER_BY_HAND
    if outcome == "entered_incorrectly":
        return Stage.ENTERED_INCORRECTLY
    return Stage.ENTERED_BY_HAND


def _check(snapshot, full, item, outcome, refreshed):
    """One unsaved check row: an open one for a To review pledge."""
    if item.base_stage == OPEN:
        return PledgeOpenCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            family_id=item.family_id,
            submission_id=item.submission_id,
            item_version=item.sequence,
            outcome=outcome,
            observed=refreshed,
        )
    return PledgeRefreshCheck(
        snapshot_id=snapshot.pk,
        full_refresh=full,
        review_id=item.review_id,
        item_version=item.sequence,
        outcome=outcome,
        observed=refreshed,
    )


def pledge_checks(snapshot, corpus, campaign_id, full, start, *, evidence=None):
    """Yield the pledge checks this promotion owes, unsaved.

    For every pledge not yet Confirmed (To review, and decided in force,
    including one before its mark ParishSoft already holds, #1890) of
    a Family the snapshot still lists (rule 4: one it no longer lists gives
    no evidence), when the campaign has a financial period and the
    snapshot's giving read is proven for it. The rules are
    ``pipeline.refresh_outcome``'s with money compared exactly; the repeats
    ``refresh_checks.owed`` leaves out record nothing, and a Conflict is
    written again only when ParishSoft's amount moves (#1747).

    ``evidence`` is the snapshot whose pledges and giving proof are read,
    ``snapshot`` itself unless given: the quick update's check passes the
    current snapshot, since its own staging snapshot holds no pledge rows
    or cursor yet while its corpus equals the current one.
    """
    evidence = snapshot.pk if evidence is None else evidence
    definition = _definition(campaign_id)
    if definition is None or giving_observation(_cursor(evidence), definition) is None:
        return
    items = [
        item
        for item in _pending(campaign_id)
        if str(item.family_duid) in corpus["family"]
    ]
    if not items:
        return
    # A decided pledge's check run counts its unchanged full refreshes (Not
    # seen); a To review pledge's latest check comes with the selection.
    states = check_states(
        {item.review_id: item.review_id for item in items if item.base_stage != OPEN}
    )
    amounts = _upcoming(evidence, campaign_id, {item.family_duid for item in items})
    threshold = None if start is None else start.not_seen_after
    for item in items:
        state = (
            CheckState(item.check or UNCHANGED, item.observed)
            if item.base_stage == OPEN
            else states.get(item.review_id, CheckState(not_seen_after=threshold))
        )
        refreshed = amounts.get(item.family_duid)
        # No evidence (no R) or no C: nothing to compare. Every financial
        # answer stores its annual pledge today; a missing one is skipped,
        # never allowed to abort the promotion. Only a marked pledge is
        # Confirmed and never checked again (#1890): one before its mark
        # whose check found ParishSoft holding it is checked again, so a
        # later amount takes the words away or makes it a Conflict.
        if (
            (state.outcome == "confirmed" and item.entered)
            or refreshed is None
            or item.carried is None
        ):
            continue
        refreshed = Decimal(refreshed)
        result = refresh_outcome(
            _stage(item, state.outcome),
            refreshed=refreshed,
            answered_against=Decimal(item.baseline),
            carried_forward=Decimal(item.carried),
            marked_against=Decimal(item.marked),
            full_refreshes_unchanged=state.unseen + full,
            not_seen_after=threshold if threshold is not None else float("inf"),
        )
        stored = PLEDGE_STORED.get(result)
        if stored == UNCHANGED and state.outcome == "entered_incorrectly":
            # Rule 2: ParishSoft back at the amount recorded at the mark is
            # no new evidence; the pledge stays Entered incorrectly (#1737's
            # fixes, or ParishSoft holding the reviewer's amount, move it).
            continue
        moved = state.observed is None or Decimal(state.observed) != refreshed
        if stored is None:
            continue
        if stored == CONFLICT and state.outcome == CONFLICT:
            # A Conflict is written again only when ParishSoft's amount
            # moves, so ParishSoft now shows the amount to decide against.
            if not moved:
                continue
        elif not owed(stored, state, full=full, marked=item.entered, moved=moved):
            continue
        yield _check(snapshot, full, item, stored, refreshed)


def proven_pledge(snapshot_id, campaign_id, family_duid, *, limit):
    """ParishSoft's upcoming-year amount for one Family in a snapshot, or None.

    Only with the snapshot's proven giving read (the rule ``pledge_checks``
    applies) and an amount under ``limit``, the largest the recording
    column can hold; otherwise None, which records no amount. The caller
    holds the work order, so no promotion moves the snapshot first.
    """
    definition = _definition(campaign_id)
    if definition is None or snapshot_id is None:
        return None
    if giving_observation(_cursor(snapshot_id), definition) is None:
        return None
    amount = _upcoming(snapshot_id, campaign_id, {family_duid}).get(family_duid)
    if amount is None or abs(Decimal(amount)) >= limit:
        return None
    return Decimal(amount)


def marked_pledge(campaign_id, family_duid):
    """ParishSoft's upcoming-year amount an Entered by hand mark records, or None.

    Read from the current snapshot (``proven_pledge``), in the decision's
    own work order; the pledge review guard checks the amount against the
    same snapshot.
    """
    snapshot_id = (
        SourceCurrent.objects.filter(singleton=True)
        .values_list("snapshot_id", flat=True)
        .first()
    )
    return proven_pledge(snapshot_id, campaign_id, family_duid, limit=MARK_LIMIT)


def answered_pledge(snapshot_id, campaign_id, family_duid):
    """ParishSoft's upcoming-year amount a Family's response records, or None.

    The financial stream's A for the Family's answer (#1753): read at the
    submit from the response's validation snapshot (the current one, which
    the submission guard requires) by ``proven_pledge``; the response's own
    guard (0136) pins it to the same snapshot's sum. None, when the read is
    unproven, records no amount. The read runs in a savepoint, so a source
    row the selection cannot read (a database error) also records none
    instead of aborting the Family's submit; the guard reads nothing then.
    Such an error is logged as a warning (its kind and class, never an
    amount), so a broken read in Production is seen rather than silent.
    """
    try:
        with transaction.atomic():
            return proven_pledge(
                snapshot_id, campaign_id, family_duid, limit=ANSWER_LIMIT
            )
    except DatabaseError as error:
        emit_failure(
            error,
            event=Event.REPORT_SHAPING_FAILED,
            level=logging.WARNING,
            shaping="answered_pledge",
            name_class=True,
        )
        return None
