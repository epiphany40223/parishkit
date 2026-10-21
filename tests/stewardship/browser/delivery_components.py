"""Synthetic mail metadata for actual-template browser acceptance, never sends.

A refused address's page is also served at its real address (``REFUSAL``),
with a "cleared" view (``CLEARED``), and the fixture server answers its
clearance POST with a real Post/Redirect/Get redirect (``POSTS``), for the
in-place clearance test (#935). A delivered Production email's page is
served at its real address too (``DELIVERED``), with a "reported" view
(``REPORTED``), for the in-place bounce report (#806 slice 4).
"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.urls import reverse

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.jobs.delivery_bulk import BulkResult
from parishkit.stewardship.jobs.delivery_metadata import (
    DELIVERY_SORTING,
    PURPOSE_CHOICES,
    RECIPIENT_PATTERN,
    RECIPIENT_REFUSAL,
    STATES,
)
from parishkit.stewardship.jobs.delivery_reads import (
    email_outcome,
    question_choices,
    with_user_links,
)
from parishkit.stewardship.jobs.delivery_views import (
    FIELD_IDS,
    FIELD_LINES,
    REFUSAL_SORTING,
    outcome_context,
)
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.filter_errors import FilterError, error_context
from parishkit.stewardship.web.tables import window_table

REFUSAL_ID = UUID(int=935)
REFUSAL = reverse("admin:delivery_refusal", args=[REFUSAL_ID])
CLEAR = reverse("admin:delivery_refusal_clear", args=[REFUSAL_ID])
CLEARED = REFUSAL + "?cleared=1"
# The fixture server's answer to Clear verified refusal (status, Location,
# body), as the view answers it.
# A delivered Production email whose bounce an Administrator reports in
# place (#806 slice 4), and the page the report's redirect answers with.
DELIVERED_ID = UUID(int=806)
DELIVERED = reverse("admin:delivery", args=[DELIVERED_ID])
BOUNCE = reverse("admin:delivery_bounce_report", args=[DELIVERED_ID])
REPORTED = DELIVERED + "?reported=1"
POSTS = {CLEAR: (303, CLEARED, ""), BOUNCE: (303, REPORTED, "")}
# Where Sent mail's POST controls post (#1562): the list fixture the
# filter tests open, whose in-place requests they route.
LIST_ACTION = "/deliveries-names"


# A Sent mail page that posts to, and draws, its own address (#1571).
OWN_ACTION = "/deliveries-own"
# The pages' viewer, an Administrator, who may open a user's Timeline
# (#1885), and the users whose addresses the fixtures link to it.
ADMIN = SimpleNamespace(
    principal=Principal(UUID(int=1885), frozenset({"administrator"}))
)
MORGAN_USER = UUID(int=18851)
SPOUSE_USER = UUID(int=18852)
MORGAN_TIMELINE = reverse("admin:user_timeline", args=[MORGAN_USER])
SPOUSE_TIMELINE = reverse("admin:user_timeline", args=[SPOUSE_USER])


def at_own_address(context):
    """``context`` with its table posting to, and its view drawn at,
    OWN_ACTION instead of LIST_ACTION."""
    return context | dict(
        table=replace(context["table"], action=OWN_ACTION),
        page_address=OWN_ACTION + "?size=25&sort=-changed",
    )


def mode_fields(*kept):
    """The fields each Production / Testing segment posts besides its mode,
    as the view draws them (#1575): the applied filters, size and sort."""
    return dict([*kept, ("size", "25"), ("sort", "-changed")])


def sent_table(*args, **kwargs):
    """A Sent mail table: its filters are private POST state (#1562)."""
    return replace(window_table(*args, **kwargs), method="post", action=LIST_ACTION)


# Sent mail's filter bar as the view draws it with no filter applied
# (#1562): every Delivery and Purpose choice, Production shown, the
# Production / Testing choices and the view's linkable address.
LIST_FILTERS = dict(
    purposes=PURPOSE_CHOICES,
    selected_purpose="all",
    recipient="",
    # The Recipient's browser check and its words, as the view passes them.
    recipient_pattern=RECIPIENT_PATTERN,
    recipient_hint=RECIPIENT_REFUSAL,
    start="",
    end="",
    zone="",
    mode="production",
    mode_fields=mode_fields(),
    page_address=LIST_ACTION + "?size=25&sort=-changed",
)


# Every action an uncertain email's page offers when nothing holds it.
UNKNOWN_ACTIONS = ("note", "accept", "confirm_unsent", "resend")


def panel(state, actions, *, task="failed", blocked=None, refusal=None, **extra):
    """The outcome panel's context as the view draws it (#1563): the email
    in ``state`` offering ``actions``, its latest task ``task``, with
    ``blocked`` or a send's ``refusal`` naming why an answer is unavailable;
    ``extra`` overrides read_detail's other fields (refused, paused,
    last_attempt_at)."""
    data = dict(refused=[], paused=False, last_attempt_at=None) | extra
    return outcome_context(
        data
        | dict(
            outcome=email_outcome(state, bool(data["refused"])),
            actions=list(actions),
            choices=question_choices(
                state, task, actions, blocked=blocked, refusal=refusal
            ),
        )
    )


def refused_recipient(context, typed):
    """``context`` drawn again for a Recipient ``typed`` that the server
    refuses at its field (#1562), as the view draws it: the field shows what
    was typed, marked, with the message in the hint row and the summary."""
    error = FilterError("recipient", RECIPIENT_REFUSAL, {"recipient": typed})
    return (
        context
        | dict(recipient=typed)
        | error_context(error, FIELD_IDS, FIELD_LINES)
        | {"filter_sizes": {FIELD_LINES["recipient"]: RECIPIENT_REFUSAL}}
    )


def components(now):
    """Keep each operational state visible without browser provider credentials."""
    message = dict(
        id=uuid4(),
        family_id=uuid4(),
        family__family_duid=12345,
        # Recipient lists everything sent to this Family (#1562). With no
        # timeline_family_id this models a Production Family the current
        # campaign has no record of, so it keeps the in-place filter; the
        # Timeline link is checked in test_sent_mail_postgresql.
        recipient_fields=[("recipient", "12345"), ("mode", "production")],
        # The Family's name beside its DUID (#931), long enough to wrap.
        family_name="Castellanos, Maximiliana and Bartholomew",
        state="delivery_unknown",
        purpose="initial",
        mode="production",
        version=4,
        attempt=1,
        updated_at=now,
        # Sent mail shows only the last change (#931), never this.
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
            table=sent_table(
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
            **LIST_FILTERS,
        ),
    )
    # Each kind of recipient (#931): a named Family, a Family the latest
    # ParishSoft data no longer has, and an Administrator report. The first
    # two emails have unresolved refused addresses (#935), counted in the
    # link line.
    names = dict(
        refusal_count=3,
        table=sent_table(
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
                # An Administrator's report, named by the Users page
                # (#1562), and a notice to an address with no Name.
                message
                | dict(
                    id=uuid4(),
                    family_id=None,
                    family__family_duid=None,
                    family_name=None,
                    purpose="daily_digest",
                    state="delivered",
                    recipient_name="Morgan Administrator",
                    recipient_address="morgan@example.org",
                    # A current user: the name links their Timeline (#1885).
                    recipient_user_id=MORGAN_USER,
                    recipient_fields=[
                        ("recipient", "morgan@example.org"),
                        ("mode", "production"),
                    ],
                ),
                message
                | dict(
                    id=uuid4(),
                    family_id=None,
                    family__family_duid=None,
                    family_name=None,
                    purpose="security_event",
                    state="permanent_failure",
                    recipient_name=None,
                    recipient_address="pat@example.org",
                    recipient_fields=[
                        ("recipient", "pat@example.org"),
                        ("mode", "production"),
                    ],
                ),
            ],
            False,
            total=(4, False),
            sorting=DELIVERY_SORTING,
            sort=DELIVERY_SORTING.default,
        ),
        states=STATES,
        selected_state="all",
        request=ADMIN,
        **LIST_FILTERS,
    )
    yield ("/deliveries-names", "deliveries", names)
    # The same page drawn again for a Recipient the server refuses at its
    # field (#1562): a DUID out of range, which the field's pattern lets
    # through.
    yield ("/deliveries-refused", "deliveries", refused_recipient(names, "0"))
    # Sent mail's filters and its Production / Testing links acting in
    # place (#1562): the answers a test routes the Testing link and an
    # applied filter to.
    yield (
        "/deliveries-testing",
        "deliveries",
        dict(
            table=sent_table(
                PageWindow(1, 25),
                [
                    message
                    | dict(
                        mode="testing",
                        recipient_fields=[("recipient", "12345"), ("mode", "testing")],
                    )
                ],
                False,
                total=(1, False),
                sorting=DELIVERY_SORTING,
                sort=DELIVERY_SORTING.default,
            ),
            states=STATES,
            selected_state="all",
            **LIST_FILTERS
            | dict(
                mode="testing",
                mode_fields=mode_fields(),
                page_address=LIST_ACTION + "?mode=testing&size=25&sort=-changed",
            ),
        ),
    )
    yield (
        "/deliveries-applied",
        "deliveries",
        dict(
            table=sent_table(
                PageWindow(1, 25),
                [message | dict(state="permanent_failure")],
                False,
                carry=[("recipient", "12345"), ("state", "needs_attention")],
                total=(1, False),
                sorting=DELIVERY_SORTING,
                sort=DELIVERY_SORTING.default,
            ),
            states=STATES,
            selected_state="needs_attention",
            **LIST_FILTERS
            | dict(
                recipient="12345",
                mode_fields=mode_fields(
                    ("recipient", "12345"),
                    ("state", "needs_attention"),
                ),
                page_address=LIST_ACTION
                + "?state=needs_attention&size=25&sort=-changed",
            ),
        ),
    )
    # Family email waiting on a sending limit (#382 M3a): the note above
    # the list, with a Gmail hold's end and the next throttled retry.
    yield (
        "/deliveries-holds",
        "deliveries",
        dict(
            table=sent_table(
                PageWindow(1, 25),
                [message | dict(state="retry_wait")],
                False,
                total=(1, False),
                sorting=DELIVERY_SORTING,
                sort=DELIVERY_SORTING.default,
            ),
            states=["all", "retry_wait"],
            selected_state="all",
            **LIST_FILTERS,
            holds=dict(
                daily_limit=True,
                gmail_held=True,
                gmail_until=now + timedelta(minutes=40),
                throttled=2,
                throttled_due=now + timedelta(minutes=15),
            ),
        ),
    )
    # Sent mail's bulk actions (#382 M4): the panel, and the answers its
    # in-place Preview and Confirm get (a test routes each POST to one).
    failed = message | dict(state="permanent_failure")
    bulk = dict(
        table=sent_table(
            PageWindow(1, 25),
            [failed],
            False,
            total=(1, False),
            sorting=DELIVERY_SORTING,
            sort=DELIVERY_SORTING.default,
        ),
        states=["all", "permanent_failure"],
        selected_state="all",
        **LIST_FILTERS,
        bulk_overview={
            "retry_failed": {
                "total": 3,
                "purposes": [("initial", 2), ("receipt", 1)],
            },
            "confirm_unsent": {"total": 1, "purposes": [("reminder", 1)]},
        },
    )
    review = bulk | dict(
        bulk_review=dict(
            kind="retry_failed",
            purpose="all",
            count=3,
            note="Gmail was down from 2 to 3 PM.",
            token="signed-preview",
            form="bulk-retry_failed",
        )
    )
    result = bulk | dict(
        table=sent_table(
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
    )
    yield "/deliveries-bulk", "deliveries", bulk
    yield "/deliveries-bulk-review", "deliveries", review
    yield "/deliveries-bulk-result", "deliveries", result
    # The same three pages served at an address that is also where they
    # post and the address they draw (OWN_ACTION), as the real Sent mail
    # page is: Preview's and Confirm's answers then set the address, so a
    # test sees what history keeps of them (#1571).
    for suffix, context in (("", bulk), ("-review", review), ("-result", result)):
        yield OWN_ACTION + suffix, "deliveries", at_own_address(context)
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
        # The second address is a current user's, so it links that user's
        # Timeline (#1885); the page's viewer may open it.
        recipients=with_user_links(
            dict(
                intended=["head@example.org", "spouse@example.org"],
                routed=["testing@example.org"],
                intended_rows=[
                    ("head@example.org", None),
                    ("spouse@example.org", None),
                ],
                routed_rows=[("testing@example.org", "not sure it arrived")],
                reached=None,
                refused_for_now=False,
            ),
            {"spouse@example.org": (SPOUSE_USER, "Spouse")},
        ),
        request=ADMIN,
        # Not sure it arrived, with every answer allowed (#1563).
        **panel("delivery_unknown", UNKNOWN_ACTIONS, last_attempt_at=now),
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
    # No Admin evidence saved yet (#227 empty state).
    yield "/delivery-no-evidence", "delivery", delivery | dict(notes=[])
    # Other campaign work holds the email: its decisions stay in view,
    # unavailable, named by the notice that says why (#1331).
    yield (
        "/delivery-held",
        "delivery",
        delivery | panel("delivery_unknown", (), blocked="held"),
    )
    # A delivered email offers only the evidence note: no decision, so the
    # note form waits closed in a disclosure (#1015).
    yield (
        "/delivery-note-only",
        "delivery",
        delivery
        | dict(
            delivery=page_message | dict(state="delivered", finished_at=now),
            refused=[],
            **panel("delivered", ("note",)),
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
            delivery=page_message
            | dict(state="delivered", mode="production", finished_at=now),
            refused=[],
            **panel("delivered", ("note",)),
            recipients=with_user_links(
                dict(
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
                {},
            ),
        ),
    )
    # A delivered Production email: Report a bounced address offers both
    # addresses (#806 slice 4), at its real address so the form posts in
    # place; after the report, the panel lists it and offers the other.
    bounce_page = delivery | dict(
        delivery=page_message
        | dict(id=DELIVERED_ID, state="delivered", finished_at=now),
        refused=[],
        **panel("delivered", ("note",)),
        recipients=with_user_links(
            dict(
                intended=production,
                routed=production,
                intended_rows=[(address, "delivered") for address in production],
                routed_rows=[(address, "delivered") for address in production],
                reached=None,
                refused_for_now=False,
            ),
            {},
        ),
        # Long enough history that the panel sits below the fold.
        events=[dict(attempt=1, action="accept", state="delivered", created_at=now)]
        * 12,
    )
    bounce = dict(
        addresses=production,
        unavailable=None,
        reported=[],
        command_id=uuid4(),
        last=None,
    )
    # The Family's only address left is one of those offered, so a notice
    # names it before the button.
    yield (
        DELIVERED,
        "delivery",
        bounce_page | dict(bounce=bounce | dict(last="head@example.org")),
    )
    yield (
        REPORTED,
        "delivery",
        bounce_page
        | dict(
            bounce=bounce
            | dict(
                addresses=["spouse@example.org"],
                reported=[
                    dict(
                        id=REFUSAL_ID,
                        address="head@example.org",
                        created_at=now,
                        cleared=False,
                    )
                ],
            )
        ),
    )
    # Not delivered yet: the panel says why Report bounce is unavailable.
    yield (
        "/delivery-bounce-unavailable",
        "delivery",
        delivery
        | dict(
            bounce=bounce
            | dict(
                unavailable="Available once this email is delivered: only a "
                "delivered email can bounce."
            )
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
    # The outcome panel's other states (#1563), each a Production email.
    production_email = page_message | dict(mode="production")
    head = [dict(id=REFUSAL_ID, address="head@example.org", kind="provider")]
    for path, state, actions, extra in (
        # Failed, with a refused address; Send it again offered.
        ("/delivery-failed", "permanent_failure", ("note", "retry_failed"), {}),
        # Failed, and the Family has responded, so it is not sent again.
        (
            "/delivery-failed-responded",
            "permanent_failure",
            ("note",),
            dict(refusal="family_responded"),
        ),
        # Not sure it arrived after the campaign ended: no resend.
        (
            "/delivery-unknown-ended",
            "delivery_unknown",
            ("note", "accept", "confirm_unsent"),
            dict(refusal="campaign_ended"),
        ),
        # Not sure it arrived while mail is paused: a resend waits.
        ("/delivery-unknown-paused", "delivery_unknown", UNKNOWN_ACTIONS, {}),
        # Waiting to retry after its task failed: Try again now.
        ("/delivery-sending", "retry_wait", ("note", "retry_unsent"), {}),
        # Waiting to retry on its own: the next attempt's time.
        ("/delivery-waiting", "retry_wait", ("note",), dict(task="running")),
        # Queued while its task still runs: nothing to answer.
        ("/delivery-queued", "pending", ("note",), dict(task="running")),
        ("/delivery-cancelled", "cancelled", ("note",), {}),
        # Delivered, but one address was refused: check and clear it.
        ("/delivery-delivered-refused", "delivered", ("note",), {}),
    ):
        refused = head if state in {"permanent_failure", "delivered"} else []
        if path == "/delivery-failed-responded":
            refused = []
        paused = path in {"/delivery-unknown-paused", "/delivery-queued"}
        yield (
            path,
            "delivery",
            delivery
            | dict(
                delivery=production_email
                | dict(
                    state=state,
                    finished_at=now,
                    not_before=now + timedelta(minutes=10),
                ),
                refused=refused,
                recipients=with_user_links(
                    dict(
                        intended=production,
                        routed=production,
                        intended_rows=[(address, None) for address in production],
                        routed_rows=[(address, None) for address in production],
                        reached=None,
                        refused_for_now=False,
                    ),
                    {},
                ),
            )
            | panel(
                state,
                actions,
                refused=refused,
                paused=paused,
                last_attempt_at=now,
                **extra,
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
    # Other campaign work holds the clearance: Clear is unavailable, named by
    # that reason (#1331).
    yield (
        "/delivery-refusal-busy",
        "delivery-refusal",
        detail | dict(can_clear=False, clear_blocked="busy"),
    )
    # A reported bounce (#806 slice 4): marked in the list, and its page
    # names the report, its time and the Administrator's note.
    reported = refusal | dict(
        kind="reported_bounce",
        evidence_note="Bounce arrived 10/12: mailbox not found.",
    )
    yield (
        "/delivery-refusals-bounce",
        "delivery-refusals",
        dict(
            table=window_table(
                PageWindow(1, 25),
                [reported],
                False,
                total=(1, False),
                sorting=REFUSAL_SORTING,
                sort=REFUSAL_SORTING.default,
            )
        ),
    )
    yield (
        "/delivery-refusal-bounce",
        "delivery-refusal",
        detail | dict(refusal=reported, family_name=refusal["family_name"]),
    )
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
