"""The closed inline-styled markup of the Admin report emails (#720).

Mail programs drop or ignore most CSS, so the desktop report layout and its
table-cell bars are styled inline. The compilers build from these constants
and the validators admit exactly them: a closed set of style values, bar
colours and presentational table attributes keeps the boundary as strict as
a tag list. Parish-authored content never gains these attributes.
"""

import re

from .buttons import EMAIL_TABLE_STYLE, email_button, email_button_styles
from .design_tokens import CHART_COLORS, COLORS, HEADINGS, hex6, px

# The Admin portal's colours from the shared design tokens (#732), so the
# report emails look like the portal. Email cannot read the portal's CSS
# custom properties, so the values are inlined.
INK = hex6(COLORS["ink"])
MUTED = hex6(COLORS["muted"])
ACCENT = hex6(COLORS["accent"])  # the primary button's background
LINK = hex6(COLORS["link"])
# The rule under a table's column headings, as in the portal's tables.
RULE = hex6(COLORS["rule-strong"])
# A report heading is a subsection heading in the portal's type scale.
HEADING_PX = px(HEADINGS["heading-3"])

# A bar's fill is the chart engine's bar colour, and its track the chart
# engine's progress track, so the email bars match the portal's charts.
BLUE = hex6(CHART_COLORS["chart-blue"])
TRACK = hex6(CHART_COLORS["chart-track"])
BAR_COLOURS = {BLUE, TRACK}
# Cell background colours: the bars, and the primary button's cell.
CELL_COLOURS = BAR_COLOURS | {ACCENT}
# The daily digest's as-of paragraph before it became the opening line
# (#1532). A retained report compiled before then still carries it; like
# the values below it is frozen history, so it stays literal.
_RETAINED_AS_OF = "margin:0 0 20px;font-size:16px;color:#1b2a31;"
# The bar fill and the heading rule before they took the tokens (#732). A
# report compiled before then and retained for delivery or a retry still
# carries them, so the validators still admit them; nothing compiles them
# any more. These are frozen history, so they stay literal.
_RETAINED_BAR_COLOURS = {"#1f6fae"}
_RETAINED_TH = (
    "padding:8px 12px 8px 0;font-size:14px;color:#4f5e66;"
    "border-bottom:2px solid #d0d5dd;"
)

# The day-by-day table's heading and value cells; ``gap`` is the left
# padding that separates a column from the one before it.
_TH = (
    "padding:8px 0 6px {gap}px;font-size:12px;color:" + MUTED + ";"
    "border-bottom:2px solid " + RULE + ";"
)
_TD = (
    "padding:2px 0 8px {gap}px;font-size:14px;color:" + INK + ";"
    "border-bottom:1px solid " + TRACK + ";"
)
# A day's date heading on a phone, on a row of its own above the day's
# values (#1559).
_DAY = f"padding:10px 0 2px;font-size:14px;font-weight:bold;color:{INK};"
# The day-by-day table's cells before each day took two rows (#1559): a
# retained report compiled before then still carries them. Frozen history,
# so literal.
_RETAINED_TABLE = {
    "padding:8px 12px 8px 0;font-size:14px;color:#4f5e66;"
    "border-bottom:2px solid #c9d1cf;",
    "padding:7px 12px 7px 0;font-size:15px;color:#1b2a31;"
    "border-bottom:1px solid #e4e7ec;",
}
# The only inline styles a compiled report may carry.
STYLES = {
    "caption": f"margin:0 0 20px;font-size:14px;color:{MUTED};",
    # The daily digest's opening line, body text rather than a muted caption
    # (#1016), and the Response dashboard link beside it (#1532).
    "opening": f"padding:0 16px 20px 0;font-size:16px;color:{INK};",
    "opening-link": "padding:0 0 20px;font-size:16px;vertical-align:top;",
    "heading": (
        f"margin:28px 0 12px;font-size:{HEADING_PX}px;line-height:1.3;color:{INK};"
    ),
    "rows": "border-collapse:collapse;margin:0 0 8px;",
    "label": f"padding:8px 16px 8px 0;font-size:16px;color:{INK};",
    "bar": "padding:8px 16px 8px 0;",
    "value": f"padding:8px 0;font-size:16px;color:{INK};white-space:nowrap;",
    # The Campaign totals (#1844): each figure's label, then a cell holding
    # its bar and value, side by side in one row. The label never wraps;
    # on a phone, the report classes below stack the two cells.
    "total-name": f"padding:8px 16px 8px 0;font-size:16px;color:{INK};"
    "white-space:nowrap;",
    "total-figure": "padding:0;",
    "track": "border-collapse:collapse;",
    "segment": "height:14px;line-height:14px;font-size:1px;",
    "image": "display:block;width:100%;max-width:880px;height:auto;border:0;",
    "table": "border-collapse:collapse;margin:16px 0 0;",
    "table-caption": f"text-align:left;font-size:14px;color:{MUTED};padding:0 0 6px;",
    # The day-by-day table: small headings and tight gaps keep the three
    # value columns inside a 320px phone (#1559); "-lead" is the date
    # column, with no gap before it. Since #1844 the date leads its day's
    # row and never wraps. The date row above the values is hidden unless a
    # phone shows it; mso-hide keeps it hidden in Outlook for Windows too.
    "th": _TH.format(gap=8),
    "th-lead": _TH.format(gap=0),
    "day-lead": _TD.format(gap=0) + "font-weight:bold;white-space:nowrap;",
    "day-row": "display:none;mso-hide:all;",
    "day-narrow": _DAY + "mso-hide:all;",
    "td": _TD.format(gap=8),
    "link": f"color:{LINK};text-decoration:underline;",
    # The weekly digests' numbered request rows.
    "who": (
        f"padding:12px 24px 12px 0;font-size:15px;color:{INK};"
        f"vertical-align:top;border-bottom:1px solid {TRACK};"
    ),
    "excerpt": (
        f"padding:12px 0;font-size:15px;color:{INK};"
        f"vertical-align:top;border-bottom:1px solid {TRACK};"
    ),
    "number": (
        f"padding:12px 12px 12px 0;font-size:15px;font-weight:bold;color:{MUTED};"
        f"vertical-align:top;text-align:right;border-bottom:1px solid {TRACK};"
    ),
}
# The report button is the shared email button (web/buttons.py); its
# table, cell and link styles are admitted as they are built there.
_BUTTON_STYLES = {EMAIL_TABLE_STYLE, *email_button_styles("primary")[1:]}
# The button's styles before it became the shared email button (#720). A
# report compiled before then and retained for delivery or a retry still
# carries them, so they stay admitted; nothing compiles them any more.
_RETAINED_STYLES = {
    "border-collapse:separate;margin:24px 0 0;",
    ("border-radius:8px;background-color:#115e56;mso-padding-alt:10px 20px;"),
    (
        "display:inline-block;padding:10px 20px;"
        "border:2px solid #115e56;border-radius:8px;"
        "background-color:#115e56;color:#ffffff;font-size:16px;"
        "font-weight:bold;line-height:1.3;text-decoration:none;"
    ),
    _RETAINED_TH,
    _RETAINED_AS_OF,
    *_RETAINED_TABLE,
}
# The two-row styles before #1844 put each figure and each day back on one
# desktop row: the Campaign totals' (#1564, v1.19.0) and the day table's
# date row and first value cell (#1559, v1.18.0). A retained report still
# carries them. Frozen history, so literal.
_RETAINED_TWO_ROWS = {
    "padding:8px 0 4px;font-size:16px;color:#1b2a31;",
    "padding:0 16px 8px 0;",
    "padding:0 0 8px;font-size:16px;color:#1b2a31;",
    "padding:10px 0 2px;font-size:14px;font-weight:bold;color:#1b2a31;",
    "padding:2px 0 8px 0px;font-size:14px;color:#1b2a31;"
    "border-bottom:1px solid #e4e7ec;",
}
_STYLE_VALUES = (
    frozenset(STYLES.values()) | _BUTTON_STYLES | _RETAINED_STYLES | _RETAINED_TWO_ROWS
)
# The report classes (#1844): mail programs that keep the email's <style>
# block restack these cells on a phone (see mail_layout._style_block).
# Without it every class is inert and the desktop layout shows. The weekly
# digest's validator admits them too (it shares LAYOUT_ATTRIBUTES), though
# only the daily digest compiles them.
REPORT_CLASSES = {
    "pk-stack",  # a Campaign totals label or figure cell: stacks on a phone
    "pk-wrap",  # a Campaign totals value: may wrap on a phone
    "pk-wide",  # a day's date cell and its heading: hidden on a phone
    "pk-narrow",  # a day's date row: shown only on a phone
}
# Closed values for the presentational table attributes the bars use.
_FIXED = {
    "role": {"presentation"},
    "cellpadding": {"0"},
    "cellspacing": {"0"},
    "border": {"0"},
    "align": {"left", "right"},
    "scope": {"col", "row", "rowgroup"},
    # A day's date row spans the value columns (#1559): 3, or 2 without
    # pledges. A retained Campaign totals label spans its bar and value
    # (#1564).
    "colspan": {"2", "3"},
    "class": REPORT_CLASSES,
}
_PERCENT = re.compile(r"(?:100|[1-9]?[0-9])%")

# Report tables and the tags and attributes their layout uses.
LAYOUT_TAGS = {"table", "caption", "thead", "tbody", "tr", "th", "td"}
LAYOUT_ATTRIBUTES = {
    "a": {"href", "title", "style"},
    "p": {"style"},
    "h2": {"style"},
    "caption": {"style"},
    "table": {"role", "width", "cellpadding", "cellspacing", "border", "style"},
    "tr": {"class", "style"},
    "th": {"scope", "colspan", "align", "class", "style"},
    "td": {"width", "bgcolor", "align", "colspan", "class", "style"},
}


def layout_attribute(tag, attribute, value):
    """Admit a layout attribute only with one of its closed set of values."""
    if attribute == "style":
        return value if value in _STYLE_VALUES else None
    if attribute == "bgcolor":
        return value if value in CELL_COLOURS | _RETAINED_BAR_COLOURS else None
    if attribute == "width":
        return value if _PERCENT.fullmatch(value) else None
    if attribute in _FIXED:
        return value if value in _FIXED[attribute] else None
    return value


def button(url, label):
    """The portal's primary button: the shared email button (web/buttons.py).

    ``url`` and ``label`` are escaped there; the rest is closed markup, which
    the report validators admit.
    """
    return email_button(label, url)
