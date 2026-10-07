"""A narrow compiled digest HTML/image boundary, independent of Django setup."""

import warnings
from html.parser import HTMLParser
from io import BytesIO

import nh3
from PIL import Image

from .content import TAGS
from .report_markup import LAYOUT_ATTRIBUTES, LAYOUT_TAGS, layout_attribute

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
    return layout_attribute(tag, attribute, value)


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
            tags=TAGS | LAYOUT_TAGS | {"img", "dl", "dt", "dd"},
            attributes=LAYOUT_ATTRIBUTES | {"img": {"src", "alt", "width", "style"}},
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
