"""The Mail queue's counts: every email still waiting to go out (#1562).

An email is queued while its outbox message is not settled: waiting for its
turn (``pending``), being handed to the mail service (``submitting``) or
waiting to retry (``retry_wait``), the states of Sent mail's Still sending
filter. A queued message that carries a pause hold is counted as Paused
instead of Queued or Waiting to retry; workers hold messages only as they
reach them, so while live delivery is paused some unheld messages still
count as Queued.

The counts cover every kind Sent mail lists (``LISTED_PURPOSES``), the
Ministry leader emails (#1592) among them, in that order, so a kind added
to Sent mail appears here too. They come from one grouped count of the
unsettled messages, which the ``outbox_due (state, not_before)`` index
finds without reading the settled majority of the outbox; every column
read is one the web login already reads. Callers run it inside
``read_transaction`` (no lock), so watching the queue never slows a send.
"""

from dataclasses import dataclass, field

from django.db.models import Count, Q

from .delivery_metadata import LISTED_PURPOSES
from .delivery_states import DeliveryState
from .outbox_models import OutboxMessage

QUEUED_STATES = (
    DeliveryState.PENDING.value,
    DeliveryState.SUBMITTING.value,
    DeliveryState.RETRY_WAIT.value,
)
# The four headline figures, in the order the page draws them.
COLUMNS = ("queued", "sending", "retry_wait", "paused")
# The kinds the queue counts, one table row each, in Sent mail's order. A
# purpose kept only so past emails stay listed (the digests, once #1786
# replaces them) is left out here, so it draws no row. Every kind here is
# one Sent mail's Purpose filter accepts, so each row may link it.
QUEUE_PURPOSES = LISTED_PURPOSES


@dataclass(frozen=True)
class KindCounts:
    """One kind's queued emails, split as the Mail queue's columns are."""

    purpose: str
    queued: int = 0
    sending: int = 0
    retry_wait: int = 0
    paused: int = 0

    @property
    def linked(self):
        """Whether the Kind links Sent mail: only while some email is queued."""
        return bool(self.total)

    @property
    def counts(self):
        """The four counts in the page's column order (``COLUMNS``)."""
        return tuple(getattr(self, name) for name in COLUMNS)

    @property
    def total(self):
        """Every queued email of this kind, whatever its column."""
        return sum(self.counts)


@dataclass(frozen=True)
class QueueCounts:
    """The operating mode's queue by kind, and the other mode's total."""

    mode: str
    kinds: tuple = field(default_factory=tuple)
    other_mode_queued: int = 0

    def column(self, name):
        """One headline figure: the column ``name`` summed over every kind."""
        return sum(getattr(kind, name) for kind in self.kinds)

    @property
    def total(self):
        """Every queued email of the operating mode."""
        return sum(kind.total for kind in self.kinds)


def _column(state, held):
    """The Mail queue column a message in ``state`` belongs to.

    A held message is Paused whatever its state; a message being handed to
    the mail service is never held (the pause holds only what has not
    started).
    """
    if state == DeliveryState.SUBMITTING.value:
        return "sending"
    if held:
        return "paused"
    return "queued" if state == DeliveryState.PENDING.value else "retry_wait"


def queued_rows():
    """The grouped count of unsettled messages, by mode, purpose and state.

    Each row also counts how many of its messages carry a pause hold. The
    state filter lets ``outbox_due (state, not_before)`` find the few
    unsettled messages without reading the settled rest.
    """
    return (
        OutboxMessage.objects.filter(purpose__in=QUEUE_PURPOSES)
        .filter(state__in=QUEUED_STATES)
        .values("mode", "purpose", "state")
        .annotate(
            held=Count("id", filter=Q(pause_hold_id__isnull=False)),
            count=Count("id"),
        )
        .order_by()
    )


def read_queue(mode):
    """Count the queued emails of every listed kind, in ``mode`` and the other.

    Returns a ``QueueCounts`` whose ``kinds`` holds one ``KindCounts`` per
    ``QUEUE_PURPOSES`` entry, in that order, zeros included, so the page
    always draws the same rows. One grouped query; run it in the caller's
    read snapshot.
    """
    rows = queued_rows()
    tallies = {purpose: dict.fromkeys(COLUMNS, 0) for purpose in QUEUE_PURPOSES}
    other = 0
    for row in rows:
        if row["mode"] != mode:
            other += row["count"]
            continue
        tally = tallies[row["purpose"]]
        held = row["held"]
        tally[_column(row["state"], True)] += held
        tally[_column(row["state"], False)] += row["count"] - held
    return QueueCounts(
        mode=mode,
        kinds=tuple(
            KindCounts(purpose, **tallies[purpose]) for purpose in QUEUE_PURPOSES
        ),
        other_mode_queued=other,
    )
