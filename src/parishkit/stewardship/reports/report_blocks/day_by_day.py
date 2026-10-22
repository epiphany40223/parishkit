"""The Day by day table block (report-emails spec, "Day by day table block").

The Daily digest's "Day by day" table: the last campaign days up to the
report day, and every day a recovery send covers, one row per day on a
desktop with its date first (#1844), and a date row of its own on a phone so
the table fits a 320px phone (#1559). The text part keeps one line per day.
"""

from datetime import timedelta

from ..daily_digest import PLEDGE_HEADING, participation_row
from .markup import plain, style
from .registry import STATUS, Block, Section

# The day-by-day table shows at least this many of the latest campaign days
# (five since #1532); the block's "days shown" option defaults to it.
TABLE_DAYS = 5


def digest_rows(document, days=TABLE_DAYS):
    """Show the last ``days`` days and every day in a missed range, never a page.

    A recovery digest's covered range can be longer than that; all of it is
    shown. The chart above the table always has the whole campaign.
    """
    chart = document.participation
    first = min(document.covered_dates[0], chart.last_date - timedelta(days=days - 1))
    return tuple(
        participation_row(day, financial_enabled=chart.financial_enabled)
        for day in chart.days
        if day.local_date >= first
    )


def headings(document):
    """The table's column headings; pledges only with financial stewardship."""
    names = ["Campaign date", "First submissions", "Cumulative participation"]
    if document.participation.financial_enabled:
        names.append(PLEDGE_HEADING)
    return names


def day_table_html(headings, rows):
    """The "Day by day" table: one row per day, its date first (#1844).

    The date is a row heading that never wraps, so on a desktop each day is
    one line. Four columns need about 380px, wider than a 320px phone leaves
    inside the email's card, and mail programs cannot scroll a table
    (#1559). So each day also has a date row across the value columns,
    hidden inline; a mail program that keeps the email's <style> block shows
    it on a phone and hides the date column instead (``pk-narrow`` and
    ``pk-wide``). In every mail program that honours inline styles only
    one of the two is displayed, so a screen reader hears each date once.
    """
    span = len(headings) - 1

    def cells(tag, items, name, extra=""):
        """One row of right-aligned value cells, padded from the one before."""
        return "".join(
            f'<{tag}{extra} align="right"{style(name)}>{plain(item)}</{tag}>'
            for item in items
        )

    html = (
        # No role="presentation": this is a data table, and that role would strip
        # the header semantics from the table and every cell (#1559 review).
        '<table width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{style("table")}><caption{style("table-caption")}>'
        "Day by day</caption><thead><tr>"
        f'<th scope="col" align="left" class="pk-wide"{style("th-lead")}>'
        f"{plain(headings[0])}</th>"
        + cells("th", headings[1:], "th", ' scope="col"')
        + "</tr></thead>"
    )
    for day, *figures in rows:
        html += (
            f'<tbody><tr class="pk-narrow"{style("day-row")}>'
            f'<th scope="rowgroup" colspan="{span}" align="left"'
            f"{style('day-narrow')}>{plain(day)}</th></tr>"
            f'<tr><th scope="row" align="left" class="pk-wide"'
            f"{style('day-lead')}>{plain(day)}</th>"
            f"{cells('td', figures, 'td')}</tr></tbody>"
        )
    return html + "</table>"


def draw(document, frame, options):
    """The table in HTML; a heading line and one line per day in text."""
    names = headings(document)
    rows = digest_rows(document, options["days"])
    text = " | ".join(names) + "\n" + "\n".join(" | ".join(row) for row in rows)
    return Section(day_table_html(names, rows), text)


# The most days the table may show (spec, "Day by day table block").
MAX_DAYS = 14


def capture(document, options):
    """The table's headings and rows, as the snapshot keeps them."""
    return {
        "headings": headings(document),
        "rows": [list(row) for row in digest_rows(document, options["days"])],
    }


def copy(data, frame):
    """The kept table's headings and rows, for the saved copy's table."""
    return {"headings": data["headings"], "rows": data["rows"]}


def check(options):
    """Refuse a "days shown" value other than a whole number from 1 to 14."""
    days = options.get("days", TABLE_DAYS)
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise ValueError("The day by day table shows 1 to 14 days.")


BLOCK = Block(
    key="day_by_day",
    label="Day by day table",
    help=(
        "A table of the latest campaign days: each day's first submissions, "
        "participation and, with financial stewardship, pledges."
    ),
    kind=STATUS,
    fact=True,
    draw=draw,
    empty="No campaign day has ended yet.",
    options={"days": TABLE_DAYS},
    check=check,
    capture=capture,
    copy=copy,
)
