"""The closed inline-styled markup of the Admin report emails (#720).

Mail programs drop or ignore most CSS, so the desktop report layout and its
table-cell bars are styled inline. The compilers build from these constants
and the validators admit exactly them: a closed set of style values, bar
colours and presentational table attributes keeps the boundary as strict as
a tag list. Parish-authored content never gains these attributes.
"""

import re
from html import escape

# The Admin portal's own values (accounts/static/stewardship/ui-v1.css:
# --ink, --muted, --accent, --link, --radius-small and the primary button
# rule), copied so the report emails look like the portal. Email cannot read
# the portal's CSS custom properties; #732 lifts these into shared tokens
# for every email and the portal.
INK = "#1b2a31"  # --ink
MUTED = "#4f5e66"  # --muted
ACCENT = "#115e56"  # --accent: primary button background and border
LINK = "#155a8a"  # --link
BUTTON_RADIUS = "8px"  # --radius-small (.5rem)
BUTTON_PADDING = "10px 20px"  # .6rem 1.25rem
# The portal's weight is 650; mail programs only reliably draw bold (700).
BUTTON_WEIGHT = "bold"

# The report chart palette (reports spec, "Chart palette and style").
BLUE = "#1f6fae"
ORANGE = "#b8620a"
TRACK = "#e4e7ec"
BAR_COLOURS = {BLUE, ORANGE, TRACK}
# Cell background colours: the bars, and the primary button's cell.
CELL_COLOURS = BAR_COLOURS | {ACCENT}

# The only inline styles a compiled report may carry.
STYLES = {
    "caption": f"margin:0 0 20px;font-size:14px;color:{MUTED};",
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
    # The portal's primary button as a "bulletproof" email button: the
    # coloured table cell draws it even where a link's padding is ignored
    # (Outlook for Windows), and the link fills the cell elsewhere. White on
    # the dark accent is expected to stay legible when a mail program
    # inverts dark mode; confirm with a Testing send.
    "action": "border-collapse:separate;margin:24px 0 0;",
    # Outlook for Windows ignores a link's padding, so the cell carries it
    # there (mso-padding-alt); other programs pad the link, which fills the
    # whole button area.
    "button-cell": (
        f"border-radius:{BUTTON_RADIUS};background-color:{ACCENT};"
        f"mso-padding-alt:{BUTTON_PADDING};"
    ),
    "button": (
        f"display:inline-block;padding:{BUTTON_PADDING};"
        f"border:2px solid {ACCENT};border-radius:{BUTTON_RADIUS};"
        f"background-color:{ACCENT};color:#ffffff;font-size:16px;"
        f"font-weight:{BUTTON_WEIGHT};line-height:1.3;text-decoration:none;"
    ),
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
_STYLE_VALUES = frozenset(STYLES.values())
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
    """The portal's primary button, built from table cells for every mail program.

    ``url`` and ``label`` are escaped here; the rest is this module's closed
    markup, which the report validators admit.
    """
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
        f' style="{STYLES["action"]}"><tbody><tr>'
        f'<td bgcolor="{ACCENT}" style="{STYLES["button-cell"]}">'
        f'<a href="{escape(url, quote=True)}" style="{STYLES["button"]}"'
        f' rel="noopener noreferrer">{escape(label, quote=False)}</a>'
        "</td></tr></tbody></table>"
    )
