"""The shared browser zone note (components/zone-note.html, #1129).

ui-conventions "Schedules" has every editor that takes typed dates and times
name the zone they are read in with one note. These tests pin the note's
markup, prove the include draws exactly the line pages used to write by hand,
and keep pages from drawing their own copy again.
"""

import re
from pathlib import Path

import pytest
from django.template import engines
from django.template.loader import render_to_string

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
COMPONENT = "components/zone-note.html"
# The toggletip draws the same note as a tip when given tip_zone_note=True
# (Sent mail's From, #1562), so it is a component too, not a hand copy.
TIP = "components/toggletip.html"
INCLUDE = '{% include "stewardship/components/zone-note.html" with note_text='
TEXT = "Enter send dates and times in this computer's time zone:"
# Pages that still write the note by hand, each with why. A page leaves this
# list when it moves to the include; the test below fails on a stale entry.
HAND_WRITTEN = {
    # Open PR #1462 is changing New and Edit scheduled email; it moves to the
    # include after that merges.
    "schedule-entry.html",
    # The recorded exception (decision 18): the refresh schedule's times are
    # in its schedule zone, and its note adds the browser's zone in a span
    # that refresh-schedule-v1.js fills, not this note.
    "refresh-schedule-editor.html",
}


def render(**context):
    """Render the component with the given include variables."""
    return render_to_string(f"stewardship/{COMPONENT}", context)


def test_note_is_hidden_until_the_script_names_the_zone():
    """The note carries its id, the text, and the empty zone-name slot."""
    assert render(note_id="log-zone-help", note_text=TEXT) == (
        '<p class="help" id="log-zone-help" data-browser-zone-note hidden>'
        "Enter send dates and times in this computer&#x27;s time zone: "
        "<span data-browser-zone-name></span>.</p>"
    )


def test_note_without_an_id_escapes_its_text():
    """No id means no id attribute; the text is data, never markup."""
    html = render(note_text="<b>Days</b> in:")
    assert html.startswith('<p class="help" data-browser-zone-note hidden>')
    assert "&lt;b&gt;Days&lt;/b&gt; in:" in html


@pytest.mark.parametrize("note_id", ["", "contact-zone-help"])
def test_include_draws_the_hand_written_line_byte_for_byte(note_id):
    """A page's translate-as and include render what its old line did, with
    no added newline, so moving a page to the include changes no output."""
    attribute = f' id="{note_id}"' if note_id else ""
    argument = f' note_id="{note_id}"' if note_id else ""
    engine = engines["django"]
    old = engine.from_string(
        '{% load i18n %}[<p class="help"' + attribute + " data-browser-zone-note"
        ' hidden>{% translate "' + TEXT + '" %} <span data-browser-zone-name>'
        "</span>.</p>]"
    ).render({})
    new = engine.from_string(
        '{% load i18n %}[{% translate "'
        + TEXT
        + '" as zone_note %}'
        + INCLUDE
        + "zone_note"
        + argument
        + " %}]"
    ).render({})
    assert new == old


def test_pages_draw_the_shared_note():
    """Every other page names the browser's zone with the include, and the
    hand-written list holds only pages that still write their own."""
    hand_written, included = set(), set()
    for path in TEMPLATES.rglob("*.html"):
        name = str(path.relative_to(TEMPLATES))
        if name in (COMPONENT, TIP):
            continue
        text = path.read_text()
        if re.search(r"<\w+ [^>]*data-browser-zone-note", text):
            hand_written.add(name)
        if INCLUDE in text:
            included.add(name)
    assert hand_written == HAND_WRITTEN
    # The schedule editors' date-change page is among the adopters.
    assert "schedule-reconcile.html" in included


def test_the_toggletip_form_of_the_note():
    """With tip_zone_note=True the toggletip is the note as a tip: hidden until
    the script knows the zone, its bubble ending in the zone name's slot.
    Without it, the toggletip is unchanged."""
    context = {"tip_id": "zone-tip", "tip_text": TEXT}
    html = render_to_string("stewardship/" + TIP, {**context, "tip_zone_note": True})
    assert html.startswith('<span class="toggletip" data-browser-zone-note hidden>')
    assert (
        '<span class="toggletip-bubble" id="zone-tip" hidden>'
        "Enter send dates and times in this computer&#x27;s time zone:"
        " <span data-browser-zone-name></span>.</span></span>"
    ) in html
    plain = render_to_string("stewardship/" + TIP, context)
    assert plain.startswith('<span class="toggletip"><button')
    assert "data-browser-zone" not in plain


def test_a_page_zone_note_variable_leaves_plain_tips_plain():
    """Pages keep the note's text in a zone_note variable (translate-as), and
    an include sees the page's whole context, so a plain toggletip drawn after
    that line must stay a plain toggletip: only tip_zone_note makes the note."""
    engine = engines["django"]
    html = engine.from_string(
        '{% load i18n %}{% translate "' + TEXT + '" as zone_note %}'
        '{% include "stewardship/' + TIP + '" with tip_id="plain-tip"'
        ' tip_text="Help." %}'
    ).render({})
    assert '<span class="toggletip"><button' in html
    assert "data-browser-zone" not in html
    assert '<span class="toggletip-bubble" id="plain-tip" hidden>Help.</span>' in html
