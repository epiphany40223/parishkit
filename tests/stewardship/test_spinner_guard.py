"""The portal has one working spinner (#1847, ui-conventions "Working spinner").

The guard half is a plain text scan of every stewardship stylesheet,
template and script, like test_button_guard.py, so it covers branches no
fixture reaches: a second spinning ring, keyframes or spinner class would
drift from the shared one (size, colour, reduced motion). The markup half
renders the shared include and the stage boxes, which show it only while a
stage is In progress.
"""

import re
from pathlib import Path

from django.template.loader import render_to_string

SOURCE = Path(__file__).parents[2] / "src/parishkit/stewardship"
SPINNER = "stewardship/components/spinner.html"
SHARED = ".spinner, :is(button, .button).is-busy::after"
COMMENTS = re.compile(r"/\*.*?\*/", re.S)


def own(pattern):
    """Stewardship files matching pattern, outside vendored libraries."""
    return sorted(path for path in SOURCE.rglob(pattern) if "vendor" not in path.parts)


def stylesheet_rules():
    """Every (file name, selector, declarations) CSS rule, comments removed.

    A flat split on braces is enough for these stylesheets: an @media or
    @keyframes prelude is dropped and its inner rules come out as rules of
    their own, so every declaration is still attributed to some selector.
    """
    rules = []
    for path in own("*.css"):
        text = COMMENTS.sub("", path.read_text())
        for selector, body in re.findall(r"([^{}]*)\{([^{}]*)\}", text):
            rules.append((path.name, " ".join(selector.split()), body))
    return rules


def test_one_keyframes_and_one_spinning_rule():
    """Only the shared rule animates, with the one busy-spin keyframes.

    Deliberately strict: the portal has no other animation, and a new one
    (a fade, a pulse) must be reviewed against reduced motion and this
    rule rather than slip in; extend this test when one is agreed.
    """
    css = "".join(COMMENTS.sub("", path.read_text()) for path in own("*.css"))
    assert re.findall(r"@keyframes\s+([\w-]+)", css) == ["busy-spin"]
    animated = [
        (name, selector)
        for name, selector, body in stylesheet_rules()
        if re.search(r"\banimation(?:-name)?\s*:(?!\s*none\b)", body)
    ]
    assert animated == [("ui-v1.css", SHARED)]


def test_no_other_ring_or_spinner_class():
    """No stylesheet draws a second open ring or names another spinner class.

    A ring is any rule with a round corner of 50% and a transparent border
    side (longhand, logical or shorthand), the open quarter of a spinner.
    """
    transparent = re.compile(r"\bborder(?:-[\w-]*)?-color\s*:[^;]*\btransparent\b")
    rings = [
        selector
        for _, selector, body in stylesheet_rules()
        if transparent.search(body) and re.search(r"border-radius\s*:\s*50%", body)
    ]
    assert rings == [SHARED]
    names = set()
    for path in own("*.css"):
        names.update(
            re.findall(r"\.([\w-]*spin[\w-]*)", COMMENTS.sub("", path.read_text()))
        )
    assert names == {"spinner"}


def test_templates_and_scripts_use_the_shared_include():
    """Only components/spinner.html writes a spinner class; scripts write
    none, and nothing animates outside the stylesheets.

    Python is scanned too, for HTML built with format_html. "spinbutton"
    (an ARIA role) is not a spinner.
    """
    spinner_class = re.compile(r"""class=["'][^"']*\bspin(?!button)""")
    inline = re.compile(r"""style=["'][^"']*\banimation|<animate(?:Transform)?\b""")
    writers = [
        str(path.relative_to(SOURCE))
        for path in own("*.html") + own("*.py")
        if spinner_class.search(path.read_text())
    ]
    assert writers == ["accounts/templates/" + SPINNER]
    for path in own("*.html") + own("*.py"):
        assert not inline.search(path.read_text()), path
    for path in own("*.js"):
        source = path.read_text()
        assert not re.search(r"""["'][\w .-]*\bspin(?!button)[\w-]*["']""", source), (
            path
        )
        assert not re.search(r"\.animate\(|style\.animation", source), path


def test_the_include_is_decorative():
    """The spinner is hidden from assistive technology; words carry the state."""
    assert render_to_string(SPINNER).strip() == (
        '<span class="spinner" aria-hidden="true"></span>'
    )


def test_stage_boxes_show_the_spinner_only_while_in_progress():
    """An In progress box, and no other state, draws the shared spinner."""
    stages = [
        {"key": key, "label": key.title(), "state": key, "status": key}
        for key in ("done", "active", "waiting", "failed", "operator")
    ]
    html = render_to_string(
        "stewardship/components/stage-boxes.html",
        {"stages": stages, "label": "Parts", "key_attribute": "part"},
    )
    boxes = dict(re.findall(r'<li [^>]*data-part="(\w+)"[^>]*>(.*?)</li>', html, re.S))
    assert boxes.keys() == {stage["key"] for stage in stages}
    spinner = render_to_string(SPINNER).strip()
    for key, inner in boxes.items():
        assert (spinner in inner) is (key == "active"), key
    # The spinner comes first, before the words, so it sits in the mark's slot.
    assert boxes["active"].startswith(spinner + '<span class="stage-label">')


def test_live_running_line_uses_the_shared_spinner():
    """The live "Working…" line draws the shared include before its words."""
    html = render_to_string("stewardship/live-running.html", {"message": "Working"})
    assert '<p class="live-running"><span class="spinner" aria-hidden="true">' in html
