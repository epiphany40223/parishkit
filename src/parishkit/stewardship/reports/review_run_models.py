"""The review wizard's runs (#2123; review pipeline spec, "Wizard run storage").

A run is one reader's pass through one stream's waiting work, one subject
(a Family or a Member) per page: its stream, its closed filters, its sort,
the sequence of subjects fixed when it started (each with the ids of the
items that matched) and the reader's position in it. A reader may have
several runs at once, one per browser tab. Runs have no time limit: an
unfinished run stays until it is finished or discarded, and a finished one
keeps its row (it is never offered again) until its user or campaign is
removed, which deletes it by the model's cascade (``on_delete``).

The row holds record ids and DUIDs only, never a name, a value or the
search text (only whether a search was used), so it holds nothing the
stream tables do not already hold. The table and its guard are frozen file
``schema/migrations/0178_review_run.sql``: once a run has started, its
user, campaign, stream, filters, sort and sequence never change, and its
position stays inside the sequence.
"""

from uuid import uuid4

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import UTCDateTimeField

# The streams a run may follow, in the Review work menu's order
# (``review_streams.STREAMS`` holds their words and reads).
STREAMS = ("census", "ministry", "pledges", "information", "limitations")
# A run's sort: Name A–Z, Oldest submitted first, Newest submitted first.
SORTS = ("name", "oldest", "newest")
# Most subjects one run holds (the spec's 5,000 pages).
MAX_SUBJECTS = 5000


class ReviewRun(models.Model):
    """One review wizard run; see the module docstring.

    ``subjects`` is a JSON array, one entry per page in order:
    ``{"family": <Family campaign record id>, "kind": "family" | "member" |
    "proposed_member", "key": <Member DUID or local id, "" for the Family>,
    "items": [<item id>, ...]}``. ``position`` is the current page, from 1.
    ``filters`` holds the closed filter values and the mode (``"basic"`` or
    ``"advanced"``); a search is recorded only as ``"search_used"``.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(
        "stewardship_accounts.PortalUser", on_delete=models.CASCADE, related_name="+"
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.CASCADE, related_name="+"
    )
    stream = models.CharField(max_length=16)
    filters = models.JSONField(default=dict)
    sort = models.CharField(max_length=8)
    subjects = models.JSONField(default=list)
    position = models.PositiveIntegerField(default=1)
    finished_at = UTCDateTimeField(null=True, blank=True)
    created_at = UTCDateTimeField(db_default=Now(), editable=False)
    updated_at = UTCDateTimeField(db_default=Now())

    class Meta:
        db_table = "stewardship_review_run"
        indexes = [
            models.Index(fields=("user", "campaign"), name="review_run_reader"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stream__in=STREAMS), name="review_run_stream"
            ),
            models.CheckConstraint(
                condition=models.Q(sort__in=SORTS), name="review_run_sort"
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=1), name="review_run_position"
            ),
        ]

    @property
    def size(self):
        """How many pages the run has."""
        return len(self.subjects)
