"""The closed inline-styled markup of the Admin report emails (#720).

Mail programs drop or ignore most CSS, so the desktop report layout and its
table-cell bars are styled inline. The compilers build from these constants
and the validators admit exactly them: a closed set of style values, bar
colours and presentational table attributes keeps the boundary as strict as
a tag list. Parish-authored content never gains these attributes.
"""

import re

from .buttons import EMAIL_TABLE_STYLE, email_button, email_button_styles
from .design_tokens import CHART_COLORS, COLORS, hex6

# The Admin portal's colours from the shared design tokens (#732), so the
# report emails look like the portal. Email cannot read the portal's CSS
# custom properties, so the values are inlined.
INK = hex6(COLORS["ink"])
MUTED = hex6(COLORS["muted"])
ACCENT = hex6(COLORS["accent"])  # the primary button's background
LINK = hex6(COLORS["link"])

# The report chart palette (reports spec, "Chart palette and style").
BLUE = "#1f6fae"
ORANGE = "#b8620a"
# A progress bar's track, shared with the chart engine's progress bars.
TRACK = CHART_COLORS["chart-track"]
BAR_COLOURS = {BLUE, ORANGE, TRACK}
# Cell background colours: the bars, and the primary button's cell.
CELL_COLOURS = BAR_COLOURS | {ACCENT}

# The only inline styles a compiled report may carry.
STYLES = {
    "caption": f"margin:0 0 20px;font-size:14px;color:{MUTED};",
    # The daily digest's as-of line: body text, not a muted caption (#1016).
    "as-of": f"margin:0 0 20px;font-size:16px;color:{INK};",
    "heading": f"margin:28px 0 12px;font-size:18px;line-height:1.3;color:{INK};",
    "rows": "border-collapse:collapse;margin:0 0 8px;",
    "label": f"padding:8px 16px 8px 0;font-size:16px;color:{INK};",
    "bar": "padding:8px 16px 8px 0;",
    "value": f"padding:8px 0;font-size:16px;color:{INK};white-space:nowrap;",
    "track": "border-collapse:collapse;",
    "segment": "height:14px;line-height:14px;font-size:1px;",
    "image": "display:block;width:100%;max-width:880px;height:auto;border:0;",
    "table": "border-collapse:collapse;margin:16px 0 0;",
    "table-caption": f"text-align:left;font-size:14px;color:{MUTED};padding:0 0 6px;",
    "th": (
        f"padding:8px 12px 8px 0;font-size:14px;color:{MUTED};"
        f"border-bottom:2px solid #d0d5dd;"
    ),
    "td": f"padding:7px 12px 7px 0;font-size:15px;color:{INK};"
    f"border-bottom:1px solid {TRACK};",
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
}
_STYLE_VALUES = frozenset(STYLES.values()) | _BUTTON_STYLES | _RETAINED_STYLES
# Closed values for the presentational table attributes the bars use.
_FIXED = {
    "role": {"presentation"},
    "cellpadding": {"0"},
    "cellspacing": {"0"},
    "border": {"0"},
    "align": {"left", "right"},
    "scope": {"col"},
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
    "th": {"scope", "align", "style"},
    "td": {"width", "bgcolor", "align", "style"},
}


def layout_attribute(tag, attribute, value):
    """Admit a layout attribute only with one of its closed set of values."""
    if attribute == "style":
        return value if value in _STYLE_VALUES else None
    if attribute == "bgcolor":
        return value if value in CELL_COLOURS else None
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
