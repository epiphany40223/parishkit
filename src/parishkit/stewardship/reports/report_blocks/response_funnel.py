"""The Response funnel block (report-emails spec, "Response funnel block").

The section both digests drew alike (#477): one row per stage with a share
bar of the email invites sent, no caption and no chart (#1370). Counting
lives in ``digest_funnel``; this module only draws what the compiling
worker counted.
"""

from ..digest_funnel import TESTING_NOTE, funnel_text, stage_rows
from .markup import TABLE, bar, plain, style
from .registry import STATUS, Block, Section


def funnel_html(document):
    """The response funnel section of the email, or "" when there is none.

    One row per stage (no charts since #1370): its short values fit a phone
    on one row, unlike the campaign totals (#1564). Each row has
    the stage (with its note), a bar of its share of email invites sent, and
    its count and share. The figures the dashboard reports beside the funnel
    are left out (#1532). A Testing digest says where the funnel is.
    """
    if document.funnel is None:
        if document.mode == "testing":
            return f"<p{style('caption')}>{plain(TESTING_NOTE)}</p>"
        return ""
    metrics = document.funnel
    invited = metrics.stage("invited")
    rows = [
        (f"{label} ({note.lower()})" if note else label, count, invited, portion)
        for label, count, portion, note in stage_rows(metrics)
    ]
    html = f"<h2{style('heading')}>Response funnel</h2>"
    html += f"<table{TABLE}{style('rows')}><tbody>"
    html += "".join(
        f'<tr><td width="34%"{style("label")}>{plain(label)}</td>'
        f'<td width="40%"{style("bar")}>{bar(count, whole)}</td>'
        f'<td width="26%" align="right"{style("value")}>'
        f"<strong>{plain(f'{count:,}')}</strong>{plain(f' ({portion})')}"
        "</td></tr>"
        for label, count, whole, portion in rows
    )
    return html + "</tbody></table>"


def draw(document, frame, options):
    """The funnel, or a Testing send's note; nothing when neither applies.

    Only a Production document carries a funnel; a document with no funnel
    outside Testing (an older caller without a mode) draws nothing.
    """
    if document.funnel is not None:
        text = funnel_text(document.funnel)
    else:
        text = TESTING_NOTE if document.mode == "testing" else ""
    return Section(funnel_html(document), text)


def capture(document, options):
    """Each stage's count and share, or that a Testing send has none."""
    if document.funnel is None:
        return {"testing": document.mode == "testing", "stages": []}
    return {
        "testing": False,
        "stages": [list(row) for row in stage_rows(document.funnel)],
    }


BLOCK = Block(
    key="response_funnel",
    label="Response funnel",
    help=(
        "How many Families were sent an email invitation, opened the form, "
        "made progress and submitted."
    ),
    kind=STATUS,
    # Counted from durable timestamps, not the pinned daily fact set.
    fact=False,
    draw=draw,
    capture=capture,
)
