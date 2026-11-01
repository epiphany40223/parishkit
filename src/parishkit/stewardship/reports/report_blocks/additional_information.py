"""The Open additional information requests block (report-emails spec).

The Weekly digest's requests sections, drawn exactly as today: the
numbered actionable requests the durable owner selected ("N new actionable
requests", or "N current actionable requests" in a manual report), then the
corrections to previously reported requests. Each row links the request in
the send's saved copy. The spec's "every open request" scope and its "N open
requests" heading replace today's selection only when the digests are
converted (report-emails spec, "Delivery plan", item 4); until then the
weekly watermark still decides which rows the document holds.

A report email's requests are ``OpenRequest`` rows, each with its stage, and
its heading names the stages it adds up ("To review 10 · To handle 3",
#2331) rather than calling them open.
"""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from django.utils.translation import ngettext

from parishkit.stewardship.web.dates import format_local

from ..report_document import STAGES
from ..weekly_digest import WeeklyInformation, shorten
from .markup import TABLE, plain, style
from .registry import LIST, Block, Section


def stage_heading(stages):
    """The heading naming each stage's count: "To review 10 · To handle 3".

    ``stages`` are the listed requests' stage values. Both stages are named,
    a zero too, so the heading always says what it adds up.
    """
    return " · ".join(
        f"{stage.label} {sum(value == stage.value for value in stages):,}"
        for stage in STAGES
    )


def sections(document):
    """Each requests section with rows: (heading with its count, rows).

    A section without rows is left out; its heading carries the count so
    the email can open straight into the requests (#720).
    """
    new = "current" if document.manual else "new"
    information, corrections = document.information, document.corrections
    # A report email lists every open request (decisions 14 and 15); the
    # Weekly digest lists what is new or current until it is converted.
    if getattr(document, "open_requests", False):
        heading = stage_heading([row.stage for row in information])
    else:
        heading = f"{len(information):,} {new} actionable request" + (
            "" if len(information) == 1 else "s"
        )
    candidates = (
        (heading, information),
        (
            f"{len(corrections):,} correction"
            + ("" if len(corrections) == 1 else "s")
            + " to previously reported requests",
            corrections,
        ),
    )
    return tuple((heading, rows) for heading, rows in candidates if rows)


def draw(document, frame, options):
    """One table per section, one row per request, linked to the saved copy.

    Rows give submitted times without a zone: the subject names the report
    and its date, so the body repeats neither.
    """
    if frame.requests_url is None:
        raise ValueError("The requests block links the saved copy's requests.")
    zone = ZoneInfo(document.campaign_timezone)
    url = frame.requests_url
    html = text = ""
    for heading, rows in sections(document):
        html += f"<h2{style('heading')}>{heading}</h2>"
        html += f"<table{TABLE}{style('table')}><tbody>"
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
            item_url = escape(url + f"{row.item_id}/", quote=True)
            link = f'<a href="{item_url}"{style("link")} rel="noopener noreferrer">'
            # Numbers are table cells, not list markers, so every mail program
            # shows the same stable 1, 2, 3 a reader can refer back to.
            label = f"{number}." if numbered else ""
            html += (
                f'<tr><td width="4%"{style("number")}>{label}</td>'
                f'<td width="32%"{style("who")}>{link}{plain(family)}</a><br>'
                f"{plain(submitted)}</td>"
                f'<td width="64%"{style("excerpt")}>{plain(detail)}'
                + (f" {link}Read the full request</a>" if shortened else "")
                + "</td></tr>"
            )
            prefix = f"{number}. " if numbered else ""
            text += f"\n\n{prefix}{family}. {submitted}\n{detail}"
            link_label = "Read the full request: " if shortened else ""
            text += "\n" + link_label + url + f"{row.item_id}/"
        html += "</tbody></table>"
    return Section(html, text)


def capture(document, options):
    """Each listed request: id, Family DUID and name, submitted time, text, stage.

    The stage is last (since frozen file 0196, #2331): the snapshot guard
    compares the list with ``stewardship_report_email_requests_v2``'s.
    """
    return {
        "items": [
            [
                str(row.item_id),
                row.family_duid,
                row.family_name,
                row.submitted_at.isoformat(),
                row.text,
                row.stage,
            ]
            for row in document.information
        ]
    }


def copy(data, frame):
    """Each kept request as a numbered row linking its own page (#1946).

    The rows keep the email's order and numbers (oldest first) and its
    shortened text; the Family's name links its Timeline when the page
    found its campaign record (``CopyFrame.requests``). A send captured
    since #2331 keeps each request's stage, so its heading names the stages
    as the email did; an earlier send's says "N open requests", as its
    email did.
    """
    rows = []
    for number, (item_id, duid, name, submitted, text, *_stage) in enumerate(
        data["items"], start=1
    ):
        shown, shortened = shorten(text)
        rows.append(
            {
                "number": number,
                "item_id": item_id,
                "family_id": frame.requests.get(item_id),
                "family_name": " ".join(name.split()),
                "family_duid": duid,
                "submitted_at": datetime.fromisoformat(submitted),
                "text": shown,
                "shortened": shortened,
                "url": f"{frame.send_path}items/{item_id}/",
            }
        )
    count = len(rows)
    if all(len(item) == 6 for item in data["items"]) and data["items"]:
        heading = stage_heading([item[5] for item in data["items"]])
    else:
        heading = ngettext(
            "%(count)s open request", "%(count)s open requests", count
        ) % {"count": f"{count:,}"}
    return {"heading": heading, "rows": rows}


BLOCK = Block(
    key="additional_information",
    label="Open additional information requests",
    help=(
        "Every additional information request from a Family that is still "
        "open (to review or to handle), oldest first."
    ),
    kind=LIST,
    fact=False,
    draw=draw,
    capture=capture,
    copy=copy,
    empty="No open requests.",
)
