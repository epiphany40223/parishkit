"""The daily digest's Compared with past campaigns section (#1018, slice 7).

The Administrator turns it on for the daily digest schedule (its
``comparison`` value, on Dates and mail schedules); it is off by default and
never part of the weekly digest. When the digest is compiled the worker reads
the campaign comparison on the digest's report day from the digest's own
pinned facts (``comparison_reads.digest_comparison``) and reduces it at once
to the **section**: a JSON document holding only what may leave the Admin
portal (campaign-history spec, "Digest section"):

- the headline figures as their tiles' text outside the portal
  (``comparison.tiles(..., outside=True)``): the small-count protection
  applied (a withheld year's average pledge, and any prior-years average
  pledge that counts one, read "Withheld"), and no median so far;
- the cumulative Families and cumulative pledges day-by-day charts, each as
  the chart engine's Vega-Lite specification with its summary and
  exact-values table (``comparison_charts.day_chart``): day totals only, no
  pledge distribution, quartile or band;
- each imported year's file checksum, which records what was compared;
- never an archive's note, provenance or dedup text.

The section is stored with the digest's ready row with the two charts'
PNGs, so the email and the saved digest page show the same figures however
the imports change later. The PNGs come from the chart engine's server
renderer, under the timeout name ``DIGEST_CHART`` (a kill leaves a durable
``helper_timed_out`` entry naming it and ``chart_render_worker``).

Nothing here reads the database or the clock except ``render_images``,
which starts the renderer's helper process.
"""

from html import escape

from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.digest_content import (
    COMPARISON_CHART_IDS,
    EMAIL_CHART_WIDTH,
    MAX_ALT_CHARACTERS,
)
from parishkit.stewardship.web.presentation import number
from parishkit.stewardship.web.report_markup import STYLES

HEADING = "Compared with past campaigns"
# The chart engine server renderer's reviewed timeout name for this work
# (reports spec, "Chart engine"); audit.schemas.TIMEOUT_KINDS and the SQL
# allowlist (frozen file 0072) admit it.
DIGEST_CHART = "digest_chart"
# The two day-by-day measures the section draws, in its order; the pledges
# chart only with Financial stewardship.
MEASURES = ("families", "pledged")
LINK_LABEL = "Open the Campaign comparison report"
# The closed failure word (audit.schemas.FAILURES) naming the section's
# charts when one cannot be drawn.
CHART_FAILURE = "digest_comparison_chart"


def section(comparison):
    """The protected section document for ``comparison``, or None.

    None when there is nothing to compare: no comparison day yet, or no
    imported year (the section is left out). Every value is an aggregate or
    a fixed word; nothing names a Family.
    """
    from .comparison import tiles
    from .comparison_charts import MEASURES as CHART_MEASURES
    from .comparison_charts import day_chart
    from .comparison_exports import compared_with

    if comparison is None or not comparison.imported or comparison.day is None:
        return None
    charts = []
    for measure in CHART_MEASURES:
        if measure.key not in MEASURES or (
            measure.financial and not comparison.financial
        ):
            continue
        chart = day_chart(measure, comparison)
        if chart is None:
            continue
        charts.append(
            {
                "key": chart.key,
                "measure": measure.key,
                "title": chart.title,
                "summary": chart.summary,
                "spec": chart.spec,
                "table": {
                    "headings": list(chart.table.headings),
                    "rows": [list(row) for row in chart.table.rows],
                },
                "notes": list(chart.notes),
            }
        )
    if not charts:
        return None
    return {
        "campaign": comparison.campaign_name,
        "day": comparison.day,
        "report_date": comparison.report_date.isoformat(),
        "compared_with": compared_with(comparison),
        "financial": comparison.financial,
        "tiles": [
            {"label": tile["label"], "value": tile["value"], "lines": tile["lines"]}
            for tile in tiles(comparison, outside=True)
        ],
        "charts": charts,
        "archives": [
            {"year": year, "checksum": checksum}
            for year, checksum in comparison.checksums
        ],
    }


def data_line(document):
    """ "Day 12 of Stewardship 2027 · through October 14 · compared with …"."""
    from datetime import date

    through = format_date(date.fromisoformat(document["report_date"]))
    day, campaign = number(document["day"]), document["campaign"]
    line = f"Day {day} of {campaign} · through {through}"
    if document["compared_with"]:
        line += f" · compared with {document['compared_with']}"
    return line


def content_id(chart):
    """The email's inline image id of one section chart."""
    return COMPARISON_CHART_IDS[MEASURES.index(chart["measure"])]


def email_spec(chart):
    """The chart's specification at the email image's width.

    The stored specification follows its panel's width in a browser; the
    email image is the participation chart's width.
    """
    return chart["spec"] | {"width": int(EMAIL_CHART_WIDTH)}


def alt_text(chart):
    """The chart's summary, which carries its figures, cut to the alt limit."""
    text = " ".join(chart["summary"].split())
    if len(text) > MAX_ALT_CHARACTERS:
        text = text[: MAX_ALT_CHARACTERS - 1] + "…"
    return text


def render_images(document):
    """Each section chart as (content id, PNG bytes), in the email's order.

    Starts the chart engine's server renderer once per chart; a kill at its
    time limit is logged under ``DIGEST_CHART``. A failure raises
    ``ChartRenderError`` naming the chart (``CHART_FAILURE``), which fails
    this compile so it is retried; the last attempt sends the digest with
    the section's figures but without its charts (``withheld``).
    """
    from .chart_rendering import render_digest_chart

    return tuple(
        (
            content_id(chart),
            render_digest_chart(
                email_spec(chart), what=DIGEST_CHART, chart=CHART_FAILURE
            ),
        )
        for chart in document["charts"]
    )


def withheld(document):
    """The section without its charts, or None when there is no section.

    Its figures, data line and link stay; the email and the saved page then
    show no chart for it (#477: a digest is sent without charts rather than
    not at all).
    """
    return None if document is None else document | {"charts": []}


def section_text(document, url):
    """The section in the plain-text part: data line, figures, link."""
    lines = [HEADING, data_line(document), ""]
    for tile in document["tiles"]:
        lines.append(f"{tile['label']}: {tile['value']}")
        lines.extend(f"  {line}" for line in tile["lines"])
    lines += ["", f"{LINK_LABEL} (staff login required): {url}"]
    return "\n".join(lines)


def attribute(value):
    """Escape an attribute value as the boundary's sanitizer writes it.

    Only ``&``, ``<``, ``>`` and the double quote are escaped; an apostrophe
    stays literal, so the compiled body equals its sanitized form.
    """
    return escape(value, quote=False).replace('"', "&quot;")


def _style(name):
    """One compiler-owned inline style attribute from the validator's closed set."""
    return f' style="{STYLES[name]}"'


def section_html(document, url):
    """The section's email markup, charts first (#720 style).

    The heading and data line, the charts (each image's alt text carrying
    its figures), then one row per headline figure with its last-year and
    average comparisons, then the link to the report on the same day. Every
    value is escaped; every attribute is the validator's closed set.
    """

    def plain(value):
        """Escape a text node; canonical HTML leaves quotes literal there."""
        return escape(value, quote=False)

    html = f"<h2{_style('heading')}>{plain(HEADING)}</h2>"
    html += f"<p{_style('caption')}>{plain(data_line(document))}</p>"
    for chart in document["charts"]:
        html += f"<p{_style('caption')}>{plain(chart['title'])}</p>"
        html += (
            f'<img src="cid:{content_id(chart)}" '
            f'alt="{attribute(alt_text(chart))}" '
            f'width="{EMAIL_CHART_WIDTH}"{_style("image")}>'
        )
    html += (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("rows")}><tbody>'
    )
    for tile in document["tiles"]:
        lines = "".join(f"<br>{plain(line)}" for line in tile["lines"])
        html += (
            f'<tr><td width="34%"{_style("label")}>{plain(tile["label"])}</td>'
            f'<td width="66%"{_style("label")}><strong>{plain(tile["value"])}'
            f"</strong>{lines}</td></tr>"
        )
    html += "</tbody></table>"
    html += (
        f'<p><a href="{attribute(url)}"{_style("link")} '
        f'rel="noopener noreferrer">{plain(LINK_LABEL)}</a></p>'
    )
    return html
