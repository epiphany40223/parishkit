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
# The optional Compared with past campaigns section's charts (#1018 slice
# 7), drawn by the chart engine's server renderer: at most one of each, in
# this order, each its own inline image beside the participation chart.
COMPARISON_CHART_IDS = (
    "comparison-families@parishkit",
    "comparison-pledges@parishkit",
)
# The Response funnel section's charts (#477 slice 2): the funnel and the
# activity series, both or neither, before the section's table. No digest
# compiled since #1370 has them; they are still accepted so a ready row
# compiled earlier can be delivered or retried unchanged.
FUNNEL_CHART_IDS = (
    "response-funnel@parishkit",
    "response-activity@parishkit",
)
# Every chart a retained digest may carry from the chart engine's server
# renderer, in the email's order: at most one of each, each its own inline
# image beside the participation chart.
SERVER_CHART_IDS = FUNNEL_CHART_IDS + COMPARISON_CHART_IDS
# Their PNGs are the email width at the renderer's scale (2); the height
# follows the legend's rows or the funnel's stages, within a bound.
COMPARISON_PIXEL_WIDTH = 2 * int(EMAIL_CHART_WIDTH)
COMPARISON_PIXEL_HEIGHTS = range(200, 2001)


def _attributes(tag, attribute, value):
    """Admit compiler-owned attributes only with their closed set of values.

    CID is permitted only for our compiled charts, never an authored link; its
    alt text is free text (escaped like any attribute) so it can carry the
    report's key numbers.
    """
    if tag == "img":
        if attribute == "src":
            known = {f"cid:{name}" for name in (CHART_ID, *SERVER_CHART_IDS)}
            return value if value in known else None
        if attribute == "width":
            return value if value in CHART_WIDTHS else None
        if attribute == "alt":
            return value if 0 < len(value) <= MAX_ALT_CHARACTERS else None
    if attribute == "href" and value.lower().startswith("cid:"):
        return None
    return layout_attribute(tag, attribute, value)


class _ChartCount(HTMLParser):
    """Collect the compiler-owned image elements, to count them by ``src``."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.charts = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.charts.append(dict(attrs))


def _png(data, *, size=None, width=None, heights=None):
    """Check one inert single-frame PNG of a bounded size; raise ValueError.

    ``size`` is its exact pixel size, or ``width`` and ``heights`` bound it.
    """
    if type(data) is not bytes or not 0 < len(data) <= MAX_CHART_BYTES:
        raise ValueError
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.n_frames != 1:
                raise ValueError
            if size is not None and image.size != size:
                raise ValueError
            if width is not None and (
                image.size[0] != width or image.size[1] not in heights
            ):
                raise ValueError
            image.verify()


def validate_digest_body(html, text, chart, images=()):
    """Reject unsafe/private-pipe data without repairing it or reading any path.

    These checks establish syntax, sizes and inert raster content, not delivery
    authority. Only the fenced owner may select the retained report and current
    recipient before invoking the adapter. ``images`` holds the server-drawn
    charts' (content id, PNG bytes) pairs in ``SERVER_CHART_IDS`` order: the
    Response funnel section's (#477 slice 2) and the Compared with past
    campaigns section's (#1018 slice 7). The body must show exactly those
    images, once each, besides the one participation chart.
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
        if type(images) is not tuple or len(images) > len(SERVER_CHART_IDS):
            raise ValueError
        names = [
            pair[0] if type(pair) is tuple and len(pair) == 2 else None
            for pair in images
        ]
        # Each known id at most once, in the email's order; the funnel's two
        # charts come together or not at all.
        if names != [name for name in SERVER_CHART_IDS if name in names] or (
            len(set(FUNNEL_CHART_IDS) & set(names)) == 1
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
        sources = [image.get("src") for image in parser.charts]
        if (
            clean != html
            or sorted(sources) != sorted(f"cid:{name}" for name in [CHART_ID, *names])
            or any(
                not image.get("alt") or image.get("width") not in CHART_WIDTHS
                for image in parser.charts
            )
        ):
            raise ValueError
        _png(chart, size=(1440, 840))
        for _name, data in images:
            _png(data, width=COMPARISON_PIXEL_WIDTH, heights=COMPARISON_PIXEL_HEIGHTS)
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
