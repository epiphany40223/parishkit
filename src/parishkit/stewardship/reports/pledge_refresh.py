"""Pledge refresh checks: the refresh re-evaluation of decided pledges (#1704).

Every promoted refresh re-evaluates the financial stream's decided pledges
against ParishSoft's pledge for the upcoming stewardship year (review
pipeline spec, "Financial stream", Pledge confirmation), as
``responses.refresh_checks`` does for census changes and Ministry requests.
A pledge's stage is derived from its ``PledgeReview`` rows, so the refresh
records what it found in a ``PledgeRefreshCheck`` (frozen migration 0134,
provisional number), in the same promotion transaction:

- ``pledge_checks`` yields the checks one promotion owes, which
  ``refresh_checks.record_refresh_checks`` writes with the others;
- ``pledge_check_states`` reads each item's latest check for the page and
  the decision service, which place the pledge from it
  (``pipeline.financial_placement``);
- ``marked_pledge`` is ParishSoft's amount an Entered by hand mark records.

R is the Family's ParishSoft pledge for the campaign's upcoming financial
period in the snapshot (``stewardship_pledge_upcoming_v1``, which the guard
reads too, so both see the same evidence), read only with the snapshot's
proven giving read (``giving_observation``, the Family form's rule). No
pledge reads as $0.00. A is $0.00 (a response records no snapshot) and C
the annual pledge carried forward. Frequency is not compared until its
smoke check, and share methods have no read.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db import connection

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.pipeline import Stage, refresh_outcome
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
from .pledge_models import PledgeRefreshCheck

ZERO = Decimal("0.00")
# The stages a refresh re-evaluates: a decision in force. To review waits
# for #1747 (it has no decision row to name, and a third value would be a
# Conflict the stream cannot show).
CHECKED_STAGES = frozenset({"to_enter_by_hand", "entered_by_hand", "ignored"})
# The largest amount a mark can record (``PledgeReview.marked_pledge``).
MARK_LIMIT = Decimal("1e12")
UPCOMING = (
    "SELECT duid, stewardship_pledge_upcoming_v1(%s, %s, duid) "
    "FROM unnest(%s::bigint[]) AS duid"
)
# The decided pledges in force, read through the source worker's definer
# selection (0134), which the guard reads too: the worker reads no response
# answers or decision rows itself.
ITEMS = (
    "SELECT family_duid, review_id, sequence, decision, entered, carried, marked "
    "FROM stewardship_pledge_refresh_items_v1(%s, NULL) ORDER BY family_duid"
)


@dataclass(frozen=True, slots=True)
class Decided:
    """One decided pledge in force, as the definer selection returns it.

    ``carried`` is C (the Edit's annual pledge, else the Family's) and
    ``marked`` the amount recorded at the mark, $0.00 (A) without one.
    """

    family_duid: int
    review_id: UUID
    sequence: int
    decision: str
    entered: bool
    carried: Decimal
    marked: Decimal


def display(amount):
    """An amount as the page shows money, cents kept ("$1,200.00")."""
    return MoneyAmount(int(amount * 100)).display


def checked(item):
    """Whether a refresh re-evaluates a ``pledge_review.Item``: a decision in force."""
    review = item.review
    return bool(
        item.in_force
        and review is not None
        and review.decision is not None
        and item.stage in CHECKED_STAGES
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
    """The page's check states: each checked ``Item``'s, by its submission id."""
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


def _decided(campaign_id):
    """The campaign's decided pledges in force (``Decided``), one statement."""
    with connection.cursor() as cursor:
        cursor.execute(ITEMS, [campaign_id])
        return [Decided(*row) for row in cursor.fetchall()]


def _stage(item, state):
    """The pipeline stage a refresh judges the pledge at."""
    if item.decision == "ignored":
        return Stage.IGNORED
    if not item.entered:
        return Stage.TO_ENTER_BY_HAND
    if state.outcome == "entered_incorrectly":
        return Stage.ENTERED_INCORRECTLY
    return Stage.ENTERED_BY_HAND


def pledge_checks(snapshot, corpus, campaign_id, full, start, *, evidence=None):
    """Yield the pledge checks this promotion owes, unsaved.

    For every decided pledge in force of a Family the snapshot still lists
    (rule 4: one it no longer lists gives no evidence), when the campaign
    has a financial period and the snapshot's giving read is proven for it.
    The rules are ``pipeline.refresh_outcome``'s with money compared
    exactly; a result the stream cannot store (a Conflict, #1747) records
    nothing, as do the repeats ``refresh_checks.owed`` leaves out.

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
        for item in _decided(campaign_id)
        if str(item.family_duid) in corpus["family"]
    ]
    if not items:
        return
    states = check_states({item.review_id: item.review_id for item in items})
    amounts = _upcoming(evidence, campaign_id, {item.family_duid for item in items})
    threshold = None if start is None else start.not_seen_after
    for item in items:
        state = states.get(item.review_id, CheckState(not_seen_after=threshold))
        refreshed = amounts.get(item.family_duid)
        # No evidence (no R) or no C: nothing to compare. Every financial
        # answer stores its annual pledge today; a missing one is skipped,
        # never allowed to abort the promotion.
        if state.outcome == "confirmed" or refreshed is None or item.carried is None:
            continue
        refreshed = Decimal(refreshed)
        result = refresh_outcome(
            _stage(item, state),
            refreshed=refreshed,
            answered_against=ZERO,
            carried_forward=Decimal(item.carried),
            marked_against=Decimal(item.marked),
            full_refreshes_unchanged=state.unseen + full,
            not_seen_after=threshold if threshold is not None else float("inf"),
        )
        stored = STORED.get(result)
        if stored == UNCHANGED and state.outcome == "entered_incorrectly":
            # Rule 2: ParishSoft back at the amount recorded at the mark is
            # no new evidence; the pledge stays Entered incorrectly (#1737's
            # fixes, or ParishSoft holding the reviewer's amount, move it).
            continue
        moved = state.observed is None or Decimal(state.observed) != refreshed
        if stored is None or not owed(
            stored, state, full=full, marked=item.entered, moved=moved
        ):
            continue
        yield PledgeRefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            review_id=item.review_id,
            item_version=item.sequence,
            outcome=stored,
            observed=refreshed,
        )


def marked_pledge(campaign_id, family_duid):
    """ParishSoft's upcoming-year amount an Entered by hand mark records, or None.

    Read from the current snapshot, only with its proven giving read (the
    rule ``pledge_checks`` applies), in the decision's own work order, so no
    promotion moves the snapshot before the row is written; the pledge
    review guard checks the amount against the same snapshot. An amount
    the row cannot hold is recorded as none.
    """
    definition = _definition(campaign_id)
    snapshot_id = (
        SourceCurrent.objects.filter(singleton=True)
        .values_list("snapshot_id", flat=True)
        .first()
    )
    if definition is None or snapshot_id is None:
        return None
    if giving_observation(_cursor(snapshot_id), definition) is None:
        return None
    amount = _upcoming(snapshot_id, campaign_id, {family_duid}).get(family_duid)
    if amount is None or abs(Decimal(amount)) >= MARK_LIMIT:
        return None
    return Decimal(amount)
