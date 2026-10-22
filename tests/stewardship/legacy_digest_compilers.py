"""Frozen copies of the Daily and Weekly digest compilers before #1786.

The report emails rework (report-emails spec, "Delivery plan", item 2)
moved the digest content into the block registry
(``reports/report_blocks``) and one compiler. This module keeps the code
those compilers had, verbatim apart from the two entry points' names, the
imports and formatting, as the oracle the gating tests compare the new compiler with
byte for byte (``test_report_blocks``). It is test code only: nothing in
the package imports it. Remove it with the gating tests once the converted
reports have shipped (report-emails spec, "Gating tests").
"""

from dataclasses import replace
from datetime import timedelta
from html import escape
from io import BytesIO
from zoneinfo import ZoneInfo

from parishkit.email.base import InlineImage
from parishkit.stewardship.reports.charts import render_participation
from parishkit.stewardship.reports.daily_digest import (
    DASHBOARD_LABEL,
    FIRST_LABEL,
    PLEDGE_HEADING,
    RESPONDED_LABEL,
    DailyDigestContent,
    DailyDigestDocument,
    comparison_url,
    participation_row,
    report_day_cards,
    response_dashboard_url,
)
from parishkit.stewardship.reports.digest_comparison import (
    render_images,
    section_html,
    section_text,
    withheld,
)
from parishkit.stewardship.reports.digest_funnel import (
    TESTING_NOTE,
    funnel_text,
    stage_rows,
)
from parishkit.stewardship.reports.links import report_url
from parishkit.stewardship.reports.weekly_digest import (
    WeeklyDigestContent,
    WeeklyDigestDocument,
    WeeklyInformation,
    shorten,
)
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.dates import format_date, format_local
from parishkit.stewardship.web.digest_content import CHART_ID, EMAIL_CHART_WIDTH
from parishkit.stewardship.web.report_markup import BLUE, STYLES, TRACK, button
from parishkit.stewardship.web.weekly_digest_content import validate_weekly_body

# The day-by-day table shows at least this many of the latest campaign days
# (five since #1532).
TABLE_DAYS = 5


def digest_rows(document):
    """Show the last five days and every day in a missed range, never a page.

    A recovery digest's covered range can be longer than that; all of it is
    shown. The chart above the table always has the whole campaign.
    """
    chart = document.participation
    first = min(
        document.covered_dates[0], chart.last_date - timedelta(days=TABLE_DAYS - 1)
    )
    return tuple(
        participation_row(day, financial_enabled=chart.financial_enabled)
        for day in chart.days
        if day.local_date >= first
    )


def _style(name):
    """One compiler-owned inline style attribute from the validator's closed set."""
    return f' style="{STYLES[name]}"'


def _segment(width, colour):
    """One coloured cell of a bar; ``&nbsp;`` keeps Outlook from collapsing it."""
    return f'<td width="{width}%" bgcolor="{colour}"{_style("segment")}>&nbsp;</td>'


def bar(part, whole):
    """A progress bar of table cells: a filled share and a grey remainder.

    Table cells with ``bgcolor`` and percentage widths are the one bar that
    every mail program draws, Outlook for Windows included, and it survives
    image blocking. A non-zero share narrower than 1% still draws a 1% sliver,
    so it never looks like zero. Returns "" when there is nothing to compare
    against, so an unavailable figure never draws as an empty bar.
    """
    if not whole or part is None:
        return ""
    # A non-zero share keeps a 1% sliver; an incomplete one never looks full.
    filled = round(100 * part / whole)
    if part:
        filled = max(1, filled if part >= whole else min(99, filled))
    cells = _segment(filled, BLUE) if filled else ""
    cells += _segment(100 - filled, TRACK) if filled < 100 else ""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("track")}><tbody><tr>{cells}</tr></tbody></table>'
    )


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


def funnel_html(document, plain):
    """The response funnel section of the email, or "" when there is none.

    One row per stage (no charts since #1370): its short values fit a phone
    on one row, unlike the campaign totals (#1564). Each row has
    the stage (with its note), a bar of its share of email invites sent, and
    its count and share. The figures the dashboard reports beside the funnel
    are left out (#1532). A Testing digest says where the funnel is.
    """
    if document.funnel is None:
        if document.mode == "testing":
            return f"<p{_style('caption')}>{plain(TESTING_NOTE)}</p>"
        return ""
    metrics = document.funnel
    invited = metrics.stage("invited")
    rows = [
        (f"{label} ({note.lower()})" if note else label, count, invited, portion)
        for label, count, portion, note in stage_rows(metrics)
    ]
    html = f"<h2{_style('heading')}>Response funnel</h2>"
    html += (
        '<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" border="0"{_style("rows")}><tbody>'
    )
    html += "".join(
        f'<tr><td width="34%"{_style("label")}>{plain(label)}</td>'
        f'<td width="40%"{_style("bar")}>{bar(count, whole)}</td>'
        f'<td width="26%" align="right"{_style("value")}>'
        f"<strong>{plain(f'{count:,}')}</strong>{plain(f' ({portion})')}"
        "</td></tr>"
        for label, count, whole, portion in rows
    )
    return html + "</tbody></table>"


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


def legacy_render_daily_digest(document, *, public_origin, charts=True):
    """Compile an accessible image/text report using the existing chart renderer.

    Every data-dependent HTML value is escaped; the only image and link markup
    comes from this compiler. The source document is reused without recapturing
    current state. A later owner may prepend sanitized parish-authored prose
    and the mandatory Testing banner, but cannot replace these required facts.

    ``charts`` False withholds every server-drawn chart (#477): through
    ``digest_comparison.withheld``, the comparison section's. The
    participation chart, drawn in process, always stays.
    """
    if not isinstance(document, DailyDigestDocument):
        raise TypeError("Daily digest rendering requires an immutable document.")
    if not charts:
        document = replace(document, comparison=withheld(document.comparison))
    # Pin the snapshot configuration's style, not the worker's active one.
    with dates.using(document.date_format):
        return _render_daily_digest(
            document, public_origin=public_origin, charts=charts
        )


def day_table_html(headings, rows, plain):
    """The "Day by day" table: one row per day, its date first (#1844).

    The date is a row heading that never wraps, so on a desktop each day is
    one line. Four columns need about 380px, wider than a 320px phone leaves
    inside the email's card, and mail programs cannot scroll a table
    (#1559). So each day also has a date row across the value columns,
    hidden inline; a mail program that keeps the email's <style> block shows
    it on a phone and hides the date column instead (``pk-narrow`` and
    ``pk-wide``). In every mail program that honours inline styles only
    one of the two is displayed, so a screen reader hears each date once.
    ``plain`` escapes a text node.
    """
    span = len(headings) - 1

    def cells(tag, items, style, extra=""):
        """One row of right-aligned value cells, padded from the one before."""
        return "".join(
            f'<{tag}{extra} align="right"{_style(style)}>{plain(item)}</{tag}>'
            for item in items
        )

    html = (
        # No role="presentation": this is a data table, and that role would strip
        # the header semantics from the table and every cell (#1559 review).
        '<table width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("table")}><caption{_style("table-caption")}>'
        "Day by day</caption><thead><tr>"
        f'<th scope="col" align="left" class="pk-wide"{_style("th-lead")}>'
        f"{plain(headings[0])}</th>"
        + cells("th", headings[1:], "th", ' scope="col"')
        + "</tr></thead>"
    )
    for day, *figures in rows:
        html += (
            f'<tbody><tr class="pk-narrow"{_style("day-row")}>'
            f'<th scope="rowgroup" colspan="{span}" align="left"'
            f"{_style('day-narrow')}>{plain(day)}</th></tr>"
            f'<tr><th scope="row" align="left" class="pk-wide"'
            f"{_style('day-lead')}>{plain(day)}</th>"
            f"{cells('td', figures, 'td')}</tr></tbody>"
        )
    return html + "</table>"


def unbroken(text):
    """Escape a value so it wraps only before a parenthesis (#1564).

    "1,234 out of 2,345 (52.6%)" may break before "(52.6%)" and nowhere else:
    ``&nbsp;`` keeps the other words together in every mail program, where
    ``white-space`` on part of a cell is not reliable.
    """
    parts = escape(text, quote=False).split(" (")
    return " (".join(part.replace(" ", "&nbsp;") for part in parts)


def totals_html(totals, plain):
    """The "Campaign totals" rows: label, bar and value on one line (#1844).

    Each figure's label cell (it never wraps) sits beside one cell holding
    a small table of its bar and exact value, so on a desktop every figure
    is one line, as in v1.18.0. One row needs about 420px with four-digit
    counts, wider than a 320px phone leaves inside the email's card (#1564).
    A mail program that keeps the email's <style> block stacks the two cells
    there (``pk-stack``): the label above, the bar and value below, the
    value wrapping only before its parenthesis (``pk-wrap``, unbroken).
    ``plain`` escapes a text node.
    """
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("rows")}><tbody>'
    )
    html += "".join(
        f'<tr><td width="34%" class="pk-stack"{_style("total-name")}>'
        f"{plain(label)}</td>"
        f'<td width="66%" class="pk-stack"{_style("total-figure")}>'
        '<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" border="0"{_style("track")}><tbody><tr>'
        f'<td width="60%"{_style("bar")}>{bar_html}</td>'
        f'<td width="40%" align="right" class="pk-wrap"{_style("value")}>'
        f"<strong>{unbroken(value)}</strong>{unbroken(note)}</td>"
        "</tr></tbody></table></td></tr>"
        for label, value, bar_html, note in totals
    )
    return html + "</tbody></table>"


def _render_daily_digest(document, *, public_origin, charts=True):
    """Compile the report body; the caller has pinned the parish date format.

    Visuals come first (#720): the opening line with a Response dashboard
    link beside it (#1532), then the campaign totals with their bars,
    the chart, the day-by-day table and a Response dashboard button.
    The subject names the parish, campaign and report day (see
    render_digest_envelope), so the body has no header or small print. Every
    value is escaped; every attribute comes from this compiler and the
    validator's closed set.
    """
    chart = document.participation
    url = response_dashboard_url(public_origin, document.mode)
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append(PLEDGE_HEADING)
    totals = report_day_bars(document)
    rows = digest_rows(document)
    # Say each fact once (#720): the opening line carries the report-day
    # definition (#721) and is the only place that names the time zone. A
    # recovery digest says which days it covers there. The ParishSoft
    # connection line is on the saved report page, not in the email.
    # Match weekly display normalization; retained observations remain exact.
    # The strict HTML compiler boundary rejects NBSP parser rewrites.
    opening, alt = (
        " ".join(label.split()) for label in (document.opening, chart_alt(document))
    )
    link_line = f"{DASHBOARD_LABEL} (staff login required): {url}"
    text = f"{opening}\n{link_line}"
    text += "\n\n" + "\n".join(f"{label}: {value}" for label, value, _b, _n in totals)
    if document.funnel is not None:
        text += "\n\n" + funnel_text(document.funnel)
    elif document.mode == "testing":
        text += "\n\n" + TESTING_NOTE
    text += "\n\n" + " | ".join(headings)
    text += "\n" + "\n".join(" | ".join(row) for row in rows)
    text += "\n\n" + link_line
    section = document.comparison
    if section is not None:
        link = comparison_url(section, public_origin)
        text += "\n\n" + section_text(section, link)

    def plain(value):
        """Escape a text node; canonical HTML leaves quotes literal there."""
        return escape(value, quote=False)

    # Prominent (#1016): body size and colour, not the muted caption style.
    # The dashboard link sits right-aligned on the same line (#1532), in a
    # two-cell presentation table: floats and flexbox are unreliable in
    # Outlook and Gmail. The link cell is 30% wide, top-aligned and wraps into
    # short lines, so the sentence keeps most of a phone's width.
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("track")}><tbody><tr>'
        f"<td{_style('opening')}>{plain(opening)}</td>"
        f'<td width="30%" align="right"{_style("opening-link")}>'
        f'<a href="{escape(url, quote=True)}"{_style("link")} '
        f'rel="noopener noreferrer">{plain(DASHBOARD_LABEL)}</a></td>'
        "</tr></tbody></table>"
    )
    html += f"<h2{_style('heading')}>Campaign totals</h2>"
    html += totals_html(totals, plain)
    html += funnel_html(document, plain)
    html += f"<h2{_style('heading')}>Daily participation</h2>"
    html += (
        f'<img src="cid:{CHART_ID}" alt="{escape(alt, quote=True)}" '
        f'width="{EMAIL_CHART_WIDTH}"'
        f"{_style('image')}>"
    )
    html += day_table_html(headings, rows, plain)
    html += button(url, DASHBOARD_LABEL)
    images = ()
    if section is not None:
        # The section follows the report it compares (#1018 slice 7), with
        # its charts from the chart engine's server renderer.
        html += section_html(section, link)
        images = tuple(InlineImage(data, name) for name, data in render_images(section))
    stream = BytesIO()
    render_participation(chart, stream, format="png", email=True)
    return DailyDigestContent(
        document.title,
        html,
        text,
        InlineImage(stream.getvalue(), CHART_ID),
        images,
        charts_withheld=not charts,
    )


# A full-width presentational table, as the report layout builds them (#720).
TABLE = ' role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'


def legacy_render_weekly_digest(document, *, public_origin):
    """Escape every Family value and retain every selected row, or fail explicitly."""
    if not isinstance(document, WeeklyDigestDocument):
        raise TypeError("Weekly digest rendering requires an immutable document.")
    if document.empty:
        raise ValueError("An empty weekly interval must not create an email.")
    # Pin the snapshot configuration's style, not the worker's active one.
    with dates.using(document.date_format):
        return _render_weekly_digest(document, public_origin=public_origin)


def _render_weekly_digest(document, *, public_origin):
    """Compile the email body; the caller has pinned the parish date format."""
    zone = ZoneInfo(document.campaign_timezone)
    observed = document.observed_at.astimezone(zone)
    title = (
        "Manual weekly" if document.manual else "Weekly"
    ) + f" information digest — {format_date(observed.date())}"
    url = report_url(public_origin, document.report_path)
    # The Administrator asked (#720) for the email to open straight into the
    # numbered requests: the subject names the parish, campaign, report and
    # capture date (see render_digest_envelope), so the body repeats none of
    # them and has no explanatory small print. Each section's heading
    # carries its count; rows give submitted times without a zone.
    new = "current" if document.manual else "new"
    sections = (
        (
            f"{len(document.information):,} {new} actionable request"
            + ("" if len(document.information) == 1 else "s"),
            document.information,
        ),
        (
            f"{len(document.corrections):,} correction"
            + ("" if len(document.corrections) == 1 else "s")
            + " to previously reported requests",
            document.corrections,
        ),
    )
    text = ""

    def plain(value):
        """Escape a text node; canonical HTML leaves quotes literal there."""
        return escape(value, quote=False)

    html = ""
    for heading, rows in sections:
        if not rows:
            continue
        html += f"<h2{_style('heading')}>{heading}</h2>"
        html += f"<table{TABLE}{_style('table')}><tbody>"
        text += ("\n\n" if text else "") + heading
        numbered = rows is document.information
        for number, row in enumerate(rows, start=1):
            # Imported names can contain NBSP or CR; collapse display-only
            # whitespace so the strict HTML serializer agrees.
            family = " ".join(
                f"{row.family_name} — Family DUID {row.family_duid}".split()
            )
            submitted = "Submitted " + format_local(
                row.submitted_at.astimezone(zone), compact=True
            )
            if isinstance(row, WeeklyInformation):
                detail, shortened = shorten(row.text)
            else:
                detail = (
                    f"{row.disposition.title()}: the previously reported request "
                    "is no longer actionable."
                )
                shortened = False
            item_url = escape(url + f"items/{row.item_id}/", quote=True)
            link = f'<a href="{item_url}"{_style("link")} rel="noopener noreferrer">'
            # Numbers are table cells, not list markers, so every mail program
            # shows the same stable 1, 2, 3 a reader can refer back to.
            label = f"{number}." if numbered else ""
            html += (
                f'<tr><td width="4%"{_style("number")}>{label}</td>'
                f'<td width="32%"{_style("who")}>{link}{plain(family)}</a><br>'
                f"{plain(submitted)}</td>"
                f'<td width="64%"{_style("excerpt")}>{plain(detail)}'
                + (f" {link}Read the full request</a>" if shortened else "")
                + "</td></tr>"
            )
            prefix = f"{number}. " if numbered else ""
            text += f"\n\n{prefix}{family}. {submitted}\n{detail}"
            link_label = "Read the full request: " if shortened else ""
            text += "\n" + link_label + url + f"items/{row.item_id}/"
        html += "</tbody></table>"
    # The response funnel (#477) follows the requests, which the weekly
    # report exists for, and uses the daily report's rows and bars, so the
    # two emails draw it alike. A Testing report says where it is instead.
    html += funnel_html(document, plain)
    if document.funnel is not None:
        text += "\n\n" + funnel_text(document.funnel)
    elif document.mode == "testing":
        text += "\n\n" + TESTING_NOTE
    html += button(url, "Open protected report")
    text += "\n\nOpen protected report (staff login required): " + url
    validate_weekly_body(html, text)
    return WeeklyDigestContent(title, html, text)
