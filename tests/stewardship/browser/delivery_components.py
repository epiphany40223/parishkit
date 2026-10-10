"""Synthetic mail metadata for actual-template browser acceptance, never sends."""

from datetime import timedelta
from uuid import uuid4

from parishkit.stewardship.jobs.delivery_bulk import BulkResult
from parishkit.stewardship.jobs.delivery_metadata import DELIVERY_SORTING
from parishkit.stewardship.jobs.delivery_views import REFUSAL_SORTING
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import window_table


def components(now):
    """Keep each operational state visible without browser provider credentials."""
    message = dict(
        id=uuid4(),
        family__family_duid=12345,
        # The Family's name beside its DUID (#931), long enough to wrap.
        family_name="Castellanos, Maximiliana and Bartholomew",
        state="delivery_unknown",
        purpose="initial",
        mode="production",
        version=4,
        attempt=1,
        updated_at=now,
        created_at=now,
    )
    refusal = dict(
        id=uuid4(), family_duid=12345, address="head@example.org", created_at=now
    )
    task = dict(id=uuid4(), state="failed")
    yield (
        "/deliveries",
        "deliveries",
        dict(
            table=window_table(
                PageWindow(1, 25),
                [message],
                True,
                carry=[("state", "delivery_unknown")],
                total=(26, False),
                sorting=DELIVERY_SORTING,
                sort=DELIVERY_SORTING.default,
            ),
            states=["all", "delivery_unknown"],
            selected_state="delivery_unknown",
            query="",
        ),
    )
    # Each kind of recipient (#931): a named Family, a Family the latest
    # ParishSoft data no longer has, and an Administrator report.
    yield (
        "/deliveries-names",
        "deliveries",
        dict(
            table=window_table(
                PageWindow(1, 25),
                [
                    message,
                    message
                    | dict(id=uuid4(), family__family_duid=4021, family_name=None),
                    message
                    | dict(
                        id=uuid4(),
                        family__family_duid=None,
                        family_name=None,
                        purpose="daily_digest",
                        state="delivered",
                    ),
                ],
                False,
                total=(3, False),
                sorting=DELIVERY_SORTING,
                sort=DELIVERY_SORTING.default,
            ),
            states=["all", "delivery_unknown"],
            selected_state="all",
            query="",
        ),
    )
    # Family email waiting on a sending limit (#382 M3a): the note above
    # the list, with a Gmail hold's end and the next throttled retry.
    yield (
        "/deliveries-holds",
        "deliveries",
        dict(
            table=window_table(
                PageWindow(1, 25),
                [message | dict(state="retry_wait")],
                False,
                total=(1, False),
                sorting=DELIVERY_SORTING,
                sort=DELIVERY_SORTING.default,
            ),
            states=["all", "retry_wait"],
            selected_state="all",
            query="",
            holds=dict(
                daily_limit=True,
                gmail_held=True,
                gmail_until=now + timedelta(minutes=40),
                throttled=2,
                throttled_due=now + timedelta(minutes=15),
            ),
        ),
    )
    # Outgoing mail's bulk actions (#382 M4): the panel, and the answers its
    # in-place Preview and Confirm get (a test routes each POST to one).
    failed = message | dict(state="permanent_failure")
    bulk = dict(
        table=window_table(
            PageWindow(1, 25),
            [failed],
            False,
            total=(1, False),
            sorting=DELIVERY_SORTING,
            sort=DELIVERY_SORTING.default,
        ),
        states=["all", "permanent_failure"],
        selected_state="all",
        query="",
        bulk_overview={
            "retry_failed": {
                "total": 3,
                "purposes": [("initial", 2), ("receipt", 1)],
            },
            "confirm_unsent": {"total": 1, "purposes": [("reminder", 1)]},
        },
    )
    yield "/deliveries-bulk", "deliveries", bulk
    yield (
        "/deliveries-bulk-review",
        "deliveries",
        bulk
        | dict(
            bulk_review=dict(
                kind="retry_failed",
                purpose="all",
                count=3,
                note="Gmail was down from 2 to 3 PM.",
                token="signed-preview",
                form="bulk-retry_failed",
            )
        ),
    )
    yield (
        "/deliveries-bulk-result",
        "deliveries",
        bulk
        | dict(
            table=window_table(
                PageWindow(1, 25),
                [failed | dict(state="pending")],
                False,
                total=(1, False),
                sorting=DELIVERY_SORTING,
                sort=DELIVERY_SORTING.default,
            ),
            bulk_result=BulkResult(
                kind="retry_failed",
                purpose="all",
                total=250,
                resolved=100,
                newly=100,
                skipped=2,
                remaining=148,
                token="signed-continue",
            ),
            bulk_note="Gmail was down from 2 to 3 PM.",
        ),
    )
    yield (
        "/deliveries-bulk-refused",
        "deliveries",
        bulk
        | dict(
            bulk_errors=[
                dict(
                    message="These emails changed, or the preview expired, "
                    "before you confirmed. Choose Preview again to see what "
                    "qualifies now."
                )
            ]
        ),
    )
    delivery = dict(
        delivery=message,
        task=task,
        commands=[
            dict(id=uuid4(), action=action, label=label)
            for action, label in (
                ("note", "Save evidence note"),
                ("accept", "Confirm delivery using external evidence"),
                (
                    "confirm_unsent",
                    "Record that the provider did not send it (no resend)",
                ),
                ("resend", "Authorize potentially duplicate resend"),
            )
        ],
        notes=[
            dict(
                action="note",
                created_at=now,
                evidence_note="Confirmed <private> evidence",
            )
        ],
        events=[
            dict(
                attempt=1,
                action="mark_unknown",
                state="delivery_unknown",
                created_at=now,
            )
        ],
    )
    yield "/delivery", "delivery", delivery
    # The evidence and attempt history over two pages, as Next page and
    # Previous page fetch them in place (#519 PR 6).
    first = delivery | dict(next_query="page=2")
    yield "/delivery-paged", "delivery", first
    yield "/delivery-paged?page=1", "delivery", first
    yield (
        "/delivery-paged?page=2",
        "delivery",
        delivery
        | dict(
            # As long as the first page, so the reader's place can be kept.
            notes=[
                dict(action="note", created_at=now, evidence_note="Earlier evidence")
            ],
            events=[dict(attempt=1, action="send", state="pending", created_at=now)],
            previous_query="page=1",
        ),
    )
    yield (
        "/delivery-refusals",
        "delivery-refusals",
        dict(
            table=window_table(
                PageWindow(1, 25),
                [refusal],
                False,
                total=(1, False),
                sorting=REFUSAL_SORTING,
                sort=REFUSAL_SORTING.default,
            )
        ),
    )
    yield (
        "/delivery-refusal",
        "delivery-refusal",
        dict(
            refusal=refusal,
            can_clear=True,
            source=dict(snapshot_id=uuid4(), generation=2),
            command_id=uuid4(),
        ),
    )
    yield (
        "/delivery-error",
        "delivery-error",
        dict(message="This information changed. Reload before trying again."),
    )
