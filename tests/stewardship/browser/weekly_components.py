"""Synthetic weekly report pages rendered with the actual production template."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string


def components(context, admin):
    """No database, live provider or private source content enters browser fixtures."""
    instant = datetime(2026, 10, 8, 12, tzinfo=UTC)
    row = {
        "value": SimpleNamespace(
            item_id=UUID(int=1),
            family_name="Example Family",
            family_duid=1234567,
            submitted_at=instant,
        ),
        "captured": "Current actionable request",
        "current": "Superseded by a later response",
        "actionable": False,
        "text": "Synthetic request " * 50 + "<script>not executable</script>",
        "url": "/weekly-detail",
    }
    page = {
        "snapshot": SimpleNamespace(
            configuration=SimpleNamespace(parish=SimpleNamespace(name="Sample Parish")),
            timezone_configuration=SimpleNamespace(
                name="2026 Census", timezone="America/New_York"
            ),
            preparation=SimpleNamespace(mode="testing"),
            observed_at=instant,
            submission_watermark=1234,
        ),
        "rows": [row],
        "information_count": 1,
        "correction_count": 0,
        "total": 1,
        "page": 1,
        "pages": 2,
        "next_page": 2,
        "report_url": "/weekly-digest",
    }
    return {
        path: (
            "text/html",
            render_to_string(
                "stewardship/weekly-digest.html",
                context | {"admin_chrome": admin} | page | {"detail": detail},
            ),
        )
        for path, detail in (("/weekly-digest", False), ("/weekly-detail", True))
    }
