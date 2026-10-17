"""A Campaign setup step an Administrator kept as is (#1404).

The Campaign setup stepper (``campaign_wizard``) reads most of its
completion from what was applied. A step whose defaults are acceptable has
nothing to apply, so pressing its **Continue to <next step>** records it here
instead: one immutable row per campaign, step and (for a later per-item mark
on Pages and emails) item. SQL admits the row only from a current
Administrator, for the current campaign while it is a Testing draft with no
go-live started, and only with its ``campaign_setup_step_reviewed`` audit
event (schema/migrations/0105_campaign_setup_review.sql).
"""

from uuid import UUID

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord

# The steps a Continue may record: those completed by a page's own change.
# Load ParishSoft data, Preview and test email and Go-live readiness have a
# check of their own instead (Families loaded, a test accepted, going live).
REVIEW_STEPS = ("settings", "share_options", "content", "schedules", "images")
# A Pages and emails item key: one page or email slot, such as page:welcome.
ITEM_PATTERN = r"^(page|email):[a-z][a-z0-9_]{0,47}$"


def item_key(kind, slot, record_id=None):
    """The ``item`` key of one Pages and emails item (#1404 PR 6b).

    ``kind`` and ``slot`` name the page or mail type; ``record_id`` is the
    saved revision the list shows (None for an empty page, or for the
    confirmation email listed from a retired closing note alone). The
    revision's id without hyphens is part of the key, so a Kept as is mark
    belongs to the exact text that was reviewed: one email of a mail type
    never marks the others, and a later save (a new revision) is not marked.
    Every slot fits ``ITEM_PATTERN`` (test_campaign_wizard checks them all).
    """
    key = f"{kind}:{slot}"
    return f"{key}_{UUID(str(record_id)).hex}" if record_id else key


class CampaignSetupReview(ImmutableRecord):
    """One Campaign setup step (or Pages and emails item) kept as is.

    ``item`` is empty for the whole step. Rows are insert-only: a reviewed
    step stays completed after later changes elsewhere (#1404 decision 8),
    and they are retained with their campaign, like its other setup
    evidence, until a campaign purge exists to inventory them.
    """

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, related_name="+"
    )
    step = models.CharField(max_length=16)
    item = models.CharField(max_length=64, default="", blank=True)

    class Meta:
        db_table = "stewardship_campaign_setup_review"
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "step", "item"), name="campaign_setup_review_once"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False),
                name="campaign_setup_review_actor",
            ),
            models.CheckConstraint(
                condition=models.Q(step__in=REVIEW_STEPS),
                name="campaign_setup_review_step",
            ),
            models.CheckConstraint(
                condition=models.Q(item="")
                | models.Q(step="content", item__regex=ITEM_PATTERN),
                name="campaign_setup_review_item",
            ),
        ]
