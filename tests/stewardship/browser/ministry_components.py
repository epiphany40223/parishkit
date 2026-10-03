"""Ministry reports reuse the shared browser server and production templates."""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.ministries import (
    DETAIL_SORTING,
    STATES,
    SUMMARY_SORTING,
    MinistryQuery,
)
from parishkit.stewardship.web.tables import report_table


def _table(rows, query, total, *, ministry, action):
    """The shared POST navigator/heading model the view builds (#203)."""
    return report_table(
        rows,
        number=1,
        size=50,
        total=total,
        carry=[(k, v) for k, v in query.form_values().items() if k != "sort"]
        + ([("ministry", str(ministry))] if ministry else []),
        sorting=DETAIL_SORTING if ministry else SUMMARY_SORTING,
        sort=query.sort,
        action=action,
    )


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
        states=STATES,
        report_url=root + "join/",
        summary_url=root,
        total=51,
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
    detail = _table(values["rows"], query, 51, ministry=9, action=root + "join/")
    history = MinistryQuery(history="all")
    summary = values | dict(ministry_id=None, action=None, rows=[], report_url=root)
    # The summary by Ministry name the other way, with a second Ministry on
    # the page: what its heading's POST form returns, served from a GET path
    # to the in-place re-sort tests (#478).
    descending = MinistryQuery(search="Example", sort="name_desc")
    more = [dict(values["summaries"][0], duid=10, name="Another Ministry")]
    summaries = more + values["summaries"]
    pages = {
        "/ministry-detail": values | dict(table=detail),
        "/ministry-summary": summary
        | dict(
            table=_table(values["summaries"], query, 1, ministry=None, action=root),
        ),
        "/ministry-summary-desc": summary
        | dict(
            query=descending,
            export_fields=descending.form_values(),
            summaries=summaries,
            table=_table(summaries, descending, 2, ministry=None, action=root),
        ),
        "/ministry-history": values
        | dict(
            query=history,
            table=_table(values["rows"], history, 51, ministry=9, action=root),
        ),
        "/ministry-empty": values
        | dict(
            rows=[],
            summaries=[],
            total=0,
            table=_table([], query, 0, ministry=9, action=root + "join/"),
        ),
        "/ministry-gated": values | dict(mutable=False, table=detail),
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
