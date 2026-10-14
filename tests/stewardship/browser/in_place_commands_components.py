"""Export status commands and Mail message resolutions acting in place (#519
PR 7c-4).

Each page is served at its real address, and the fixture server answers each
command's POST as its view does: a Post/Redirect/Get redirect back to the
same page (``posts()``), which a route cannot fulfil on every engine. The
redirect lands on a stand-in for the page once the command has run, at the
same path with a query, so the answer is still this page to ui-v1.js. One
Retry and one resolution are refused instead, with the fixed error page each
view answers a conflict or a stale version with (409).
"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.template.loader import render_to_string
from django.urls import reverse

from .delivery_components import components as delivery_components


def _export(number):
    """The status page address of export ``number`` and its two commands'."""
    job = UUID(int=number)
    return (
        reverse("admin:report_export", args=[job]),
        reverse("admin:report_export_retry", args=[job]),
        reverse("admin:report_export_cancel", args=[job]),
    )


# A failed report export (Retry), a queued one (Cancel), the same two for a
# latest-data export, and a failed report export whose Retry is refused.
EXPORT_FAILED, EXPORT_RETRY, _ = _export(5191)
EXPORT_QUEUED, _, EXPORT_CANCEL = _export(5192)
EXACT_FAILED, EXACT_RETRY, _ = _export(5193)
EXACT_QUEUED, _, EXACT_CANCEL = _export(5194)
EXPORT_REFUSED, EXPORT_REFUSED_RETRY, _ = _export(5195)

# The four resolutions of an email whose arrival is uncertain, each on its
# own email so each POST has its own answer; a note on a delivered email,
# whose note form waits in a closed disclosure; and a note refused because
# the email changed meanwhile (a stale version).
RESOLUTIONS = ("note", "accept", "confirm_unsent", "resend")
DELIVERY = {
    action: reverse("admin:delivery", args=[UUID(int=5200 + index)])
    for index, action in enumerate(RESOLUTIONS + ("note_only", "stale"))
}
RESOLVE = {
    action: reverse("admin:delivery_resolve", args=[UUID(int=5200 + index)])
    for index, action in enumerate(RESOLUTIONS + ("note_only", "stale"))
}
# What each resolution's answer page records as the newest evidence.
SAVED_NOTE = "Checked the Sent folder: it went out."


def _stand_in(address, what):
    """The stand-in address a command's redirect lands on (see above)."""
    return f"{address}?{what}=1"


# The redirects; posts() adds the two refusals.
REDIRECTS = {
    EXPORT_RETRY: (303, _stand_in(EXPORT_FAILED, "retried"), ""),
    EXPORT_CANCEL: (303, _stand_in(EXPORT_QUEUED, "cancelled"), ""),
    EXACT_RETRY: (303, _stand_in(EXACT_FAILED, "retried"), ""),
    EXACT_CANCEL: (303, _stand_in(EXACT_QUEUED, "cancelled"), ""),
} | {
    RESOLVE[action]: (303, _stand_in(DELIVERY[action], "resolved"), "")
    for action in RESOLUTIONS + ("note_only",)
}


def components(context, admin):
    """Each page as first shown and as its command's answer shows it."""
    now = context["server_now"]
    page = context | {"admin_chrome": admin}

    def export(number, state, exact=False, **extra):
        """Export ``number``'s status page in ``state``, as the view draws it
        (a latest-data export when ``exact``)."""
        job = SimpleNamespace(
            pk=UUID(int=number),
            format="xlsx",
            requester_id=UUID(int=21),
            parameters={"population_scope": "current"},
            population_scope="current",
            fact_set=SimpleNamespace(source_generation=3, submission_watermark=4),
            submission_watermark=4,
            through_date=now.date(),
            timezone_configuration=SimpleNamespace(timezone="America/New_York"),
            created_at=now.isoformat(),
            browser_timezone="America/Los_Angeles",
        )
        return render_to_string(
            f"stewardship/report-{'exact' if exact else 'export'}.html",
            page
            | {
                "job": job,
                "status": {"state": state} | extra,
                "source": {"generation": 3, "promoted_at": now.isoformat()},
                "mutable": True,
                "can_cancel": state in {"queued", "running", "retry_wait"},
                "retry_key": uuid4(),
                "report_url": "/participation",
                "report_name": "Participation",
            },
        )

    responses = {
        EXPORT_FAILED: export(5191, "failed"),
        _stand_in(EXPORT_FAILED, "retried"): export(5191, "ready"),
        EXPORT_QUEUED: export(5192, "queued"),
        _stand_in(EXPORT_QUEUED, "cancelled"): export(5192, "cancelled"),
        EXACT_FAILED: export(5193, "failed", exact=True),
        _stand_in(EXACT_FAILED, "retried"): export(
            5193, "succeeded", exact=True, export_id=UUID(int=5196)
        ),
        EXACT_QUEUED: export(5194, "queued", exact=True),
        _stand_in(EXACT_QUEUED, "cancelled"): export(5194, "cancelled", exact=True),
        EXPORT_REFUSED: export(5195, "failed"),
    }

    # The Mail message page as delivery_components draws it, with one Admin
    # note already recorded and the four resolutions offered.
    base = {path: extra for path, _, extra in delivery_components(now)}["/delivery"]
    note_only = [dict(id=uuid4(), action="note", label="Save evidence note")]

    def delivery(action, **changes):
        """Email ``action``'s page, with ``changes`` to its email and page."""
        index = (RESOLUTIONS + ("note_only", "stale")).index(action)
        email = base["delivery"] | {"id": UUID(int=5200 + index)}
        email |= changes.pop("delivery", {})
        values = base | {"delivery": email} | changes
        return render_to_string(
            "stewardship/delivery.html",
            context | {"admin_chrome": admin | {"delivery_unknown": 1}} | values,
        )

    def recorded(action, state, outcome=None):
        """The page once ``action`` was recorded and left the email ``state``:
        the decision is the newest evidence and history entry, the Testing
        recipient's line says ``outcome`` (else as before), and only the
        evidence note is still offered (none for a note, which changes
        nothing)."""
        note = dict(action=action, created_at=now, evidence_note=SAVED_NOTE)
        event = dict(attempt=1, action=action, state=state, created_at=now)
        changes = {
            "delivery": {"state": state, "version": 5},
            "notes": [note, *base["notes"]],
            "events": [event, *base["events"]],
        }
        if outcome:
            routed = [
                (address, outcome) for address, _ in base["recipients"]["routed_rows"]
            ]
            changes["recipients"] = base["recipients"] | {"routed_rows": routed}
        if action != "note":
            changes |= {"decisions": False, "commands": note_only}
        return changes

    # The outcome labels as delivery_reads.recipient_rows words them.
    for action, state, outcome in (
        ("note", "delivery_unknown", None),
        ("accept", "delivered", "delivered"),
        ("confirm_unsent", "permanent_failure", "not delivered"),
        ("resend", "pending", "not delivered yet"),
    ):
        # The note email also has a second history page, so paging can be
        # checked to keep a note being typed.
        paged = {"next_query": "page=2"} if action == "note" else {}
        responses[DELIVERY[action]] = delivery(action, **paged)
        responses[_stand_in(DELIVERY[action], "resolved")] = delivery(
            action, **recorded(action, state, outcome)
        )
    responses[DELIVERY["note"] + "?page=2"] = delivery(
        "note",
        previous_query="page=1",
        events=[dict(attempt=1, action="created", state="pending", created_at=now)],
        notes=[
            dict(
                action="note",
                created_at=now - timedelta(days=1),
                evidence_note="Earlier evidence",
            )
        ],
    )
    delivered = {"delivery": {"state": "delivered"}, "decisions": False}
    responses[DELIVERY["note_only"]] = delivery(
        "note_only", commands=note_only, **delivered
    )
    responses[_stand_in(DELIVERY["note_only"], "resolved")] = delivery(
        "note_only",
        **(recorded("note", "delivered") | {"decisions": False, "commands": note_only}),
    )
    responses[DELIVERY["stale"]] = delivery("stale")
    return {path: ("text/html", html) for path, html in responses.items()}


def posts(context, admin):
    """The fixture server's answers: the redirects, and the 409 error pages
    the two refused commands' views answer with."""
    page = context | {"admin_chrome": admin}
    export_error = render_to_string(
        "stewardship/report-export-error.html", page | {"request_id": UUID(int=5195)}
    )
    delivery_error = render_to_string(
        "stewardship/delivery-error.html",
        page | {"message": "This information changed. Reload before trying again."},
    )
    return REDIRECTS | {
        EXPORT_REFUSED_RETRY: (409, None, export_error),
        RESOLVE["stale"]: (409, None, delivery_error),
    }
