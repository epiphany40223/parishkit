"""Server-side chart rendering: real images, bounded, with a logged kill (#477).

``render_chart``'s caller names the timeout (``what``); these tests use a
placeholder and capture the entry, since registering the reviewed names
belongs to the first caller (see ``chart_rendering``'s TODO).
"""

import json
import struct
import subprocess
import sys
import time

import pytest
import vl_convert

from parishkit.stewardship import chart_render_worker
from parishkit.stewardship.observability import Event
from parishkit.stewardship.reports import chart_rendering
from parishkit.stewardship.reports.chart_rendering import (
    PNG_SCALE,
    ChartImage,
    ChartRenderError,
    render_chart,
)
from parishkit.stewardship.reports.chart_specs import WIDTH, funnel_chart
from parishkit.stewardship.web.dates import using

from .test_chart_specs import metrics

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
WHAT = "chart_test"


def render(spec, **options):
    """``render_chart`` under the tests' placeholder timeout name."""
    return render_chart(spec, what=WHAT, **options)


def helper(monkeypatch, code):
    """Make the render helper a Python one-liner, isolated as the real one is."""
    monkeypatch.setattr(chart_rendering, "COMMAND", (sys.executable, "-I", "-c", code))


def png_size(data):
    """The (width, height) of a PNG from its IHDR chunk."""
    assert data.startswith(PNG_SIGNATURE)
    return struct.unpack(">II", data[16:24])


@pytest.fixture(scope="module")
def funnel():
    """One funnel chart spec shared by the rendering tests."""
    with using("us_long"):
        return funnel_chart(metrics()).spec


def test_png_renders_the_spec_at_the_email_scale(funnel):
    """The helper renders a real PNG twice the CSS size, deterministically."""
    image = render(funnel, format="png")
    width, height = png_size(image.data)
    # The spec's width is the plot; axes and padding add to it.
    assert width > WIDTH * PNG_SCALE and height > 5 * 36 * PNG_SCALE
    # The CSS size an email's <img width height> uses.
    assert image == ChartImage(
        image.data, "png", width // PNG_SCALE, height // PNG_SCALE
    )
    assert render(funnel, format="png").data == image.data


def test_the_empty_environment_renders_what_vl_convert_renders(funnel):
    """The helper's empty environment loses nothing, fonts included.

    The same spec rendered in this process, with its full environment, is
    byte-for-byte the helper's image; a missing font would change the text.
    """
    direct = vl_convert.vegalite_to_png(funnel, scale=PNG_SCALE, allowed_base_urls=[])
    assert render(funnel, format="png").data == direct
    assert render(funnel, format="svg").data.decode() == vl_convert.vegalite_to_svg(
        funnel, allowed_base_urls=[]
    )


def test_svg_renders_the_spec_with_its_description(funnel):
    """The SVG is the same chart, carrying the accessible description."""
    image = render(funnel, format="svg")
    data = image.data.decode()
    assert data.startswith("<svg") and "Response funnel" in data
    assert f'width="{image.width}" height="{image.height}"' in data
    # Vega labels every mark for assistive technology, from the encoding.
    assert 'role="graphics-object"' in data
    assert 'aria-label="Families: 4; stage: Invited' in data
    assert 'aria-label="Families: 3; stage: Submitted' in data


def test_unknown_format_is_refused_before_anything_runs(funnel):
    with pytest.raises(ValueError):
        render(funnel, format="pdf")


def test_an_oversized_spec_is_refused_before_the_helper_starts(monkeypatch):
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(chart_rendering, "MAX_SPEC_BYTES", 100)
    with pytest.raises(ChartRenderError, match="too large"):
        render({"data": {"values": [{"a": "x" * 200}]}}, format="svg")
    assert not started


def test_the_helper_runs_isolated(monkeypatch):
    """-I, an empty environment, stderr discarded, like every other helper."""
    launched = {}
    real = subprocess.Popen

    def spy(command, **options):
        """Record the launch, then start the real helper."""
        launched.update(options, command=command)
        return real(command, **options)

    monkeypatch.setattr(subprocess, "Popen", spy)
    monkeypatch.setenv("PYTHONPATH", "/nonexistent")
    render({"mark": "point"}, format="svg")
    assert launched["command"] == [
        sys.executable,
        "-I",
        "-m",
        "parishkit.stewardship.chart_render_worker",
    ]
    assert launched["env"] == {} and launched["close_fds"] is True
    assert launched["stderr"] is subprocess.DEVNULL


def test_a_helper_killed_at_its_limit_is_logged(monkeypatch):
    """The kill writes the caller's what, the limit and the elapsed time.

    Through ``helper_timeout_recorder``, as every helper's kill is; whether
    the SQL review admits the names is the first caller's test.
    """
    recorded = []
    monkeypatch.setattr(
        "parishkit.stewardship.audit.timeouts.record_timeout",
        lambda event, **facts: recorded.append((event, facts)),
    )
    helper(monkeypatch, "import time; time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(ChartRenderError, match="time limit"):
        render({}, format="svg", limit_seconds=0.5)
    assert time.monotonic() - started < 10
    ((event, facts),) = recorded
    assert event is Event.HELPER_TIMED_OUT
    assert facts["what"] == WHAT
    assert facts["helper"] == "chart_render_worker"
    assert facts["limit_seconds"] == 0.5
    assert 0.5 <= facts["elapsed_seconds"] < 10


def test_a_helper_that_writes_but_never_exits_is_killed_at_its_limit(monkeypatch):
    """A whole image is not enough: the helper must also exit in time."""
    recorded = []
    monkeypatch.setattr(
        "parishkit.stewardship.audit.timeouts.record_timeout",
        lambda event, **facts: recorded.append(facts),
    )
    helper(
        monkeypatch,
        "import sys, time; sys.stdout.write('<svg/>'); sys.stdout.close(); "
        "time.sleep(30)",
    )
    with pytest.raises(ChartRenderError, match="time limit"):
        render({}, format="svg", limit_seconds=0.5)
    assert len(recorded) == 1


def test_a_failing_helper_raises_a_fixed_message(monkeypatch):
    """Nothing the helper writes to stderr is reflected."""
    helper(monkeypatch, "import sys; sys.stderr.write('secret'); exit(3)")
    with pytest.raises(ChartRenderError) as error:
        render({}, format="svg")
    assert str(error.value) == "Chart rendering failed."


def test_a_fractional_svg_size_is_rounded_up(monkeypatch):
    """A spec with a fractional width gives fractional sizes, rounded up."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="381.7" height="166.3"/>'
    helper(monkeypatch, f"import sys; sys.stdin.read(); sys.stdout.write({svg!r})")
    image = render({}, format="svg")
    assert (image.width, image.height) == (382, 167)


@pytest.mark.parametrize(
    ("format", "output"), [("png", "not a png"), ("svg", "<html></html>")]
)
def test_a_reply_that_is_not_the_requested_image_is_refused(
    monkeypatch, format, output
):
    helper(monkeypatch, f"import sys; sys.stdin.read(); sys.stdout.write({output!r})")
    with pytest.raises(ChartRenderError, match="failed"):
        render({}, format=format)


def test_an_oversized_image_is_refused_without_reading_it_all(monkeypatch):
    """The reply is read only to its bound; the helper is killed, not logged."""
    recorded = []
    monkeypatch.setattr(
        "parishkit.stewardship.audit.timeouts.record_timeout",
        lambda event, **facts: recorded.append(facts),
    )
    monkeypatch.setattr(chart_rendering, "MAX_IMAGE_BYTES", 10)
    # Far more than a pipe buffer, and it never exits on its own.
    helper(monkeypatch, "import sys\nwhile True: sys.stdout.write('x' * 65536)")
    started = time.monotonic()
    with pytest.raises(ChartRenderError, match="too large"):
        render({}, format="svg")
    assert time.monotonic() - started < 10 and not recorded


@pytest.mark.parametrize(
    "request_text",
    [
        "not json",
        json.dumps([]),
        json.dumps({"spec": {}, "format": "pdf", "scale": 2}),
        json.dumps({"spec": {}, "format": "png", "scale": 0}),
        json.dumps({"spec": {}, "format": "png", "scale": 9}),
        json.dumps({"spec": [], "format": "png", "scale": 2}),
        json.dumps({"spec": {}, "format": "png", "scale": 2, "extra": 1}),
        json.dumps({"spec": {}, "format": "png"}),
    ],
)
def test_worker_refuses_malformed_requests(request_text):
    assert chart_render_worker.parse(request_text.encode()) is None


def test_worker_refuses_a_request_past_its_size_bound(monkeypatch):
    monkeypatch.setattr(chart_render_worker, "MAX_REQUEST_BYTES", 20)
    request = b'{"spec": {}, "format": "svg", "scale": 1}'
    assert chart_render_worker.parse(request) is None


def test_worker_exits_nonzero_on_a_render_failure(monkeypatch, capsysbinary):
    """A spec vl-convert rejects is exit 1 with nothing on stdout."""
    monkeypatch.setattr(
        "sys.stdin",
        type(
            "Stdin",
            (),
            {
                "buffer": type(
                    "Buffer",
                    (),
                    {
                        "read": staticmethod(
                            lambda n: json.dumps(
                                {
                                    "spec": {"mark": "nonsense-mark"},
                                    "format": "svg",
                                    "scale": 1,
                                }
                            ).encode()
                        )
                    },
                )()
            },
        )(),
    )
    assert chart_render_worker.main() == 1
    assert capsysbinary.readouterr().out == b""
