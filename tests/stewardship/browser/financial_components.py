"""The financial detail report reuses the shared browser server and templates."""

from datetime import UTC, date, datetime
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.financial import (
    FINANCIAL_SORTING,
    FREQUENCY_LABELS,
    PAGE_SIZES,
    FinancialQuery,
)
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.web.tables import report_table


def components(context, admin):
    """Detached authorized sample data exercises native controls and escaping."""
    campaign = UUID(int=96)
    moment = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
    row = dict(
        id=str(UUID(int=97)),
        family_name="Example <Family>",
        family_duid=1234567,
        active=True,
        submitted_at=moment,
        first_submitted_at=moment,
        family_version=2,
        annual=MoneyAmount(123450),
        installment=MoneyAmount(10288),
        frequency="monthly",
        frequency_label=FREQUENCY_LABELS["monthly"],
        shares=[
            dict(label="Online <giving>", text=""),
            dict(label="Another way", text="Stock <gift>"),
        ],
        source_pledge=MoneyAmount(120000),
        source_contributions=MoneyAmount(10000),
    )
    # A zero pledge has no frequency; without proof, source money is unavailable.
    unproven = row | dict(
        active=None,
        annual=MoneyAmount(0),
        installment=MoneyAmount(None),
        frequency="",
        frequency_label=FREQUENCY_LABELS["none"],
        shares=[],
        source_pledge=MoneyAmount(None),
        source_contributions=MoneyAmount(None),
    )
    # Share methods are stable option identities, as the closed grammar requires.
    online, other = str(UUID(int=98)), str(UUID(int=99))
    query = FinancialQuery.parse(
        dict(search="Example", pledge_min="100.00", share=online)
    )

    def table(rows, *, number=1, total=51):
        """The shared POST report table the view builds around one SQL page."""
        return report_table(
            rows,
            number=number,
            size=50,
            total=total,
            carry=[
                (key, value)
                for key, value in query.form_values().items()
                if key != "sort"
            ],
            sorting=FINANCIAL_SORTING,
            sort=query.sort,
            action=f"/admin/reports/{campaign}/financial/",
            sizes=PAGE_SIZES,
        )

    values = dict(
        campaign_id=campaign,
        query=query,
        query_fields=query.form_values(),
        frequencies=FREQUENCY_LABELS,
        share_choices=[(other, "Another way"), (online, "Online <giving>")],
        table=table([row]),
        total=51,
        # The export form: enabled, with its one-time key and timezone choices.
        mutable=True,
        request_key=UUID(int=100),
        export_timezones=("America/New_York", "UTC"),
        summary=dict(
            families=51,
            annual_total=MoneyAmount(6295950),
            frequencies=[(label, 3) for label in FREQUENCY_LABELS.values()],
            shares=[("Another way", 4), ("Online <giving>", 40)],
            no_share=7,
        ),
        metadata=dict(
            name="Sample campaign",
            source_as_of=moment,
            timezone="UTC",
            comparison_start="2025-07-01",
            comparison_end="2026-06-30",
            giving_through=date(2026, 6, 30),
        ),
    )
    pages = {
        "/financial-report": values,
        "/financial-unproven": values
        | dict(
            table=table([unproven, row | dict(active=False)]),
            metadata=values["metadata"] | dict(giving_through=None),
        ),
        "/financial-empty": values | dict(table=table([], total=0), total=0),
        # The campaign cannot accept work: the export controls are offered gated.
        "/financial-gated": values | dict(mutable=False),
        "/financial-last": values | dict(table=table([row], number=2)),
    }
    result = {
        path: (
            "text/html",
            render_to_string(
                "stewardship/financial-report.html",
                context | {"admin_chrome": admin} | data,
            ),
        )
        for path, data in pages.items()
    }
    for status in (400, 503):
        result[f"/financial-error-{status}"] = (
            "text/html",
            render_to_string(
                "stewardship/financial-report-error.html",
                context
                | {"admin_chrome": admin}
                | dict(campaign_id=campaign, status=status),
            ),
        )
    return result
