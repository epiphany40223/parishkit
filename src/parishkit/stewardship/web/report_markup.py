"""The closed inline-styled markup of the Admin report emails (#720).

Mail programs drop or ignore most CSS, so the desktop report layout and its
table-cell bars are styled inline. The compilers build from these constants
and the validators admit exactly them: a closed set of style values, bar
colours and presentational table attributes keeps the boundary as strict as
a tag list. Parish-authored content never gains these attributes.
"""

import re

# The shared report palette (reports spec, "Chart palette and style").
BLUE = "#1f6fae"
ORANGE = "#b8620a"
TRACK = "#e4e7ec"
INK = "#1f2933"
MUTED = "#52606d"
BAR_COLOURS = {BLUE, ORANGE, TRACK}

# The only inline styles a compiled report may carry.
STYLES = {
    "caption": f"margin:0 0 20px;font-size:14px;color:{MUTED};",
    "eyebrow": f"margin:0 0 4px;font-size:14px;color:{MUTED};",
    "title": f"margin:0 0 4px;font-size:24px;line-height:1.25;color:{INK};",
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
    "button": (
        f"display:inline-block;background-color:{BLUE};color:#ffffff;"
        "text-decoration:none;font-weight:bold;padding:12px 20px;border-radius:6px;"
    ),
    "action": "margin:24px 0 0;",
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
    "footer": f"margin:16px 0 0;font-size:13px;line-height:1.5;color:{MUTED};",
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
        return value if value in BAR_COLOURS else None
    if attribute == "width":
        return value if _PERCENT.fullmatch(value) else None
    if attribute in _FIXED:
        return value if value in _FIXED[attribute] else None
    return value
