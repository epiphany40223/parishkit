"""Render one Vega-Lite chart to PNG or SVG in a short-lived helper process (#477).

``reports.chart_rendering`` starts this module with ``python -I -m`` and an
empty environment for every image it needs, writes one JSON request to its
stdin and reads the image from its stdout, killing it at the time limit.
vl-convert renders inside an embedded JavaScript runtime that the parent could
neither interrupt nor make release its memory (about 100 MB while it renders),
so the render runs here: a kill at the deadline is clean, and the worker
process stays small between renders. Nothing here touches Django, the database
or the environment; the rendering tests check that an empty environment
renders byte for byte what vl-convert renders with a full one, fonts included.
No exception text leaves the process: stderr is discarded, and a failure is a
non-zero exit, which the parent reports under its own fixed message.
"""

import json
import sys

FORMATS = ("png", "svg")
# The request is a chart spec; a page's worth of inline data is a few hundred
# kilobytes, so anything larger is a mistake, never rendered.
MAX_REQUEST_BYTES = 1_000_000


def render(request):
    """The image bytes for one validated request (format, spec and PNG scale)."""
    import vl_convert

    spec = request["spec"]
    if request["format"] == "png":
        # An empty allowlist refuses every external data or image URL: the
        # specs carry their data inline, and a render never reaches the
        # network.
        return vl_convert.vegalite_to_png(
            spec, scale=request["scale"], allowed_base_urls=[]
        )
    return vl_convert.vegalite_to_svg(spec, allowed_base_urls=[]).encode()


def parse(data):
    """The request from its JSON bytes, or None when it is not a valid request."""
    if len(data) > MAX_REQUEST_BYTES:
        return None
    try:
        request = json.loads(data)
    except ValueError:
        return None
    if (
        type(request) is not dict
        or set(request) != {"spec", "format", "scale"}
        or type(request["spec"]) is not dict
        or request["format"] not in FORMATS
        or type(request["scale"]) not in {int, float}
        or not 0 < request["scale"] <= 4
    ):
        return None
    return request


def main():
    """Read one request from stdin, write the image to stdout, exit 0 or 1."""
    request = parse(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
    if request is None:
        return 1
    try:
        image = render(request)
    except Exception:
        return 1
    sys.stdout.buffer.write(image)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
