"""Record every count a refused ParishSoft load was checked on (ADM-13, #530)."""

from .drop_models import SourceDropCount


def record_drop_counts(attempt_id, checks, *, actor_id):
    """Write one row per ``loading.CountCheck`` for a rejected attempt.

    Runs inside the caller's refusal transaction
    (``failures.settle_failed_read``), after the attempt's snapshot was
    rejected; the SQL guard
    refuses anything else, and refuses a ``failed`` flag that does not
    follow the loss rule.
    """
    SourceDropCount.objects.bulk_create(
        SourceDropCount(
            attempt_id=attempt_id,
            actor_id=actor_id,
            measure=check.measure,
            before=check.before,
            after=check.after,
            limit_percent=check.limit_percent,
            failed=check.failed,
        )
        for check in checks
    )
