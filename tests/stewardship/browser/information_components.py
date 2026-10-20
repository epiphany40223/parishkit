"""Additional information (#1685): the review worklist and a request's page.

Each worklist path renders the real page through the view's own sorting,
``information_worklist.worklist_page`` and the real templates, from placed
rows, so the headings', navigator's and dialogs' POST forms carry exactly
what the production page would. Each test answers a POST with the fixture
page after it, as the view answers with its page redrawn.

The request's page is also served at its real address (``ITEM``), with its
history's second page (``OLDER``) and first page by number (``NEWER``) for
the in-place history pager tests (#519 PR 6).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.pipeline import information_placement
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.information_worklist import (
    SORTING,
    WorklistQuery,
    worklist_page,
)
from parishkit.stewardship.web.tables import paginate

# The browser zone of the dated fixtures, named alike by every engine.
DATED_ZONE = "America/Havana"

CAMPAIGN, ITEM_ID = UUID(int=80), UUID(int=81)
# The worklist's own path: what every heading, navigator, filter and dialog
# posts.
PATH = reverse("admin:information_queue")
ITEM = reverse("admin:information_item", args=[ITEM_ID])
OLDER, NEWER = ITEM + "?page=2", ITEM + "?page=1"
ROWS = 30
# The selection's filters with none applied, over every request.
EVERY_REQUEST = InformationQuery(disposition="all")
# The fixture server answers no POST of these pages itself: the tests
# answer each with the page after it (Playwright routes).
POSTS = {}
# The answers the tests receive after a decision, and their notices.
ACCEPTED = "/information-accepted"
ACCEPTED_NOTICE = "The request from Family DUID 2000 accepted."
IGNORED = "/information-ignored"
IGNORED_NOTICE = "The request from Family DUID 2001 ignored."
REOPENED = "/information-reopened"
REOPENED_NOTICE = "The request from Family DUID 2001 sent back to review."
HANDLED = "/information-handled"
HANDLED_NOTICE = "The request from Family DUID 2000 marked handled."
UNDONE = "/information-undone"
UNDONE_NOTICE = "The request from Family DUID 2000 sent back to review."
BULKED = "/information-bulked"
BULK_NOTICE = "2 requests accepted."
STALE = "/information-stale"
STALE_NOTICE = (
    "The request from Family DUID 2000: not recorded, changed by someone else."
)


# The request page's Contact details (#1595): the Family record and one head,
# with an address long enough to test wrapping at phone width.
CONTACTS = {
    "state": "shown",
    "owners": [
        {
            "label": "Family record",
            "emails": ["the.example.household.with.a.long.address@example.org"],
            "phones": [],
        },
        {
            "label": "Anna Example",
            "emails": ["anna@example.org"],
            "phones": [("Home", "202-555-0123"), ("Mobile", "202-555-0199")],
        },
    ],
}


def placed(index, when, *, accepted=(), ignored=(), handled=()):
    """One request as the worklist reads it: To review unless a decision
    (``accepted``, ``ignored`` or ``handled`` holds its index) moved it, one
    version on."""
    decided = index in accepted or index in ignored or index in handled
    row = {
        "id": str(UUID(int=200 + index)),
        "version": 2 if decided else 1,
        "text": f"Please call household {index:02d} <about the choir>.",
        "disposition": "current_actionable",
        "follow_up_needed": index in accepted or index in handled,
        "followed_up_at": when if index in handled else None,
        "family_duid": 2000 + index,
        "family_name": f"Family {index:02d}",
        "family_id": None,
        # Later Families answered later, so newest first reverses.
        "submitted_at": when + timedelta(minutes=index),
        "decided_by": "staff@example.org" if decided else "",
        "decided_at": when if decided else None,
        "note": "Will call <soon>" if decided else "",
    }
    row["placement"] = information_placement(
        disposition="current_actionable",
        follow_up_needed=row["follow_up_needed"],
        followed_up=row["followed_up_at"] is not None,
        staff_ignored=index in ignored,
    )
    return row


def components(context, admin):
    """The worklist at each table choice and after each decision the tests
    take, and a request's page; real authorization lives in PostgreSQL tests."""
    when = datetime(2054, 10, 5, 12, tzinfo=UTC)
    metadata = {
        "name": "Sample campaign",
        "source_generation": 3,
        "source_as_of": when,
        "timezone": "America/New_York",
    }

    def page(
        paging,
        notice=None,
        *,
        stage="all",
        rows=ROWS,
        mutable=True,
        information=EVERY_REQUEST,
        **changes,
    ):
        """Render the worklist with the table paged and sorted as ``paging``
        says, the Stage filter at ``stage`` and the selection's filters at
        ``information``, ``notice`` above the table and ``changes``
        (``placed``'s) applied, for a Staff reader."""
        query = WorklistQuery(stage=stage, information=information)
        found = [placed(index, when, **changes) for index in range(rows)]
        counts = {}
        for row in found:
            value = row["placement"].stage.value
            counts[value] = counts.get(value, 0) + 1
        shown = [
            row
            for row in found
            if stage == "all" or row["placement"].stage.value == stage
        ]
        table = paginate(
            shown, paging, carry=list(query.form_values().items()), sorting=SORTING
        )
        worklist, table = worklist_page(counts, query, table, PATH, mutable=mutable)
        return (
            "information",
            {
                "metadata": metadata,
                "worklist": worklist,
                "table": table,
                "query": query,
                "query_fields": query.form_values(),
                "export_fields": query.export_values(paging.get("sort", "")),
                "notice": notice,
                "mutable": mutable,
                "request_key": UUID(int=83),
                "campaign_id": CAMPAIGN,
            },
        )

    item = dict(
        id=ITEM_ID,
        version=2,
        family_name="Sample Family",
        family_id=None,
        family_duid=12345,
        submitted_at=when,
        text="Please call <our household>.",
        disposition="superseded",
        disposition_label="Superseded",
        replacement_id=UUID(int=82),
        previously_reported=True,
        correction_resolved=True,
        follow_up_needed=True,
        followed_up_at=when,
        completed_by="staff@example.org",
        placement=information_placement(
            disposition="superseded", follow_up_needed=True, followed_up=True
        ),
    )
    accepted = SimpleNamespace(
        expected_version=1,
        actor_label="staff@example.org",
        created_at=when,
        follow_up_needed=True,
        followed_up=True,
        followed_up_at=when,
        completer_label="staff@example.org",
        notes="Called; awaiting a response.",
        decision_label="Handled",
    )
    request = {
        "metadata": metadata,
        "campaign_id": CAMPAIGN,
        "item": item,
        "contacts": CONTACTS,
        "history": [accepted],
        "history_page": 1,
        "mutable": True,
    }
    older = SimpleNamespace(
        **vars(accepted)
        | {"expected_version": 0, "notes": "Older <edit>", "decision_label": "Accepted"}
    )
    size = {"size": "25"}
    pages = {
        "/information": page(size, stage="to_review"),
        PATH: page(size, stage="to_review"),
        "/information-all": page(size),
        "/information-page-2": page({"size": "25", "page": "2"}),
        "/information-by-name": page({"size": "25", "sort": "-family"}),
        # What Apply returns for submitted days in the browser's zone (#558).
        "/information-dated": page(
            size,
            information=InformationQuery(
                disposition="all",
                start="2026-11-01",
                end="2026-11-02",
                zone=DATED_ZONE,
            ),
        ),
        ACCEPTED: page(size, ACCEPTED_NOTICE, accepted={0}),
        IGNORED: page(size, IGNORED_NOTICE, ignored={1}),
        REOPENED: page(size, REOPENED_NOTICE),
        HANDLED: page(size, HANDLED_NOTICE, handled={0}),
        # Handled's undo (#1840): To handle again, still accepted.
        UNDONE: page(size, UNDONE_NOTICE, accepted={0}),
        BULKED: page(size, BULK_NOTICE, accepted={0, 1}),
        STALE: page(size, STALE_NOTICE),
        # What Apply returns for Stage To handle, one request there.
        "/information-to-handle": page(size, stage="to_handle", accepted={0}),
        "/information-queue-gated": page(size, mutable=False),
        # No requests at all, and none at the stage chosen.
        "/information-none": page(size, rows=0),
        "/information-none-stage": page(size, rows=0, stage="to_review"),
        "/information-item": ("information", request),
        # A request no Staff member has decided yet (#227 empty state).
        "/information-item-new": ("information", request | {"history": []}),
        # A history page past its end, as a bookmarked ?page= can ask for.
        "/information-item-past-end": (
            "information",
            request | {"history": [], "previous_history": 1},
        ),
        "/information-unresolved": (
            "information",
            request | {"item": item | {"correction_resolved": False}},
        ),
        # The request page at its real address, with its history's pages.
        ITEM: ("information", request | {"next_history": 2}),
        NEWER: ("information", request | {"next_history": 2}),
        OLDER: (
            "information",
            request | {"history_page": 2, "previous_history": 1, "history": [older]},
        ),
        "/information-gated": ("information", request | {"mutable": False}),
        "/information-unavailable": (
            "information-error",
            {"campaign_id": CAMPAIGN, "item_id": None, "status": 503},
        ),
        "/information-refused": (
            "information-error",
            {"campaign_id": CAMPAIGN, "item_id": None, "status": 400},
        ),
    }
    return {
        path: (
            "text/html",
            render_to_string(
                f"stewardship/{template}.html", context | {"admin_chrome": admin} | data
            ),
        )
        for path, (template, data) in pages.items()
    }
