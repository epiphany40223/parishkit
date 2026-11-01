"""The Campaign totals block (report-emails spec, "Campaign totals block").

The Daily digest's "Campaign totals" section: the report day's end-of-day
figures, each on one line on a desktop (its label, a table-cell bar and the
exact value, #1844); on a phone the label moves above its bar and value, so
four-digit counts still fit a 320px phone (#1564). Under its heading a
fixed line names the Families counted and the report day (#2331): a report
email counts today's campaign Families, as the report pages do by default.
"""

from html import escape
from zoneinfo import ZoneInfo

from parishkit.stewardship.web.dates import format_date, format_time

from ..daily_digest import FIRST_LABEL, RESPONDED_LABEL, report_day_cards
from ..report_document import SEND_TIME, ReportDocument
from .markup import TABLE, bar, plain, style
from .registry import STATUS, Block, Section


def report_day_figures(document):
    """Each report-day card: (label, value, bar part, bar whole, note).

    Families that have responded are drawn against the Families they are out
    of; the day's first submissions against the campaign's busiest day so far,
    stated beside the number. Pledges have no end-of-day target (the
    comparison pledges are a live figure, #721), so they have no bar.
    """
    chart = document.participation
    day = document.report_day
    available = [entry for entry in chart.days if entry.population_available]
    peak = max((entry.first_responses for entry in available), default=0)
    # Bars follow each card's identity, not its position in the list.
    measures = {
        RESPONDED_LABEL: (day.cumulative_responses, day.cohort_denominator, ""),
        FIRST_LABEL: (
            day.first_responses,
            peak,
            f" (busiest day: {peak:,})" if peak else "",
        ),
    }
    figures = []
    for label, value in report_day_cards(document):
        part, whole, note = measures.get(label, (None, None, ""))
        if not day.population_available:
            part, note = None, ""
        figures.append((label, value, part, whole, note))
    return tuple(figures)


def report_day_bars(document):
    """Each report-day card with its bar drawn: (label, value, bar HTML, note)."""
    return tuple(
        (label, value, bar(part, whole), note)
        for label, value, part, whole, note in report_day_figures(document)
    )


# The Families a report email's figures count, in the report pages'
# Families counted words (#2324, #2331).
FAMILIES_COUNTED = "In this campaign today"


def families_counted(document):
    """The fixed line naming the Families counted and the report day (#2331).

    "Families counted: In this campaign today, as of the end of October 30,
    2026" for a report as of the end of a day; a report as of when it is
    made names that moment instead ("as of 7:00 AM, October 31, 2026").
    None for a retired digest's document, which counted the historical
    cohort and drew no such line.
    """
    if not isinstance(document, ReportDocument):
        return None
    if document.as_of == SEND_TIME:
        local = document.as_of_at.astimezone(ZoneInfo(document.campaign_timezone))
        when = f"{format_time(local)}, {format_date(local.date())}"
    else:
        when = f"the end of {format_date(document.report_day.local_date)}"
    return f"Families counted: {FAMILIES_COUNTED}, as of {when}"


def capture(document, options):
    """The report day, its figures and its Families counted line, as kept."""
    return {
        "report_day": document.report_day.local_date.isoformat(),
        "figures": [list(figure) for figure in report_day_figures(document)],
        "families_counted": families_counted(document) or "",
    }


def unbroken(text):
    """Escape a value so it wraps only before a parenthesis (#1564).

    "1,234 out of 2,345 (52.6%)" may break before "(52.6%)" and nowhere else:
    ``&nbsp;`` keeps the other words together in every mail program, where
    ``white-space`` on part of a cell is not reliable.
    """
    parts = escape(text, quote=False).split(" (")
    return " (".join(part.replace(" ", "&nbsp;") for part in parts)


def totals_html(totals):
    """The "Campaign totals" rows: label, bar and value on one line (#1844).

    Each figure's label cell (it never wraps) sits beside one cell holding
    a small table of its bar and exact value, so on a desktop every figure
    is one line, as in v1.18.0. One row needs about 420px with four-digit
    counts, wider than a 320px phone leaves inside the email's card (#1564).
    A mail program that keeps the email's <style> block stacks the two cells
    there (``pk-stack``): the label above, the bar and value below, the
    value wrapping only before its parenthesis (``pk-wrap``, unbroken).
    """
    html = f"<table{TABLE}{style('rows')}><tbody>"
    html += "".join(
        f'<tr><td width="34%" class="pk-stack"{style("total-name")}>'
        f"{plain(label)}</td>"
        f'<td width="66%" class="pk-stack"{style("total-figure")}>'
        f"<table{TABLE}{style('track')}><tbody><tr>"
        f'<td width="60%"{style("bar")}>{bar_html}</td>'
        f'<td width="40%" align="right" class="pk-wrap"{style("value")}>'
        f"<strong>{unbroken(value)}</strong>{unbroken(note)}</td>"
        "</tr></tbody></table></td></tr>"
        for label, value, bar_html, note in totals
    )
    return html + "</tbody></table>"


def draw(document, frame, options):
    """The heading, the Families counted line and the rows; text lines alike.

    The text part gives the line, then one "label: value" line per figure.
    """
    totals = report_day_bars(document)
    line = families_counted(document)
    html = f"<h2{style('heading')}>Campaign totals</h2>"
    if line:
        html += f"<p{style('caption')}>{plain(line)}</p>"
    html += totals_html(totals)
    text = "\n".join(
        ([line] if line else [])
        + [f"{label}: {value}" for label, value, _bar, _note in totals]
    )
    return Section(html, text)


def copy(data, frame):
    """The saved copy's Campaign totals: stat tiles in the email's words (#1639).

    A send captured since #2331 keeps its Families counted line, shown as
    the email showed it; an earlier send has none.
    """
    from ..digest_presentation import saved_total_tiles

    return {
        "tiles": saved_total_tiles(data["figures"]),
        "families_counted": data.get("families_counted", ""),
    }


BLOCK = Block(
    key="campaign_totals",
    label="Campaign totals",
    help=(
        "How many Families have responded, the day's first submissions and, "
        "with financial stewardship, the annual pledges so far."
    ),
    kind=STATUS,
    fact=True,
    draw=draw,
    capture=capture,
    copy=copy,
    empty="No campaign day has ended yet.",
)
