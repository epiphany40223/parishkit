"""The financial stream's review history: one immutable row per decision (#1686).

Pledges (``reports.pledge_review``) is the financial stream's review
worklist. Each Accept, Edit, Ignore, Back to review and Entered by hand on a
Family's pledge is one ``PledgeReview`` row, and the Family's item is its
current pledge answer with its latest row: no stage is stored (review
pipeline spec, "Financial stream"). Frozen migration 0135 (provisional
number) creates the table with its guard, which admits INSERT only, from a
current Administrator or Staff member, for the Family's current live
response, at the item's next ``sequence``, in a decision its derived stage
offers, and with its audit event.
"""

from decimal import Decimal

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord

# What a row records, in the order the worklist's icons offer them.
PLEDGE_ACTIONS = ("accept", "edit", "ignore", "back_to_review", "entered_by_hand")
PLEDGE_DECISIONS = ("accepted", "accepted_edited", "ignored")
# The Family form's frequencies; a zero pledge may have none (NULL).
PLEDGE_FREQUENCIES = ("weekly", "monthly", "quarterly", "annual")


class PledgeReview(ImmutableRecord):
    """One decision on a Family's pledge answer, and the state it leaves.

    ``family`` is the Family's campaign record and ``submission`` the live
    response the decision was made on; ``answer`` is that response's pledge
    answer as SQL fingerprints it (``stewardship_pledge_answer_v1``), set by
    the guard, never by the caller, so a later response is compared with it
    exactly. ``decision`` is the decision in force afterwards (NULL after
    Back to review), with the reviewer's annual pledge and frequency for
    Accepted (edited); ``entered`` is the Entered by hand mark, which
    carries the decision forward. ``marked_pledge`` is ParishSoft's
    upcoming-year pledge read at the mark, when ``marked_pledge_known``;
    nothing reads it before #1704, so every mark records it unknown.
    """

    family = models.ForeignKey(
        "stewardship_campaigns.FamilyCampaign",
        on_delete=models.PROTECT,
        related_name="pledge_reviews",
    )
    submission = models.ForeignKey(
        "stewardship_responses.Submission",
        on_delete=models.PROTECT,
        related_name="pledge_reviews",
    )
    sequence = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    action = models.CharField(max_length=16)
    decision = models.CharField(max_length=16, null=True)
    reviewed_annual_pledge = models.DecimalField(
        max_digits=14, decimal_places=2, null=True
    )
    reviewed_frequency = models.CharField(max_length=12, null=True)
    entered = models.BooleanField()
    answer = models.JSONField()
    marked_pledge = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    marked_pledge_known = models.BooleanField()
    note = models.TextField(default="", blank=True)

    class Meta:
        db_table = "stewardship_pledge_review"
        constraints = [
            models.UniqueConstraint(
                fields=("family", "sequence"), name="pledge_review_sequence"
            ),
            models.UniqueConstraint(
                fields=("actor_id", "request_key"), name="pledge_review_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False, sequence__gte=1),
                name="pledge_review_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=PLEDGE_ACTIONS),
                name="pledge_review_action",
            ),
            models.CheckConstraint(
                condition=models.Q(decision__isnull=True)
                | models.Q(decision__in=PLEDGE_DECISIONS),
                name="pledge_review_decision",
            ),
            # An edited decision carries the reviewer's amount, and only it
            # does; a frequency is one of the Family form's, and a pledge
            # above zero needs one.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        decision="accepted_edited",
                        decision__isnull=False,
                        reviewed_annual_pledge__isnull=False,
                        reviewed_annual_pledge__gte=0,
                        reviewed_annual_pledge__lte=Decimal("999999999.99"),
                    )
                    & (
                        models.Q(
                            reviewed_frequency__isnull=False,
                            reviewed_frequency__in=PLEDGE_FREQUENCIES,
                        )
                        | models.Q(
                            reviewed_frequency__isnull=True,
                            reviewed_annual_pledge=0,
                        )
                    )
                )
                | models.Q(
                    decision__isnull=True,
                    reviewed_annual_pledge__isnull=True,
                    reviewed_frequency__isnull=True,
                )
                | models.Q(
                    decision__in=("accepted", "ignored"),
                    decision__isnull=False,
                    reviewed_annual_pledge__isnull=True,
                    reviewed_frequency__isnull=True,
                ),
                name="pledge_review_value_shape",
            ),
            # The row's state is what its action records: a mark keeps an
            # accepted decision, every other action is not a mark.
            models.CheckConstraint(
                condition=models.Q(
                    action="accept",
                    decision="accepted",
                    decision__isnull=False,
                    entered=False,
                )
                | models.Q(
                    action="edit",
                    decision="accepted_edited",
                    decision__isnull=False,
                    entered=False,
                )
                | models.Q(
                    action="ignore",
                    decision="ignored",
                    decision__isnull=False,
                    entered=False,
                )
                | models.Q(
                    action="back_to_review", decision__isnull=True, entered=False
                )
                | models.Q(
                    action="entered_by_hand",
                    decision__isnull=False,
                    decision__in=("accepted", "accepted_edited"),
                    entered=True,
                ),
                name="pledge_review_action_state",
            ),
            models.CheckConstraint(
                condition=models.Q(marked_pledge_known=True, entered=True)
                | models.Q(marked_pledge_known=False, marked_pledge__isnull=True),
                name="pledge_review_mark_shape",
            ),
        ]


# The outcomes a refresh records for a pledge, as 0136's census and Ministry
# refresh checks name them (``workflows.models.REFRESH_OUTCOMES``).
PLEDGE_REFRESH_OUTCOMES = (
    "confirmed",
    "unchanged",
    "entered_incorrectly",
    "ignored_now_matches",
)


class PledgeRefreshCheck(ImmutableRecord):
    """One refresh's outcome for a decided pledge (#1704).

    A pledge's stage is derived from its ``PledgeReview`` rows, so the
    baseline refresh re-evaluation records what each refresh found here, as
    ``workflows.models.RefreshCheck`` does for census changes and Ministry
    requests: the outcome, ParishSoft's upcoming-year amount (``observed``)
    and whether the refresh was full. ``review`` is the decision row in force
    when it was written and ``item_version`` that row's sequence; a check
    counts only while its row is still the item's latest and in force. Only
    the source worker writes one, inside the promotion of ``snapshot_id``;
    SQL checks the item, the amount and the outcome and keeps the rows
    immutable (schema/migrations/0139_pledge_refresh_checks.sql).
    """

    snapshot_id = models.UUIDField()
    full_refresh = models.BooleanField()
    review = models.ForeignKey(
        PledgeReview, on_delete=models.PROTECT, related_name="refresh_checks"
    )
    item_version = models.PositiveBigIntegerField()
    outcome = models.CharField(max_length=24)
    observed = models.DecimalField(max_digits=24, decimal_places=2)

    class Meta:
        db_table = "stewardship_pledge_refresh_check"
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot_id", "review"), name="pledge_refresh_check_once"
            ),
            models.CheckConstraint(
                condition=models.Q(outcome__in=PLEDGE_REFRESH_OUTCOMES),
                name="pledge_refresh_check_outcome",
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=True, item_version__gte=1),
                name="pledge_refresh_check_identity",
            ),
        ]
