"""Synthetic staff requests rendered through the actual production templates."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.information import InformationQuery


def components(context, admin):
    """Keep UI checks credential-free; real authorization lives in PostgreSQL tests."""
    instant = datetime(2026, 10, 2, 12, tzinfo=UTC)
    campaign = UUID(int=80)
    item = dict(
        id=UUID(int=81),
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
        mutable=True,
        export_timezones=("UTC", "America/Detroit"),
        next_page=2,
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
        "/information-queue-gated": ("information", values | {"mutable": False}),
        "/information-item": ("information", values | {"item": item}),
        "/information-unresolved": (
            "information",
            values | {"item": item | {"correction_resolved": False}},
        ),
        "/information-gated": (
            "information",
            values | {"item": item, "mutable": False},
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
