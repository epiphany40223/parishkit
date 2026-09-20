"""Ministry reports reuse the shared browser server and production templates."""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.ministries import STATES, MinistryQuery


def components(context, admin):
    """Detached authorized sample data exercises native controls and escaping."""
    campaign = UUID(int=90)
    moment = datetime(2026, 9, 19, tzinfo=UTC)
    root = f"/admin/reports/{campaign}/ministries/"
    query = MinistryQuery(search="Example")
    values = dict(
        campaign_id=campaign,
        ministry_id=9,
        action="join",
        query=query,
        mutable=True,
        export_key=UUID(int=91),
        packet_key=UUID(int=96),
        export_fields=query.form_values(),
        export_timezones=["UTC", "America/Detroit"],
        query_fields=query.form_values() | {"ministry": 9},
        states=STATES,
        report_url=root + "join/",
        summary_url=root,
        total=51,
        next_page=2,
        metadata=dict(
            name="Sample campaign",
            source_generation=1234,
            source_as_of=moment,
            report_date="2026-09-19",
            timezone="America/Detroit",
        ),
        summaries=[
            dict(
                duid=9,
                name="Example <Ministry>",
                active=False,
                joining=51,
                leaving=0,
                unresolved=51,
                progress="0 out of 51 (0%)",
            )
        ],
        rows=[
            dict(
                member_name="Example <Member>",
                member_duid=12345,
                submitted_at=moment,
                state_label="New",
                outcome_label="Not yet recorded",
                gender="Unspecified",
                age=66,
                email_visibility="not_published",
                phone_visibility="not_published",
                address_lines=["1 Example Street"],
            )
        ],
    )
    pages = {
        "/ministry-detail": values,
        "/ministry-summary": values
        | dict(
            ministry_id=None,
            action=None,
            rows=[],
            query_fields=query.form_values(),
            report_url=root,
        ),
        "/ministry-history": values | dict(query=MinistryQuery(history="all")),
        "/ministry-empty": values
        | dict(rows=[], summaries=[], total=0, next_page=None),
        "/ministry-gated": values | dict(mutable=False),
    }
    return {
        path: (
            "text/html",
            render_to_string(
                "stewardship/ministry-report.html",
                context | {"admin_chrome": admin} | data,
            ),
        )
        for path, data in pages.items()
    }
