"""Synthetic mail metadata for actual-template browser acceptance, never sends.

A refused address's page is also served at its real address (``REFUSAL``),
with a "cleared" view (``CLEARED``), and the fixture server answers its
clearance POST with a real Post/Redirect/Get redirect (``POSTS``), for the
in-place clearance test (#935).
"""

from datetime import timedelta
from uuid import UUID, uuid4

from django.urls import reverse

from parishkit.stewardship.jobs.delivery_bulk import BulkResult
from parishkit.stewardship.jobs.delivery_metadata import DELIVERY_SORTING
from parishkit.stewardship.jobs.delivery_views import REFUSAL_SORTING
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import window_table

REFUSAL_ID = UUID(int=935)
REFUSAL = reverse("admin:delivery_refusal", args=[REFUSAL_ID])
CLEAR = reverse("admin:delivery_refusal_clear", args=[REFUSAL_ID])
CLEARED = REFUSAL + "?cleared=1"
# The fixture server's answer to Clear verified refusal (status, Location,
# body), as the view answers it.
POSTS = {CLEAR: (303, CLEARED, "")}


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
        # Outgoing mail shows only the last change (#931), never this.
        created_at=now - timedelta(hours=3),
    )
    refusal = dict(
        id=REFUSAL_ID,
        family_duid=12345,
        address="head@example.org",
        created_at=now,
        # The Family's name and the email that recorded it (#935).
        family_name="Castellanos, Maximiliana and Bartholomew",
        message_id=message["id"],
    )
    # An email's own page has an About control beside its heading, which
    # must fit on one line, so its Family's name is shorter than the list's.
    page_message = message | dict(family_name="Castellanos, Maximiliana")
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
    # ParishSoft data no longer has, and an Administrator report. The first
    # two emails have unresolved refused addresses (#935), counted in the
    # link line.
    yield (
        "/deliveries-names",
        "deliveries",
        dict(
            refusal_count=3,
            table=window_table(
                PageWindow(1, 25),
                [
                    message | dict(refused=2),
                    message
                    | dict(
                        id=uuid4(),
                        family__family_duid=4021,
                        family_name=None,
                        refused=1,
                    ),
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
        delivery=page_message,
        task=task,
        # The refused address its delivery recorded, linking its own page
        # (#1015).
        refused=[dict(id=REFUSAL_ID, address="head@example.org")],
        # Whom it is for, read through the definer function (#1024): a
        # Testing-mode email, routed to the Testing recipient instead, whose
        # latest attempt's outcome follows it (#806).
        recipients=dict(
            intended=["head@example.org", "spouse@example.org"],
            routed=["testing@example.org"],
            intended_rows=[("head@example.org", None), ("spouse@example.org", None)],
            routed_rows=[("testing@example.org", "not sure it arrived")],
            reached=None,
            refused_for_now=False,
        ),
        # A decision is offered, so the "Resolve this email" section shows.
        decisions=True,
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
    # A delivered email offers only the evidence note: no decision, so the
    # note form waits closed in a disclosure (#1015).
    yield (
        "/delivery-note-only",
        "delivery",
        delivery
        | dict(
            delivery=page_message | dict(state="delivered"),
            refused=[],
            decisions=False,
            commands=[dict(id=uuid4(), action="note", label="Save evidence note")],
        ),
    )
    # A Production email accepted for one address and refused for now by
    # the other: each To: address is followed by its outcome, and a line
    # says how many it reached (#806).
    production = ["head@example.org", "spouse@example.org"]
    yield (
        "/delivery-partial",
        "delivery",
        delivery
        | dict(
            delivery=page_message | dict(state="delivered", mode="production"),
            refused=[],
            decisions=False,
            commands=[dict(id=uuid4(), action="note", label="Save evidence note")],
            recipients=dict(
                intended=production,
                routed=production,
                intended_rows=[
                    ("head@example.org", "delivered"),
                    ("spouse@example.org", "refused for now"),
                ],
                routed_rows=[
                    ("head@example.org", "delivered"),
                    ("spouse@example.org", "refused for now"),
                ],
                reached=(1, 2),
                refused_for_now=True,
            ),
        ),
    )
    # A message retried after a failure: each recorded change is one short
    # row of the attempt history table, newest first (#934).
    yield (
        "/delivery-attempts",
        "delivery",
        delivery
        | dict(
            events=[
                dict(
                    attempt=attempt,
                    action=action,
                    state=state,
                    created_at=now - timedelta(minutes=minutes),
                )
                for attempt, action, state, minutes in (
                    (2, "mark_unknown", "delivery_unknown", 0),
                    (2, "submit", "submitting", 5),
                    (1, "retry_unaccepted", "retry_wait", 30),
                    (1, "submit", "submitting", 35),
                )
            ]
        ),
    )
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
            events=[dict(attempt=1, action="created", state="pending", created_at=now)],
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
    # A refused address's page, under a Family the latest data no longer
    # has (#935); at its real address, before and after clearance.
    detail = dict(
        refusal=refusal,
        message_id=message["id"],
        can_clear=True,
        source=dict(snapshot_id=uuid4(), generation=2),
        command_id=uuid4(),
    )
    yield "/delivery-refusal", "delivery-refusal", detail
    named = detail | dict(family_name=refusal["family_name"])
    yield REFUSAL, "delivery-refusal", named
    yield (
        CLEARED,
        "delivery-refusal",
        named
        | dict(
            resolved=dict(
                reason="verified_admin",
                evidence_note="Called the Family; the mailbox works again.",
                created_at=now,
            )
        ),
    )
    yield (
        "/delivery-error",
        "delivery-error",
        dict(message="This information changed. Reload before trying again."),
    )
