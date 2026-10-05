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

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord

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
