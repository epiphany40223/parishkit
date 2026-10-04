"""Static PNG or SVG of a chart for emails and exports (#477).

The same Vega-Lite spec the browser draws (``chart_specs``) is rendered here
by vl-convert, which bundles the vendored Vega releases (``chart_assets``).
Rendering runs only in workers and export tasks, never on a page view, and
always in a short-lived helper process (``stewardship.chart_render_worker``):
vl-convert's embedded JavaScript runtime cannot be interrupted from Python and
keeps about 100 MB resident once it has rendered, so the helper renders one
image and exits, and is killed at its time limit.

The helper is isolated like every other ParishKit helper: ``python -I`` (no
user site, no ``PYTHON*`` variables, no current directory on the path), an
empty environment, stderr discarded and a pipe exchange whose request and
reply are both bounded. A kill at the deadline is reported through
``provider_checks.helper_timeout_recorder`` with the caller's ``what``.

TODO(#477, the PR that first calls ``render_chart``): register the caller's
``what`` and ``HELPER`` as reviewed timeout names (``audit.schemas``,
``observability.TIMEOUT_LIMITS`` and the SQL mirror in
``stewardship_safe_context_v1``, through a frozen schema migration) in the
same PR. Until then nothing renders charts on the server, and an unreviewed
name would leave the kill only in the process log, not the durable one.
"""

import json
import math
import re
import struct
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from threading import Event, Thread

from parishkit.stewardship.provider_checks import (
    ProviderCheckDrainFailure,
    _stop,
    helper_timeout_recorder,
)

FORMATS = ("png", "svg")
# Emails and exports are read on high-density screens: a PNG has PNG_SCALE
# device pixels per CSS pixel, and ChartImage reports the CSS size.
PNG_SCALE = 2
# A chart renders in well under a second; a minute means the runtime hung.
RENDER_SECONDS = 60
MAX_SPEC_BYTES = 1_000_000
# A 1,440-pixel-wide PNG of a dense chart is a few hundred kilobytes.
MAX_IMAGE_BYTES = 16 * 1024 * 1024
# The helper's entry point, the timeout entry's helper name (see the TODO
# above). -I keeps the helper from reading anything the environment or the
# working directory could inject; it imports the installed package.
HELPER = "chart_render_worker"
COMMAND = (sys.executable, "-I", "-m", f"parishkit.stewardship.{HELPER}")
# The root <svg> element's size attributes, which vl-convert writes in CSS
# pixels (the same numbers as its viewBox); a spec with a fractional width or
# height gives fractional sizes.
SVG_SIZE = re.compile(
    rb'<svg\b[^>]*?\swidth="(\d+(?:\.\d+)?)"[^>]*?\sheight="(\d+(?:\.\d+)?)"'
)


class ChartRenderError(Exception):
    """The chart could not be rendered; the message is fixed, never provider text."""


@dataclass(frozen=True)
class ChartImage:
    """One rendered chart and its size in CSS pixels.

    A PNG is ``PNG_SCALE`` times larger in device pixels; an email's
    ``<img width height>`` uses ``width`` and ``height`` as they are.
    """

    data: bytes
    format: str
    width: int
    height: int


def render_chart(spec, *, format, what, limit_seconds=RENDER_SECONDS):
    """Render one Vega-Lite ``spec`` to a PNG (at ``PNG_SCALE``) or SVG.

    ``what`` is the reviewed timeout name the caller's work is logged under
    when the helper is killed at ``limit_seconds``. Raises
    ``ChartRenderError`` when the helper fails, is killed at its deadline
    (after the timeout entry is written) or returns something outside the
    size bounds or not an image of the requested format. A spec that cannot
    be serialized raises ``TypeError``; one too large is refused before
    anything starts.
    """
    if format not in FORMATS:
        raise ValueError("Charts render to PNG or SVG.")
    request = json.dumps(
        {"spec": spec, "format": format, "scale": PNG_SCALE}, separators=(",", ":")
    ).encode()
    if len(request) > MAX_SPEC_BYTES:
        raise ChartRenderError("The chart specification is too large to render.")
    on_timeout = helper_timeout_recorder(HELPER, what=what, seconds=limit_seconds)
    deadline = time.monotonic() + limit_seconds
    try:
        process = subprocess.Popen(
            list(COMMAND),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={},
        )
    except OSError:
        raise ChartRenderError("Chart rendering is unavailable.") from None
    try:
        data = _exchange(process, request, deadline, on_timeout)
    finally:
        _stop(process)
        for stream in (process.stdin, process.stdout):
            with suppress(Exception):
                stream.close()
    return _image(data, format)


def _exchange(process, request, deadline, on_timeout):
    """Write the request and read at most MAX_IMAGE_BYTES + 1 of the reply.

    One pump thread owns both pipes (a blocked write or read cannot stall the
    deadline) and waits for the helper to exit after a complete reply; this
    thread kills the helper when the deadline passes, then logs the kill, as
    ``family_delivery_process`` does. A reply past the bound is not read
    further: the helper is killed without a timeout entry, since no limit of
    time stopped it.
    """
    state = {}
    done = Event()

    def pump():
        """Send the request, read the bounded reply, and reap a finished helper."""
        try:
            process.stdin.write(request)
            process.stdin.close()
            state["data"] = process.stdout.read(MAX_IMAGE_BYTES + 1)
            if len(state["data"]) <= MAX_IMAGE_BYTES:
                state["returncode"] = process.wait()
        except Exception:
            # Keep pipe errors private; a missing result is a failed render.
            pass
        finally:
            done.set()

    thread = Thread(target=pump, name="chart-render", daemon=True)
    thread.start()
    finished = done.wait(max(0, deadline - time.monotonic()))
    stopped = None if finished else time.monotonic()
    # Killing ends the pump's read or wait, so ``state`` is final once joined.
    _stop(process)
    thread.join(timeout=5)
    if thread.is_alive():
        # A pump still inside a pipe call may hold its stream lock.
        raise ProviderCheckDrainFailure("Chart render helper could not drain.")
    if stopped is not None:
        with suppress(Exception):
            on_timeout(stopped)
        raise ChartRenderError("Chart rendering exceeded its time limit.")
    data = state.get("data")
    if data is not None and len(data) > MAX_IMAGE_BYTES:
        raise ChartRenderError("The rendered chart is too large.")
    if state.get("returncode") != 0 or not data:
        raise ChartRenderError("Chart rendering failed.")
    return data


def _image(data, format):
    """The ChartImage for the helper's bytes, with its CSS size.

    A PNG's size is in its IHDR chunk (device pixels, divided back by
    ``PNG_SCALE``); an SVG's is on its root element.
    """
    if format == "png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
            raise ChartRenderError("Chart rendering failed.")
        width, height = struct.unpack(">II", data[16:24])
        return ChartImage(
            data, format, round(width / PNG_SCALE), round(height / PNG_SCALE)
        )
    size = SVG_SIZE.match(data)
    if size is None:
        raise ChartRenderError("Chart rendering failed.")
    # Round fractional sizes up so an <img> never clips the drawing.
    return ChartImage(
        data, format, math.ceil(float(size[1])), math.ceil(float(size[2]))
    )
