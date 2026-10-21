"""The Open additional information requests block (report-emails spec).

The Weekly digest's requests sections, drawn exactly as today: the
numbered actionable requests the durable owner selected ("N new actionable
requests", or "N current actionable requests" in a manual report), then the
corrections to previously reported requests. Each row links the request in
the send's saved copy. The spec's "every open request" scope and its "N open
requests" heading replace today's selection only when the digests are
converted (report-emails spec, "Delivery plan", item 4); until then the
weekly watermark still decides which rows the document holds.
"""

from html import escape
from zoneinfo import ZoneInfo

from parishkit.stewardship.web.dates import format_local

from ..weekly_digest import WeeklyInformation, shorten
from .markup import TABLE, plain, style
from .registry import LIST, Block, Section


def sections(document):
    """Each requests section with rows: (heading with its count, rows).

    A section without rows is left out; its heading carries the count so
    the email can open straight into the requests (#720).
    """
    new = "current" if document.manual else "new"
    information, corrections = document.information, document.corrections
    candidates = (
        (
            f"{len(information):,} {new} actionable request"
            + ("" if len(information) == 1 else "s"),
            information,
        ),
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
    if frame.copy_url is None:
        raise ValueError("The requests block links the send's saved copy.")
    zone = ZoneInfo(document.campaign_timezone)
    url = frame.copy_url
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
            item_url = escape(url + f"items/{row.item_id}/", quote=True)
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
            text += "\n" + link_label + url + f"items/{row.item_id}/"
        html += "</tbody></table>"
    return Section(html, text)


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
    empty="No open requests.",
)
