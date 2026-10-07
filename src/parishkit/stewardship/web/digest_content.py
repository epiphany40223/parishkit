"""A narrow compiled digest HTML/image boundary, independent of Django setup."""

import re
import warnings
from html.parser import HTMLParser
from io import BytesIO

import nh3
from PIL import Image

from .content import TAGS

CHART_ID = "participation@parishkit"
# The fixed alt text of digests compiled before #720; still accepted so a
# retained message compiled earlier can be delivered or retried unchanged.
CHART_ALT = "Family participation chart; exact daily values follow in the table."
MAX_CHART_BYTES = 1024 * 1024
MAX_BODY_BYTES = 1024 * 1024
MAX_ALT_CHARACTERS = 500
# The chart's CSS width: 880 in the desktop layout (#720), 720 before it.
EMAIL_CHART_WIDTH = "880"
CHART_WIDTHS = {"720", EMAIL_CHART_WIDTH}

# The shared report palette (reports spec, "Chart palette and style").
BLUE = "#1f6fae"
ORANGE = "#b8620a"
TRACK = "#e4e7ec"
INK = "#1f2933"
MUTED = "#52606d"
BAR_COLOURS = {BLUE, ORANGE, TRACK}

# The only inline styles a compiled digest may carry (#720). Mail programs
# drop or ignore most CSS, so the desktop layout and its table-cell bars are
# styled inline; a closed set keeps the boundary as strict as a tag list.
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


def _attributes(tag, attribute, value):
    """Admit compiler-owned attributes only with their closed set of values.

    CID is permitted only for our compiled chart, never an authored link; its
    alt text is free text (escaped like any attribute) so it can carry the
    report's key numbers.
    """
    if tag == "img":
        if attribute == "src":
            return value if value == f"cid:{CHART_ID}" else None
        if attribute == "width":
            return value if value in CHART_WIDTHS else None
        if attribute == "alt":
            return value if 0 < len(value) <= MAX_ALT_CHARACTERS else None
    if attribute == "href" and value.lower().startswith("cid:"):
        return None
    if attribute == "style":
        return value if value in _STYLE_VALUES else None
    if attribute == "bgcolor":
        return value if value in BAR_COLOURS else None
    if attribute == "width":
        return value if _PERCENT.fullmatch(value) else None
    if attribute in _FIXED:
        return value if value in _FIXED[attribute] else None
    return value


class _ChartCount(HTMLParser):
    """Require exactly one complete compiler-owned image element."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.charts = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.charts.append(dict(attrs))


def validate_digest_body(html, text, chart):
    """Reject unsafe/private-pipe data without repairing it or reading any path.

    These checks establish syntax, sizes and inert raster content, not delivery
    authority. Only the fenced owner may select the retained report and current
    recipient before invoking the adapter.
    """
    try:
        if any(
            type(value) is not str
            or not value.strip()
            or "\x00" in value
            or len(value.encode("utf-8")) > MAX_BODY_BYTES
            for value in (html, text)
        ):
            raise ValueError
        clean = nh3.clean(
            html,
            tags=TAGS
            | {
                "img",
                "dl",
                "dt",
                "dd",
                "table",
                "caption",
                "thead",
                "tbody",
                "tr",
                "th",
                "td",
            },
            attributes={
                "a": {"href", "title", "style"},
                "img": {"src", "alt", "width", "style"},
                "p": {"style"},
                "h2": {"style"},
                "caption": {"style"},
                "table": {
                    "role",
                    "width",
                    "cellpadding",
                    "cellspacing",
                    "border",
                    "style",
                },
                "th": {"scope", "align", "style"},
                "td": {"width", "bgcolor", "align", "style"},
            },
            attribute_filter=_attributes,
            url_schemes={"https", "http", "mailto", "tel", "cid"},
            link_rel="noopener noreferrer",
            strip_comments=True,
        )
        parser = _ChartCount()
        parser.feed(html)
        if (
            clean != html
            or len(parser.charts) != 1
            or parser.charts[0].get("src") != f"cid:{CHART_ID}"
            or not parser.charts[0].get("alt")
            or parser.charts[0].get("width") not in CHART_WIDTHS
        ):
            raise ValueError
        if type(chart) is not bytes or not 0 < len(chart) <= MAX_CHART_BYTES:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(chart)) as image:
                if (
                    image.format != "PNG"
                    or image.size != (1440, 840)
                    or image.n_frames != 1
                ):
                    raise ValueError
                image.verify()
    except (
        ValueError,
        TypeError,
        UnicodeError,
        OSError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        raise ValueError("Invalid compiled digest content.") from None
