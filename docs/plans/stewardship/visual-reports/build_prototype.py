"""Render the visual daily-digest design prototype (#720) from fixture data.

This is a design aid, not product code: it shows the proposed email layout
from "Visual report design" in docs/specs/stewardship/reports/spec.md so the
Administrator can judge it before the daily digest is rebuilt. It builds the
same immutable report types the digest uses (``ParticipationDocument`` and
``PopulationStatistics``) from a realistic, entirely fictional campaign,
renders the daily-responses chart with vl-convert (the chart engine's server
renderer), writes the email HTML with every style inline, and takes
Playwright screenshots at desktop and phone widths, plus one with images
blocked, as many mail programs do by default.

Run with an environment that has ParishKit, vl-convert and Playwright's
Chromium installed (from the repository root)::

    PYTHONPATH=src python \\
        docs/plans/stewardship/visual-reports/build_prototype.py
"""

import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
from uuid import UUID

import django
import vl_convert

# The report types live beside Django models; the unit-test settings load
# them without a database.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "parishkit.stewardship.settings.test")
django.setup()

from parishkit.stewardship.reports.money import MoneyAmount  # noqa: E402
from parishkit.stewardship.reports.participation import (  # noqa: E402
    ParticipationDay,
    ParticipationDocument,
)
from parishkit.stewardship.reports.statistics import PopulationStatistics  # noqa: E402
from parishkit.stewardship.web.dates import format_date, format_instant  # noqa: E402

HERE = Path(__file__).resolve().parent
ZONE = "America/New_York"
# The shared palette (see the specification): validated colour-blind safe.
BLUE, ORANGE, TEAL = "#1f6fae", "#b8620a", "#1a9a8a"
TRACK, INK, MUTED, RULE = "#e4e7ec", "#1f2933", "#52606d", "#d0d5dd"
FONT = "Arial, Helvetica, sans-serif"
WIDTH = 960  # desktop email width in CSS pixels
CHART_WIDTH = 880  # chart image's CSS width inside the 40px side padding

# Fictional first responses per day: a spike after each send, then a decay.
DAILY = [
    41, 64, 37, 22, 15, 11, 9, 7, 12, 8, 6, 49, 30, 17, 10, 8, 6, 5, 38, 24,
    15, 11, 18,
]  # fmt: skip
SENDS = {0: "Invitation", 11: "Reminder 1", 18: "Reminder 2"}
FIRST = date(2026, 9, 14)


def fixture():
    """Return a realistic campaign: chart document, statistics and funnel."""
    observed = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
    days, total, pledged = [], 0, Decimal(0)
    for index, count in enumerate(DAILY):
        total += count
        # About $1,420 a year per responding Family, as cumulative cents.
        pledged += Decimal(count * 1423) + Decimal("17.25")
        days.append(
            ParticipationDay(
                local_date=FIRST + timedelta(days=index),
                first_responses=count,
                cumulative_responses=total,
                cohort_denominator=1240,
                source_generation=index + 1,
                source_as_of=observed - timedelta(days=len(DAILY) - index),
                population_available=True,
                pledge_available=True,
                pledge_total=pledged,
            )
        )
    chart = ParticipationDocument(
        campaign_id=UUID(int=1),
        fact_set_id=UUID(int=2),
        parish_name="Example Parish",
        campaign_name="2027 Stewardship Campaign",
        population_scope="historical",
        campaign_timezone=ZONE,
        browser_timezone=ZONE,
        source_generation=len(DAILY),
        source_as_of=observed - timedelta(hours=2),
        submission_watermark=4321,
        requested_at=observed,
        first_date=FIRST,
        last_date=days[-1].local_date,
        financial_enabled=True,
        days=tuple(days),
    )
    active = PopulationStatistics(
        families=1240,
        active_members=3052,
        eligible_email=1081,
        deliverable_email=1032,
        responses=total,
        annual_pledge=MoneyAmount(int(pledged * 100)),
        comparison_pledge=MoneyAmount(142_000_000),
    )
    # Funnel stages as the response dashboard counts them (aggregates only).
    funnel = (
        ("Emailed", 1032),
        ("Opened their link", 803),
        ("Started the form", 655),
        ("Submitted", total),
    )
    return chart, active, funnel, observed


def chart_spec(chart, comparison):
    """Build the one Vega-Lite figure: three stacked panels on one date axis.

    One measure per panel, so no panel needs a second y-axis; the shared
    x-axis lines the panels up so a reminder's effect reads straight down.
    """
    rows = [
        {
            "date": day.local_date.isoformat(),
            "new": day.first_responses,
            "total": day.cumulative_responses,
            "pledged": float(day.pledge_total),
        }
        for day in chart.days
    ]
    sends = [
        {"date": (FIRST + timedelta(days=i)).isoformat(), "send": label}
        for i, label in SENDS.items()
    ]
    x = {
        "field": "date",
        "type": "temporal",
        "timeUnit": "yearmonthdate",
        "title": None,
        "axis": {"format": "%b %-d", "labelAngle": 0, "tickCount": 8},
    }
    # Bars fill each day's band; lines and labels sit at the band's middle.
    mid = x | {"bandPosition": 0.5}
    width = CHART_WIDTH - 90

    def panel(title, layers, height):
        """One titled panel sharing the date axis and the quiet style."""
        return {"title": title, "width": width, "height": height, "layer": layers}

    def markers(with_labels):
        """Dashed rules (and labels on the top panel) at each scheduled send."""
        layers = [
            {
                "data": {"values": sends},
                "mark": {"type": "rule", "color": "#7b8794", "strokeDash": [4, 3]},
                "encoding": {"x": x},
            }
        ]
        if with_labels:
            layers.append(
                {
                    "data": {"values": sends},
                    "mark": {
                        "type": "text",
                        "align": "left",
                        "dx": 4,
                        "y": -4,
                        "baseline": "bottom",
                        "color": MUTED,
                        "fontSize": 11,
                    },
                    "encoding": {"x": x, "text": {"field": "send"}},
                }
            )
        return layers

    rows[-1]["latest"] = True  # the latest day gets a direct label
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "data": {"values": rows},
        "config": {
            "font": "Helvetica",
            "view": {"stroke": None},
            "axis": {
                "labelColor": MUTED,
                "titleColor": MUTED,
                "domainColor": RULE,
                "tickColor": RULE,
                "gridColor": "#eef0f3",
                "labelFontSize": 12,
            },
            # Equal y-axis gutters keep the three panels' dates lined up.
            "axisY": {"minExtent": 52},
            "title": {
                "anchor": "start",
                "fontSize": 14,
                "fontWeight": "bold",
                "color": INK,
                "offset": 20,
            },
        },
        "spacing": 30,
        "resolve": {"scale": {"x": "shared"}},
        "vconcat": [
            panel(
                "New responses each day",
                [
                    *markers(True),
                    {
                        "mark": {
                            "type": "bar",
                            "color": BLUE,
                            "cornerRadiusEnd": 2,
                            "width": {"band": 0.75},
                        },
                        "encoding": {
                            "x": x,
                            "y": {
                                "field": "new",
                                "type": "quantitative",
                                "title": None,
                                "axis": {"tickCount": 4},
                            },
                        },
                    },
                    {
                        "transform": [{"filter": "datum.latest"}],
                        "mark": {
                            "type": "text",
                            "dy": -8,
                            "color": INK,
                            "fontWeight": "bold",
                        },
                        "encoding": {
                            "x": mid,
                            "y": {"field": "new", "type": "quantitative"},
                            "text": {"field": "new"},
                        },
                    },
                ],
                150,
            ),
            panel(
                "Families who have responded so far",
                [
                    *markers(False),
                    {
                        "mark": {
                            "type": "line",
                            "color": BLUE,
                            "strokeWidth": 2,
                        },
                        "encoding": {
                            "x": mid,
                            "y": {
                                "field": "total",
                                "type": "quantitative",
                                "title": None,
                                "axis": {"tickCount": 4},
                            },
                        },
                    },
                ],
                120,
            ),
            panel(
                "Annual pledges so far",
                [
                    *markers(False),
                    {
                        "data": {"values": [{"goal": comparison}]},
                        "mark": {"type": "rule", "color": ORANGE, "strokeDash": [6, 4]},
                        "encoding": {"y": {"field": "goal", "type": "quantitative"}},
                    },
                    {
                        "data": {"values": [{"goal": comparison}]},
                        "mark": {
                            "type": "text",
                            "align": "left",
                            "x": 4,
                            "dy": -8,
                            "color": MUTED,
                            "fontSize": 11,
                            "text": "Last year's pledges",
                        },
                        "encoding": {"y": {"field": "goal", "type": "quantitative"}},
                    },
                    {
                        "mark": {"type": "line", "color": ORANGE, "strokeWidth": 2},
                        "encoding": {
                            "x": mid,
                            "y": {
                                "field": "pledged",
                                "type": "quantitative",
                                "title": None,
                                "axis": {"format": "$,.2~s", "tickCount": 4},
                            },
                        },
                    },
                ],
                110,
            ),
        ],
    }


def chart_alt(chart, active):
    """Alt text that carries the chart's key numbers, per the alt-text rules."""
    days = chart.days
    peak = max(days, key=lambda day: day.first_responses)
    last = days[-1]
    return (
        f"Daily responses chart, {format_date(chart.first_date)} to "
        f"{format_date(chart.last_date)}. {last.first_responses} new responses on "
        f"{format_date(last.local_date)}; {last.cumulative_responses:,} of "
        f"{active.families:,} Families have responded. Busiest day: "
        f"{format_date(peak.local_date)}, {peak.first_responses} responses. "
        f"Pledges so far: {money(active.annual_pledge)}."
    )


def money(amount):
    """Whole-dollar display for headline figures; exact cents stay in tables."""
    return f"${amount.cents // 100:,}"


def percent(part, whole):
    """A whole-number percentage for a bar label (the table keeps exact counts)."""
    return f"{round(100 * part / whole)}%"


def bar(part, whole, color, *, height=14):
    """A table-based horizontal bar: a filled cell and a grey remainder.

    Table cells with ``bgcolor`` and percentage widths are the one bar
    construction every mail program draws, Outlook for Windows included, and
    it survives image blocking. Very small shares keep a 1% sliver so a
    non-zero value never looks like zero.
    """
    filled = max(1, min(100, round(100 * part / whole))) if part else 0
    cells = ""
    if filled:
        cells += (
            f'<td width="{filled}%" bgcolor="{color}" style="background-color:'
            f'{color};height:{height}px;line-height:{height}px;font-size:1px;">'
            "&nbsp;</td>"
        )
    if filled < 100:
        cells += (
            f'<td width="{100 - filled}%" bgcolor="{TRACK}" style="background-color:'
            f'{TRACK};height:{height}px;line-height:{height}px;font-size:1px;">'
            "&nbsp;</td>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="border-collapse:collapse;"><tr>{cells}</tr></table>'
    )


def bullet(part, target, color):
    """A bullet-graph bar: the value against a target marker past the bar.

    The scale runs to whichever is larger, value or target, plus 10%, so the
    target marker (a dark 3px cell) always shows on the track.
    """
    scale = max(part, target) * 1.1
    filled = round(100 * part / scale)
    mark = round(100 * target / scale)

    def cell(width, background):
        """One fixed-height segment of the bullet track."""
        return (
            f'<td width="{width}%" bgcolor="{background}" style="background-color:'
            f'{background};height:14px;line-height:14px;font-size:1px;">&nbsp;</td>'
        )

    cells = cell(filled, color) + cell(mark - filled - 1, TRACK)
    cells += (
        f'<td width="3" bgcolor="{INK}" style="background-color:{INK};width:3px;'
        'height:22px;font-size:1px;">&nbsp;</td>'
    )
    cells += cell(100 - mark, TRACK)
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="border-collapse:collapse;"><tr>{cells}</tr></table>'
    )


def compare(current, earlier):
    """Two small labelled bars on one scale: this week against the last."""
    scale = max(current[1], earlier[1]) or 1
    rows = ""
    for (label, count), color in ((current, BLUE), (earlier, "#8fb3d6")):
        rows += (
            f'<tr><td style="font-size:13px;color:{MUTED};padding:0 8px 4px 0;'
            f'white-space:nowrap;width:90px;">{label}</td>'
            f'<td style="padding:0 0 4px;">{bar(count, scale, color, height=10)}</td>'
            f'<td align="right" style="font-size:13px;color:{INK};padding:0 0 4px 8px;'
            f'width:36px;"><b>{count:,}</b></td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0">{rows}</table>'
    )


def tile(label, value, detail, visual):
    """One headline tile; a fluid inline block so tiles wrap on phones."""
    return (
        '<div class="pk-tile" style="display:inline-block;vertical-align:top;'
        'width:100%;max-width:272px;margin:0 0 16px;">'
        f'<div style="font-size:14px;color:{MUTED};margin:0 0 4px;">{label}</div>'
        f'<div style="font-size:34px;line-height:1.1;font-weight:bold;color:{INK};'
        f'margin:0 0 8px;">{value}</div>{visual}'
        f'<div style="font-size:14px;color:{MUTED};margin:8px 0 0;">{detail}</div>'
        "</div>"
    )


def ghost(cells, widths):
    """Wrap fluid tiles in Outlook-only table cells (the 'hybrid' technique)."""
    html = (
        '<!--[if mso]><table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0" border="0"><tr><![endif]-->'
    )
    for index, (cell, width) in enumerate(zip(cells, widths, strict=True)):
        html += f'<!--[if mso]><td width="{width}" valign="top"><![endif]-->{cell}'
        html += "<!--[if mso]></td><![endif]-->"
        if index < len(cells) - 1:
            html += (
                '<div class="pk-gap" style="display:inline-block;width:16px;">'
                "&nbsp;</div>"
            )
    return html + "<!--[if mso]></tr></table><![endif]-->"


def section(title, body):
    """A titled section separated by a hairline rule."""
    return (
        f'<tr><td style="padding:28px 40px 0;border-top:1px solid {TRACK};">'
        f'<h2 style="margin:0 0 16px;font-size:20px;line-height:1.3;color:{INK};">'
        f"{title}</h2>{body}</td></tr>"
    )


def labelled_bar(label, part, whole, color):
    """A bar row with its label left and its exact figure right."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 14px;"><tr>'
        f'<td style="font-size:15px;color:{INK};padding:0 0 4px;">{label}</td>'
        f'<td align="right" style="font-size:15px;color:{INK};padding:0 0 4px;'
        f'white-space:nowrap;"><b>{part:,}</b> of {whole:,} '
        f'<span style="color:{MUTED};">({percent(part, whole)})</span></td>'
        f'</tr><tr><td colspan="2">{bar(part, whole, color)}</td></tr></table>'
    )


def email(chart, active, funnel, observed, chart_src):
    """Compose the proposed daily digest email (all styles inline)."""
    last = chart.days[-1]
    week = chart.days[-7:]
    previous = chart.days[-14:-7]
    new_week = sum(day.first_responses for day in week)
    old_week = sum(day.first_responses for day in previous)
    pledged, comparison = active.annual_pledge.cents, active.comparison_pledge.cents
    tiles = [
        tile(
            "Families who have responded",
            f"{active.responses:,}",
            f"of {active.families:,} active Families "
            f"({percent(active.responses, active.families)})",
            bar(active.responses, active.families, BLUE),
        ),
        tile(
            f"New responses on {format_date(last.local_date)}",
            f"{last.first_responses:,}",
            "Compared with the week before",
            compare(("Last 7 days", new_week), ("7 days before", old_week)),
        ),
        tile(
            "Annual pledges so far",
            money(active.annual_pledge),
            f"{percent(pledged, comparison)} of last year's "
            f"{money(active.comparison_pledge)} (the dark marker)",
            bullet(pledged, comparison, ORANGE),
        ),
    ]
    head = (
        f'<tr><td style="padding:32px 40px 8px;background-color:#f8fafc;">'
        f'<div style="font-size:14px;color:{MUTED};">{escape(chart.parish_name)} · '
        f"{escape(chart.campaign_name)}</div>"
        f'<h1 style="margin:4px 0 6px;font-size:26px;line-height:1.25;color:{INK};">'
        f"Daily campaign report — {format_date(last.local_date)}</h1>"
        f'<div style="font-size:14px;color:{MUTED};">Counted '
        f"{format_instant(observed, ZONE)} · ParishSoft data as of "
        f"{format_instant(chart.source_as_of, ZONE)}</div></td></tr>"
        f'<tr><td style="padding:24px 40px 8px;background-color:#f8fafc;">'
        f"{ghost(tiles, (272, 272, 272))}</td></tr>"
    )
    reach = (
        labelled_bar("Have responded", active.responses, active.families, BLUE)
        + labelled_bar(
            "Can be reached by email",
            active.deliverable_email,
            active.families,
            TEAL,
        )
        + f'<p style="margin:4px 0 0;font-size:14px;color:{MUTED};">'
        f"{active.no_deliverable_email:,} Families have no working email "
        "address and get campaign mail by post.</p>"
    )
    stages = "".join(
        labelled_bar(label, count, funnel[0][1], BLUE) for label, count in funnel
    )
    figure = (
        f'<img src="{chart_src}" width="{CHART_WIDTH}" '
        f'alt="{escape(chart_alt(chart, active))}" '
        f'style="display:block;width:100%;max-width:{CHART_WIDTH}px;height:auto;'
        f'border:0;font-size:14px;line-height:1.4;color:{MUTED};">'
    )
    header_cells = "".join(
        f'<th scope="col" align="{align}" style="padding:8px 10px;font-size:13px;'
        f'color:{MUTED};border-bottom:2px solid {RULE};font-weight:bold;">{name}</th>'
        for name, align in (
            ("Date", "left"),
            ("New responses", "right"),
            (f"Families responded (of {active.families:,})", "right"),
            ("Annual pledges", "right"),
        )
    )
    body_rows = "".join(
        "<tr>"
        + "".join(
            f'<td align="{align}" style="padding:7px 10px;font-size:14px;color:{INK};'
            f'border-bottom:1px solid {TRACK};{bold}">{value}</td>'
            for value, align in (
                (format_date(day.local_date, compact=True), "left"),
                (f"{day.first_responses:,}", "right"),
                (
                    f"{day.cumulative_responses:,} "
                    f"({percent(day.cumulative_responses, day.cohort_denominator)})",
                    "right",
                ),
                (f"${day.pledge_total:,.2f}", "right"),
            )
        )
        + "</tr>"
        for day in week
        for bold in ["font-weight:bold;" if day is last else ""]
    )
    table = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;margin:20px 0 0;">'
        f'<caption style="text-align:left;font-size:14px;color:{MUTED};padding:0 0 '
        '6px;">Exact numbers for the last 7 days</caption>'
        f"<thead><tr>{header_cells}</tr></thead><tbody>{body_rows}</tbody></table>"
    )
    foot = (
        f'<tr><td style="padding:28px 40px 32px;border-top:1px solid {TRACK};">'
        f'<a href="https://campaign.example.invalid/admin/reports/daily-digests/x/" '
        f'style="display:inline-block;background-color:{BLUE};color:#ffffff;'
        "text-decoration:none;font-weight:bold;padding:12px 20px;border-radius:6px;"
        '">Open the full report</a>'
        f'<p style="margin:16px 0 0;font-size:13px;color:{MUTED};">Staff sign-in '
        "required. Counts include every active Family; Testing responses are not "
        "counted. ParishSoft connection: working. Data load "
        f"{chart.source_generation:,}, responses up to number "
        f"{chart.submission_watermark:,}.</p></td></tr>"
    )
    style = (
        "body{margin:0;padding:0;background-color:#eef1f5;}"
        "@media only screen and (max-width:700px){"
        ".pk-pad{padding-left:16px !important;padding-right:16px !important;}"
        ".pk-tile{max-width:100% !important;}.pk-gap{display:none !important;}"
        "h1{font-size:22px !important;}}"
    )
    content = (
        head
        + section("How many Families have responded", reach)
        + section("From email to response", stages)
        + section("Day by day", figure + table)
        + foot
    ).replace('<td style="padding:', '<td class="pk-pad" style="padding:')
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        "<title>Daily campaign report prototype</title>"
        f"<style>{style}</style></head>"
        f'<body style="margin:0;padding:0;background-color:#eef1f5;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="background-color:#eef1f5;"><tr>'
        '<td align="center" style="padding:24px 8px;">'
        f'<!--[if mso]><table role="presentation" width="{WIDTH}" cellpadding="0" '
        'cellspacing="0" border="0"><tr><td><![endif]-->'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="max-width:{WIDTH}px;background-color:#ffffff;'
        f"border:1px solid {TRACK};border-radius:8px;font-family:{FONT};"
        f'color:{INK};text-align:left;">{content}</table>'
        "<!--[if mso]></td></tr></table><![endif]-->"
        "</td></tr></table></body></html>"
    )


def screenshots():
    """Capture desktop, phone and images-blocked views with Playwright."""
    from playwright.sync_api import sync_playwright

    page_url = (HERE / "daily-digest.html").as_uri()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, width, block in (
            ("daily-digest-desktop.png", 1000, False),
            ("daily-digest-phone.png", 390, False),
            ("daily-digest-images-off.png", 1000, True),
        ):
            page = browser.new_page(
                viewport={"width": width, "height": 800}, device_scale_factor=1
            )
            if block:
                page.route("**/*.png", lambda route: route.abort())
            page.goto(page_url)
            page.screenshot(path=str(HERE / name), full_page=True)
            page.close()
        browser.close()


def main():
    """Write the chart, the email HTML and the screenshots beside this file."""
    chart, active, funnel, observed = fixture()
    spec = chart_spec(chart, active.comparison_pledge.cents / 100)
    (HERE / "daily-digest-chart.vl.json").write_text(json.dumps(spec, indent=1))
    png = vl_convert.vegalite_to_png(json.dumps(spec), scale=2)
    (HERE / "daily-digest-chart.png").write_bytes(png)
    html = email(chart, active, funnel, observed, "daily-digest-chart.png")
    (HERE / "daily-digest.html").write_text(html)
    screenshots()


if __name__ == "__main__":
    main()
