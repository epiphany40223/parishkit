"""Settle observed provider results without crossing the SQL deadline boundary."""

from django.db import IntegrityError, transaction

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .sessions import database_now


def settle_result(outcome, *, deadline, write):
    """Retry a deadline-raced result as unknown, under the caller's existing locks.

    PostgreSQL checks its wall clock independently of the preceding Python read.
    A savepoint lets that final guard reject a late definitive result without
    losing the owned outer transaction. The one retry still passes every SQL
    ownership and state guard; it grants no new authority and never sends again.
    """
    require_work_order()
    if not isinstance(outcome, DeliveryOutcome):
        raise ValueError("A closed delivery outcome is required.")
    if database_now() >= deadline:
        outcome = DeliveryOutcome.UNKNOWN
    try:
        with transaction.atomic():
            return write(outcome)
    except IntegrityError:
        if outcome is DeliveryOutcome.UNKNOWN or database_now() < deadline:
            raise
        return write(DeliveryOutcome.UNKNOWN)
