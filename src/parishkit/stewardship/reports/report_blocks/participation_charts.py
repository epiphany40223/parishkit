"""The Participation charts block (report-emails spec, "Participation charts block").

The Daily digest's "Daily participation" section: the participation graph
drawn in process as one inline PNG, whose alt text carries the report day's
totals. It adds nothing to the text part, as the digest never did.

It always draws every panel, as the digest did. Its "which charts" option
(spec, "Participation charts block") arrives with the editor (delivery
plan item 6), which is the first place a report can choose panels; it is
not declared before the renderer honours it.
"""

from html import escape
from io import BytesIO

from parishkit.email.base import InlineImage
from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.digest_content import CHART_ID, EMAIL_CHART_WIDTH

from ..charts import render_participation
from ..daily_digest import report_day_cards
from .markup import style
from .registry import STATUS, Block, Section


def chart_alt(document):
    """Alt text that carries the chart's key numbers, not a picture description."""
    chart = document.participation
    totals = " ".join(
        f"{label}: {value}." for label, value in report_day_cards(document)
    )
    return (
        f"Daily participation chart, {format_date(chart.first_date)} to "
        f"{format_date(chart.last_date)}. {totals} Exact values follow in the "
        "table."
    )


def draw(document, frame, options):
    """The heading and the chart image; the PNG travels as an inline image."""
    # Match weekly display normalization; retained observations remain exact.
    # The strict HTML compiler boundary rejects NBSP parser rewrites.
    alt = " ".join(chart_alt(document).split())
    html = f"<h2{style('heading')}>Daily participation</h2>"
    html += (
        f'<img src="cid:{CHART_ID}" alt="{escape(alt, quote=True)}" '
        f'width="{EMAIL_CHART_WIDTH}"'
        f"{style('image')}>"
    )
    stream = BytesIO()
    render_participation(document.participation, stream, format="png", email=True)
    return Section(html, "", (InlineImage(stream.getvalue(), CHART_ID),))


BLOCK = Block(
    key="participation_charts",
    label="Participation charts",
    help=(
        "Charts of first submissions each day, the Families participating "
        "so far and, with financial stewardship, the annual pledges."
    ),
    kind=STATUS,
    fact=True,
    draw=draw,
    empty="No campaign day has ended yet.",
)
