"""The Participation charts block (report-emails spec, "Participation charts block").

The Daily digest's "Daily participation" section: the participation graph
drawn in process as one inline PNG, whose alt text carries the report day's
totals. It adds nothing to the text part, as the digest never did.

Its "which charts" option (``charts``, #1956) chooses the graph's panels;
a report without it draws every panel the campaign has, as the digest did,
byte for byte.
"""

from html import escape
from io import BytesIO

from parishkit.email.base import InlineImage
from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.digest_content import CHART_ID, EMAIL_CHART_WIDTH

from ..charts import (
    CHARTS,
    CUMULATIVE_FAMILIES,
    FIRST_SUBMISSIONS,
    PLEDGES,
    render_participation,
)
from ..daily_digest import report_day_cards
from .markup import style
from .registry import STATUS, Block, Section

# The chart each of ``report_day_cards``' figures belongs to, in its order:
# participation so far, the day's first submissions, then the pledges (only
# with financial stewardship, so the cards may stop short of it).
CARD_CHARTS = (CUMULATIVE_FAMILIES, FIRST_SUBMISSIONS, PLEDGES)


def shown_charts(document, options):
    """The chosen charts the campaign has, in their order (spec, the block).

    Pledges are drawn only with financial stewardship. When nothing chosen
    remains (only pledges, and financial stewardship turned off since), the
    block draws every chart the campaign has, so a report never loses its
    chart: a report's other images travel only beside it.
    """
    available = [
        key
        for key in CHARTS
        if key != PLEDGES or document.participation.financial_enabled
    ]
    return [key for key in available if key in options["charts"]] or available


def chart_alt(document, charts=CHARTS):
    """Alt text that carries the chart's key numbers, not a picture description.

    Only the figures of the ``charts`` drawn are named.
    """
    chart = document.participation
    totals = " ".join(
        f"{label}: {value}."
        for key, (label, value) in zip(
            CARD_CHARTS, report_day_cards(document), strict=False
        )
        if key in charts
    )
    return (
        f"Daily participation chart, {format_date(chart.first_date)} to "
        f"{format_date(chart.last_date)}. {totals} Exact values follow in the "
        "table."
    )


def draw(document, frame, options):
    """The heading and the chart image; the PNG travels as an inline image."""
    charts = shown_charts(document, options)
    # Match weekly display normalization; retained observations remain exact.
    # The strict HTML compiler boundary rejects NBSP parser rewrites.
    alt = " ".join(chart_alt(document, charts).split())
    html = f"<h2{style('heading')}>Daily participation</h2>"
    html += (
        f'<img src="cid:{CHART_ID}" alt="{escape(alt, quote=True)}" '
        f'width="{EMAIL_CHART_WIDTH}"'
        f"{style('image')}>"
    )
    stream = BytesIO()
    render_participation(
        document.participation, stream, format="png", email=True, charts=charts
    )
    return Section(html, "", (InlineImage(stream.getvalue(), CHART_ID),))


def capture(document, options):
    """The charted dates, the charts drawn and the alt text's figures.

    The image is kept beside them.
    """
    chart = document.participation
    charts = shown_charts(document, options)
    return {
        "first_date": chart.first_date.isoformat(),
        "last_date": chart.last_date.isoformat(),
        "charts": charts,
        "alt": " ".join(chart_alt(document, charts).split()),
    }


def check(options):
    """Refuse a "which charts" choice that is empty, unknown or out of order.

    The stored list is canonical: distinct known keys in the panels' order.
    The editor and the command line both store every chart the campaign
    offers as no choice (``report_email_editor.stored_charts``), so two
    reports with the same choice store the same value. Whether the
    campaign has financial stewardship for pledges is the editor's check
    (``report_email_editor.value_problems``), since a campaign can turn it
    off after a report chose pledges.
    """
    charts = options.get("charts", list(CHARTS))
    if (
        type(charts) is not list
        or not charts
        or charts != [key for key in CHARTS if key in charts]
    ):
        raise ValueError("Choose at least one known chart, each once, in order.")


def copy(data, frame):
    """The participation graph, drawn by the chart engine from the pinned facts.

    The snapshot keeps the chart's dates and figures only as alt text; its
    pinned fact generation (``CopyFrame.participation``) holds every day,
    and is kept as long as the snapshot is, so a copy without it is broken.
    Only the charts the email drew are drawn: a snapshot records them under
    ``charts`` once the block can choose them (#1956); one without the key,
    every snapshot before that, drew every chart.
    """
    from parishkit.stewardship.storage import StorageInvariantError

    from ..chart_specs import participation_chart

    if frame.participation is None:
        raise StorageInvariantError("The saved copy's pinned facts are missing.")
    return {"chart": participation_chart(frame.participation, data.get("charts"))}


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
    capture=capture,
    copy=copy,
    empty="No campaign day has ended yet.",
    options={"charts": list(CHARTS)},
    check=check,
)
