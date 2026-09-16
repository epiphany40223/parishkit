"""Synthetic mail metadata for actual-template browser acceptance, never sends."""

from uuid import uuid4


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
    )
    refusal = dict(
        id=uuid4(), family_duid=12345, address="head@example.org", created_at=now
    )
    task = dict(id=uuid4(), state="failed")
    yield (
        "/deliveries",
        "deliveries",
        dict(
            deliveries=[message],
            states=[("all", "All"), ("delivery_unknown", "Delivery unknown")],
            selected_state="delivery_unknown",
            query="",
            next_query="page=2",
        ),
    )
    yield (
        "/delivery",
        "delivery",
        dict(
            delivery=message,
            task=task,
            commands=[
                dict(id=uuid4(), action=action, label=label)
                for action, label in (
                    ("note", "Save evidence note"),
                    ("accept", "Confirm delivery using external evidence"),
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
        ),
    )
    yield "/delivery-refusals", "delivery-refusals", dict(refusals=[refusal])
    yield (
        "/delivery-refusal",
        "delivery-refusal",
        dict(
            refusal=refusal,
            source=dict(snapshot_id=uuid4(), generation=2),
            command_id=uuid4(),
        ),
    )
    yield (
        "/delivery-error",
        "delivery-error",
        dict(message="This information changed. Reload before trying again."),
    )
