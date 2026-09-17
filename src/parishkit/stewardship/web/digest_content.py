"""A narrow compiled digest HTML/image boundary, independent of Django setup."""

import warnings
from html.parser import HTMLParser
from io import BytesIO

import nh3
from PIL import Image

from .content import TAGS

CHART_ID = "participation@parishkit"
CHART_ALT = "Family participation chart; exact daily values follow in the table."
MAX_CHART_BYTES = 1024 * 1024
MAX_BODY_BYTES = 1024 * 1024


def _attributes(tag, attribute, value):
    """CID is permitted only for our compiled chart, never an authored link."""
    if tag == "img":
        expected = {"src": f"cid:{CHART_ID}", "alt": CHART_ALT, "width": "720"}
        return value if expected.get(attribute) == value else None
    if attribute == "href" and value.lower().startswith("cid:"):
        return None
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
                "a": {"href", "title"},
                "img": {"src", "alt", "width"},
                "th": {"scope"},
            },
            attribute_filter=_attributes,
            url_schemes={"https", "http", "mailto", "tel", "cid"},
            link_rel="noopener noreferrer",
            strip_comments=True,
        )
        parser = _ChartCount()
        parser.feed(html)
        if clean != html or parser.charts != [
            {"src": f"cid:{CHART_ID}", "alt": CHART_ALT, "width": "720"}
        ]:
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
