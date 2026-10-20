"""The Day by day table block (report-emails spec, "Day by day table block").

The Daily digest's "Day by day" table: the last campaign days up to the
report day, and every day a recovery send covers, each day two rows so the
table fits a 320px phone (#1559). The text part keeps one line per day.
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
    """The "Day by day" table, two rows per day so it fits a phone (#1559).

    Four columns (date, first submissions, participation, pledges) need about
    380px, wider than a 320px phone leaves inside the email's card, and mail
    programs cannot scroll a table. So each day's date heads its own row
    across the table, a row-group heading for screen readers, and its values
    follow in one row under the value columns' headings. The first value
    column has no left padding, so the table starts flush with its caption.
    """
    values = headings[1:]
    span = len(values)

    def cells(tag, items, name, extra=""):
        """One row of cells; the first is left-aligned under its date.

        The later cells are right-aligned and padded.
        """
        return "".join(
            f'<{tag}{extra} align="{"right" if index else "left"}"'
            f"{style(name if index else name + '-lead')}>"
            f"{plain(item)}</{tag}>"
            for index, item in enumerate(items)
        )

    html = (
        # No role="presentation": this is a data table, and that role would strip
        # the header semantics from the table and every cell (#1559 review).
        '<table width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{style("table")}><caption{style("table-caption")}>'
        "Day by day</caption><thead><tr>"
        + cells("th", values, "th", ' scope="col"')
        + "</tr></thead>"
    )
    for day, *figures in rows:
        html += (
            f'<tbody><tr><th scope="rowgroup" colspan="{span}" align="left"'
            f"{style('day')}>{plain(day)}</th></tr>"
            f"<tr>{cells('td', figures, 'td')}</tr></tbody>"
        )
    return html + "</table>"


def draw(document, frame, options):
    """The table in HTML; a heading line and one line per day in text."""
    names = headings(document)
    rows = digest_rows(document, options["days"])
    text = " | ".join(names) + "\n" + "\n".join(" | ".join(row) for row in rows)
    return Section(day_table_html(names, rows), text)


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
)
