"""The Campaign totals block (report-emails spec, "Campaign totals block").

The Daily digest's "Campaign totals" section: the report day's end-of-day
figures, each on one line on a desktop (its label, a table-cell bar and the
exact value, #1844); on a phone the label moves above its bar and value, so
four-digit counts still fit a 320px phone (#1564).
"""

from html import escape

from ..daily_digest import FIRST_LABEL, RESPONDED_LABEL, report_day_cards
from .markup import TABLE, bar, plain, style
from .registry import STATUS, Block, Section


def report_day_bars(document):
    """Pair each report-day card with what its bar measures, if anything.

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
    bars = []
    for label, value in report_day_cards(document):
        part, whole, note = measures.get(label, (None, None, ""))
        if not day.population_available:
            part, note = None, ""
        bars.append((label, value, bar(part, whole), note))
    return tuple(bars)


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
    """The heading and rows in HTML; one "label: value" line per figure in text."""
    totals = report_day_bars(document)
    html = f"<h2{style('heading')}>Campaign totals</h2>" + totals_html(totals)
    text = "\n".join(f"{label}: {value}" for label, value, _bar, _note in totals)
    return Section(html, text)


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
    empty="No campaign day has ended yet.",
)
