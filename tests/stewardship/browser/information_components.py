"""Synthetic staff requests rendered through the actual production templates.

The item page is also served at its real address (``ITEM``), with a "saved"
view (``SAVED``), and the fixture server answers its Save POST with a real
Post/Redirect/Get redirect (``POSTS``), for the in-place Save tests (#519).
Its Staff history's second page (``OLDER``) and first page by number
(``NEWER``) serve the in-place history pager tests (#519 PR 6).
"""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.information import (
    INFORMATION_SORTING,
    PAGE_SIZES,
    InformationQuery,
)
from parishkit.stewardship.web.tables import report_table

CAMPAIGN, ITEM_ID = UUID(int=80), UUID(int=81)
ITEM = reverse("admin:information_item", args=[ITEM_ID])
UPDATE = reverse("admin:information_update", args=[ITEM_ID])
SAVED = ITEM + "?saved=1"
OLDER, NEWER = ITEM + "?page=2", ITEM + "?page=1"
# Save and next (#534): an item whose save moves to the next item in the
# remembered queue view, the next item's page, and the queue view itself.
TOKEN = "cd" * 16
ADVANCE_ID, NEXT_ID, LAST_ID = UUID(int=85), UUID(int=86), UUID(int=87)
ADVANCE_ITEM = reverse("admin:information_item", args=[ADVANCE_ID])
ADVANCE_UPDATE = reverse("admin:information_update", args=[ADVANCE_ID])
NEXT_ITEM = reverse("admin:information_item", args=[NEXT_ID]) + f"?queue={TOKEN}"
LAST_ITEM = reverse("admin:information_item", args=[LAST_ID])
LAST_UPDATE = reverse("admin:information_update", args=[LAST_ID])
QUEUE_VIEW = reverse("admin:information_queue") + f"?queue={TOKEN}"
# The fixture server's answers to a Save (status, Location, body).
POSTS = {
    UPDATE: (303, SAVED, ""),
    ADVANCE_UPDATE: (303, NEXT_ITEM, ""),
    LAST_UPDATE: (303, QUEUE_VIEW, ""),
}


def components(context, admin):
    """Keep UI checks credential-free; real authorization lives in PostgreSQL tests."""
    instant = datetime(2026, 10, 2, 12, tzinfo=UTC)
    campaign = CAMPAIGN
    item = dict(
        id=ITEM_ID,
        version=2,
        family_name="Sample Family",
        family_duid=12345,
        submitted_at=instant,
        text="Please call <our household>.",
        disposition="superseded",
        disposition_label="Superseded",
        replacement_id=UUID(int=82),
        previously_reported=True,
        correction_resolved=True,
        follow_up_needed=True,
        followed_up_at=instant,
        completed_by="staff@example.org",
        notes="Called; awaiting a response.",
    )
    query = InformationQuery(search="Sample", disposition="all")

    def table(query, rows=(item,), total=51):
        """The queue's report table under ``query``'s filters and sort."""
        return report_table(
            list(rows),
            number=1,
            size=50,
            total=total,
            carry=[
                (key, value)
                for key, value in query.form_values().items()
                if key != "sort"
            ],
            sorting=INFORMATION_SORTING,
            sort=query.sort,
            action=reverse("admin:information_queue"),
            sizes=PAGE_SIZES,
        )

    # What the Family heading's POST form returns: the queue by Family name,
    # served from a GET path to the in-place re-sort tests (#478).
    by_name = replace(query, sort="name")
    withdrawn = replace(query, disposition="withdrawn")
    nothing = replace(query, search="Nobody")
    other = item | {"id": UUID(int=84), "family_name": "Other Family"}
    values = dict(
        metadata=dict(
            name="Sample campaign",
            source_generation=3,
            source_as_of=instant,
            timezone="America/New_York",
        ),
        campaign_id=campaign,
        query=query,
        query_fields=query.form_values(),
        total=51,
        rows=[item],
        table=table(query),
        mutable=True,
        export_timezones=("UTC", "America/Detroit"),
        request_key=UUID(int=83),
        history_page=1,
        history=[
            SimpleNamespace(
                expected_version=1,
                actor_label="staff@example.org",
                created_at=instant,
                follow_up_needed=True,
                followed_up=True,
                followed_up_at=instant,
                completer_label="staff@example.org",
                notes=item["notes"],
            )
        ],
    )
    pages = {
        "/information": ("information", values),
        "/information-by-name": (
            "information",
            values
            | {
                "query": by_name,
                "query_fields": by_name.form_values(),
                "table": table(by_name),
            },
        ),
        # What Apply filters returns for withdrawn requests (#484): another
        # Family, a smaller matching count, and a search that matches none.
        "/information-withdrawn": (
            "information",
            values
            | {
                "query": withdrawn,
                "query_fields": withdrawn.form_values(),
                "total": 3,
                "table": table(withdrawn, [other], 3),
            },
        ),
        "/information-none": (
            "information",
            values
            | {
                "query": nothing,
                "query_fields": nothing.form_values(),
                "total": 0,
                "table": table(nothing, [], 0),
            },
        ),
        "/information-queue-gated": ("information", values | {"mutable": False}),
        "/information-item": ("information", values | {"item": item}),
        "/information-unresolved": (
            "information",
            values | {"item": item | {"correction_resolved": False}},
        ),
        # The item page at its real address, before and after a save that
        # changed the notes and added a version to the Staff history.
        ITEM: ("information", values | {"item": item, "next_history": 2}),
        NEWER: ("information", values | {"item": item, "next_history": 2}),
        # The history's last page: an older edit, and only Newer history.
        OLDER: (
            "information",
            values
            | {
                "item": item,
                "history_page": 2,
                "previous_history": 1,
                "history": [
                    SimpleNamespace(
                        **vars(values["history"][0])
                        | {"expected_version": 0, "notes": "Older <edit>"}
                    )
                ],
            },
        ),
        SAVED: (
            "information",
            values
            | {
                "item": item | {"version": 3, "notes": "Saved <note>"},
                "history": [
                    SimpleNamespace(
                        **vars(values["history"][0])
                        | {"expected_version": 2, "notes": "Saved <note>"}
                    ),
                    *values["history"],
                ],
            },
        ),
        ADVANCE_ITEM: (
            "information",
            values
            | {
                "item": item | {"id": ADVANCE_ID, "family_name": "First Family"},
                "queue_token": TOKEN,
                "next_id": str(NEXT_ID),
            },
        ),
        NEXT_ITEM: (
            "information",
            values
            | {
                "item": item | {"id": NEXT_ID, "family_name": "Second Family"},
                "queue_token": TOKEN,
                "next_id": str(LAST_ID),
            },
        ),
        LAST_ITEM: (
            "information",
            values
            | {
                "item": item | {"id": LAST_ID, "family_name": "Last Family"},
                "queue_token": TOKEN,
            },
        ),
        QUEUE_VIEW: ("information", values | {"queue_token": TOKEN}),
        "/information-gated": (
            "information",
            values | {"item": item, "mutable": False},
        ),
        "/information-unavailable": (
            "information-error",
            {"campaign_id": campaign, "item_id": None, "status": 503},
        ),
        "/information-refused": (
            "information-error",
            {"campaign_id": campaign, "item_id": None, "status": 400},
        ),
        "/information-conflict": (
            "information-error",
            {"campaign_id": campaign, "item_id": item["id"], "status": 409},
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
