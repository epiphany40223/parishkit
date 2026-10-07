"""Synthetic mail metadata for actual-template browser acceptance, never sends."""

from uuid import uuid4

from parishkit.stewardship.jobs.delivery_metadata import DELIVERY_SORTING
from parishkit.stewardship.jobs.delivery_views import REFUSAL_SORTING
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import window_table


def components(now):
    """Keep each operational state visible without browser provider credentials."""
    message = dict(
        id=uuid4(),
        family__family_duid=12345,
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
