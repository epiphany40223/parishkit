"""Markup primitives the report email blocks share.

Every block draws only the closed markup the report validators admit
(``web.report_markup``): these helpers write its styles, bars and tables
the one way the Daily and Weekly digests always wrote them, so moving the
digest sections into blocks changed no byte of their output.
"""

from html import escape

from parishkit.stewardship.web.report_markup import BLUE, STYLES, TRACK

# A full-width presentational table, as the report layout builds them (#720).
TABLE = ' role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'


def style(name):
    """One compiler-owned inline style attribute from the validator's closed set."""
    return f' style="{STYLES[name]}"'


def plain(value):
    """Escape a text node; canonical HTML leaves quotes literal there."""
    return escape(value, quote=False)


def segment(width, colour):
    """One coloured cell of a bar; ``&nbsp;`` keeps Outlook from collapsing it."""
    return f'<td width="{width}%" bgcolor="{colour}"{style("segment")}>&nbsp;</td>'


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
    cells = segment(filled, BLUE) if filled else ""
    cells += segment(100 - filled, TRACK) if filled < 100 else ""
    return f"<table{TABLE}{style('track')}><tbody><tr>{cells}</tr></tbody></table>"
