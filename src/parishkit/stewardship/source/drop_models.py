"""Every count a refused ParishSoft load was checked on (ADM-13, #530).

A full refresh is refused when a record or eligibility count fell too far
(``loading.check_source_counts``); a quick update that falls too far falls
back to a full refresh instead, so only full refreshes are recorded. The
refused attempt then records every count the check compared, failing or not,
with its before value, after value and the limit in effect, in the same
transaction that rejects the attempt. The System health page shows them,
and the one-time acceptance of ADM-13 PR 5 binds to them, so they come from
this durable record rather than the worker's log. These are counts only,
never parish data, so a row can be read and kept like any other operational
evidence; rows are never changed or deleted (the guards are in
``schema/migrations/0007_system_health_records.sql``).
"""

import uuid

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import (
    ImmutableQuerySet,
    ImmutableRecord,
    UTCDateTimeField,
)

from .count_names import DERIVED_COUNTS, TREND_COLLECTIONS
from .refresh_models import SourceRefreshAttempt

# The closed names of every count a load is checked on, in check order.
DROP_MEASURES = (*TREND_COLLECTIONS, *DERIVED_COUNTS)


class SourceDropCount(ImmutableRecord):
    """One count a refused load was checked on, and whether it fell too far.

    ``before`` is the baseline the check compared with (``None`` when there
    was none, before the first promoted load); ``after`` is the refused
    load's value; ``limit_percent`` is the loss allowed (the default 25 percent, or the
    operator's override). The insert guard recomputes
    ``failed`` from those three, so it cannot disagree with the rule.
    """

    attempt = models.ForeignKey(
        SourceRefreshAttempt, on_delete=models.PROTECT, related_name="drop_counts"
    )
    measure = models.CharField(max_length=32)
    before = models.PositiveBigIntegerField(null=True, blank=True)
    after = models.PositiveBigIntegerField()
    limit_percent = models.PositiveSmallIntegerField()
    failed = models.BooleanField()

    class Meta:
        db_table = "stewardship_source_drop_count"
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "measure"], name="source_drop_count_identity"
            ),
            models.CheckConstraint(
                condition=models.Q(measure__in=DROP_MEASURES),
                name="source_drop_count_measure",
            ),
            models.CheckConstraint(
                condition=models.Q(limit_percent__lte=100),
                name="source_drop_count_limit",
            ),
            # Only a count that had a nonzero baseline under a limit below
            # 100 percent can fail; the insert guard checks the exact rule.
            models.CheckConstraint(
                condition=models.Q(failed=False)
                | models.Q(before__gt=0, limit_percent__lt=100),
                name="source_drop_count_failure",
            ),
        ]


class SourceDropAcceptance(models.Model):
    """An Administrator's one-time acceptance of one refused large change (#530).

    It names the refused attempt (one acceptance per refusal, ever) and copies
    every count recorded with it, as reviewed: ``counts`` maps each measure to
    ``{"before": …, "after": …, "failed": …}``. ``session_id`` and
    ``authenticated_at`` are the Administrator's session and its fresh
    sign-in instant. The guard in
    ``schema/migrations/0080_drop_acceptance.sql`` admits a row only from the
    web login, for a freshly signed-in Administrator, for the newest refusal
    with no full refresh promoted since, with counts equal to the recorded
    ones, while no other acceptance is live; it sets ``created_at`` and
    ``expires_at`` (24 hours later) from the database clock, and refuses every
    update and delete. Whether an acceptance is still live is derived, never
    stored (``stewardship_source_drop_acceptance_active_v1()``); see
    ``source.drop_acceptance`` for how the worker honors it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = UTCDateTimeField(db_default=Now())
    actor_id = models.UUIDField()
    session_id = models.UUIDField()
    authenticated_at = UTCDateTimeField()
    attempt = models.OneToOneField(
        SourceRefreshAttempt, on_delete=models.PROTECT, related_name="drop_acceptance"
    )
    counts = models.JSONField()
    # The guard always sets it, 24 hours after ``created_at``; the database
    # default only lets an insert omit it and read the real value back.
    expires_at = UTCDateTimeField(db_default=Now())

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        db_table = "stewardship_source_drop_acceptance"
        indexes = [
            models.Index(
                models.F("created_at").desc(), name="source_drop_acceptance_newest"
            )
        ]
