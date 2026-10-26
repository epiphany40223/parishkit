"""The financial detail report reuses the shared browser server and templates."""

from datetime import UTC, date, datetime
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.chart_specs import financial_charts
from parishkit.stewardship.reports.financial import (
    FIELD_MESSAGES,
    FINANCIAL_SORTING,
    FREQUENCY_LABELS,
    PAGE_SIZES,
    FinancialQuery,
)
from parishkit.stewardship.reports.financial_views import (
    FIELD_IDS,
    FIELD_LINES,
    page_address,
    summary_tiles,
)
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.web.filter_errors import (
    FilterError,
    error_context,
    line_sizes,
)
from parishkit.stewardship.web.tables import report_table

# The browser zone of the dated fixture, named alike by every engine.
DATED_ZONE = "America/Havana"


def refused_values(name="pledge_max", pair="pledge_min", **sent_fields):
    """The view's context for a range out of order, refused at its end.

    By default At most below At least; ``name`` and ``pair`` name another
    range's ends, and ``sent_fields`` the values sent.
    """
    sent = FinancialQuery(**(sent_fields or dict(pledge_min="500", pledge_max="100")))
    error = FilterError(name, FIELD_MESSAGES[name], sent, pair=pair)
    shown = FinancialQuery()
    return error_context(error, FIELD_IDS, FIELD_LINES) | dict(
        query=sent, applied=shown, query_fields=shown.form_values()
    )


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
        frequency_short="mo",
        shares=[
            # The first has a short label (#1569); the second does not, so its
            # short form is its label.
            dict(label="Online <giving>", short="Online", text=""),
            dict(label="Another way", short="Another way", text="Stock <gift>"),
        ],
        source_pledge=MoneyAmount(120000),
        source_contributions=MoneyAmount(10000),
        # Entered by Staff for the Family through Open form (#794).
        staff_entered=True,
    )
    # A zero pledge has no frequency; without proof, source money is unavailable.
    unproven = row | dict(
        active=None,
        annual=MoneyAmount(0),
        installment=MoneyAmount(None),
        frequency="",
        frequency_label=FREQUENCY_LABELS["none"],
        frequency_short="",
        shares=[],
        source_pledge=MoneyAmount(None),
        source_contributions=MoneyAmount(None),
        staff_entered=False,
    )
    # Rows as a parish's typically read (#1544): a longer surname-first
    # name, a seven-digit DUID, five-figure sums and up to three methods
    # (each row carries its short labels, #1569), one with Other text, an
    # inactive Family with its badge, and a Family with a single Other
    # method.
    typical = [
        row
        | dict(
            family_name="Hernandez-Oliveira, Maria and Jose",
            family_duid=7654321,
            annual=MoneyAmount(1200000),
            installment=MoneyAmount(100000),
            shares=[
                dict(label="Online giving", short="Online", text=""),
                dict(label="Envelope", short="Envelope", text=""),
                dict(label="Other", short="Other", text="Quarterly stock transfer"),
            ],
            source_pledge=MoneyAmount(1150000),
            source_contributions=MoneyAmount(1098765),
        ),
        unproven | dict(family_name="Nguyen, Thanh", active=False),
        row
        | dict(
            family_name="Smith, John",
            staff_entered=False,
            shares=[dict(label="Other", short="Other", text="Payroll gift")],
        ),
    ]
    # Share methods are stable option identities, as the closed grammar requires.
    online, other = str(UUID(int=98)), str(UUID(int=99))
    query = FinancialQuery.parse(
        dict(search="Example", pledge_min="100.00", share=online)
    )
    # What Apply returns for response days in the browser's zone (#558).
    dated = FinancialQuery.parse(
        dict(
            search="Example",
            first_start="2026-11-01",
            latest_end="2026-11-02",
            zone=DATED_ZONE,
        )
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
            action=reverse("admin:financial_report"),
            sizes=PAGE_SIZES,
        )

    choices = [(other, "Another way"), (online, "Online <giving>")]
    summary = dict(
        families=51,
        annual_total=MoneyAmount(6295950),
        frequencies=[(label, 3) for label in FREQUENCY_LABELS.values()],
        shares=[("Another way", 4), ("Online <giving>", 40)],
        share_counts={other: 4, online: 40},
        no_share=7,
        cannot_give=2,
    )
    filtered = summary | dict(
        families=4,
        annual_total=MoneyAmount(480000),
        frequencies=[
            (label, int(key != "none")) for key, label in FREQUENCY_LABELS.items()
        ],
        # Nobody here chose Another way: its bar stays, at zero, so the
        # chart keeps its height and the filters below it do not move.
        shares=[("Online <giving>", 2)],
        share_counts={online: 2},
        no_share=1,
    )
    long_choices = [(other, "Online giving through Faith Direct")]
    crowded = summary | dict(
        families=1031,
        frequencies=[(label, 1031) for label in FREQUENCY_LABELS.values()],
        share_counts={other: 1031},
        no_share=0,
    )
    values = dict(
        campaign_id=campaign,
        query=query,
        query_fields=query.form_values(),
        frequencies=FREQUENCY_LABELS,
        share_choices=choices,
        table=table([row]),
        total=51,
        # The export form: enabled, with its one-time key and timezone choices.
        mutable=True,
        # This view's own address (#1586): the sample's search and pledge
        # range are private, and its share method is closed.
        page_address=page_address(reverse("admin:financial_report"), query, 50),
        request_key=UUID(int=100),
        # The filters' reserved message lines (#1332), as the view sizes them.
        filter_sizes=line_sizes(FIELD_MESSAGES, FIELD_LINES),
        summary=summary,
        # The summary's tiles and charts (#720), as the view builds them.
        summary_tiles=summary_tiles(summary),
        charts=financial_charts(summary, choices),
        metadata=dict(
            name="Sample campaign",
            source_as_of=moment,
            timezone="UTC",
            comparison_start="2025-07-01",
            comparison_end="2026-06-30",
            comparison_years="2025–2026",
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
        "/financial-typical": values | dict(table=table(typical)),
        # Every money cell at $99,999, the table's whole-dollar form of
        # $99,999.00 (#2258).
        "/financial-wide": values
        | dict(
            table=table(
                [
                    found
                    | dict(
                        annual=MoneyAmount(9999900),
                        source_pledge=MoneyAmount(9999900),
                        source_contributions=MoneyAmount(9999900),
                    )
                    for found in typical
                ]
            )
        ),
        # The campaign cannot accept work: the export controls are offered gated.
        "/financial-gated": values | dict(mutable=False),
        # The report as first opened, with no filter applied: its Filters
        # panel starts closed (#227).
        "/financial-unfiltered": values
        | dict(query=FinancialQuery(), query_fields=FinancialQuery().form_values()),
        "/financial-last": values | dict(table=table([row], number=2)),
        # The answer to an applied filter: a smaller summary, its charts
        # drawn again in place (#720).
        "/financial-filtered": values
        | dict(
            summary=filtered,
            summary_tiles=summary_tiles(filtered),
            charts=financial_charts(filtered, choices),
            total=4,
            # The answer names its view's closed choices for the address bar.
            page_address=page_address(
                reverse("admin:financial_report"),
                FinancialQuery(frequency="monthly", sort="pledge_desc"),
                100,
            ),
        ),
        # Every Family chose one long-named method (#1266): its full-length
        # bar's label must still fit after it at phone width.
        "/financial-long-labels": values
        | dict(charts=financial_charts(crowded, long_choices)),
        # Filters the server refused at At most (#1332): drawn again with
        # the values sent, the error at the field and the unfiltered results.
        "/financial-refused": values | refused_values(),
        # Each response-date range refused the same way, a Through date
        # before its From date (#1469: the message said once).
        "/financial-refused-first": values
        | refused_values(
            "first_end",
            "first_start",
            first_start="2026-11-02",
            first_end="2026-11-01",
            zone=DATED_ZONE,
        ),
        "/financial-refused-latest": values
        | refused_values(
            "latest_end",
            "latest_start",
            latest_start="2026-11-02",
            latest_end="2026-11-01",
            zone=DATED_ZONE,
        ),
        "/financial-dated": values
        | dict(query=dated, query_fields=dated.form_values()),
    }
    # The report at its own address, and at the address an applied filter's
    # answer names (#1586), so a reload of that address draws its view.
    route = reverse("admin:financial_report")
    pages[route] = pages["/financial-report"]
    pages[pages["/financial-filtered"]["page_address"]] = pages["/financial-filtered"]
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
    # Dates sent without the browser's zone (#558).
    result["/financial-error-zone"] = (
        "text/html",
        render_to_string(
            "stewardship/financial-report-error.html",
            context
            | {"admin_chrome": admin}
            | dict(campaign_id=campaign, status=400, zone=True),
        ),
    )
    return result
