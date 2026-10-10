"""Portal templates draw buttons with the shared {% button %} tag (#732).

Like the help guards (test_admin_help_guards.py), this is a plain text scan of
every portal template, so it covers branches no fixture reaches. A raw
``<button>`` or a link with the ``button`` class bypasses the one definition
in web/buttons.py, so it can drift from the portal and email style.

RAW_ALLOWED names the reviewed exceptions with the most raw buttons each may
keep. Lower or remove an entry when a template moves to the tag; never raise
one. A new exception should be rare and explained.
"""

import re
from pathlib import Path

import pytest
from django.template import Context, Template, TemplateSyntaxError

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
ALL_TEMPLATES = sorted(
    str(path.relative_to(TEMPLATES)) for path in TEMPLATES.rglob("*.html")
)

RAW_ALLOWED = {
    # The Family portal pages are unchanged until the Family slice of #732.
    "family.html": 2,
    "family-login.html": 1,
    # The toggletip is itself a shared component with its own round style.
    "components/toggletip.html": 1,
    # A POST heading's button and a GET heading's link share one label across
    # an {% if %}, which a block tag cannot straddle.
    "table-sort-heading.html": 1,
    # Attribute values built from several parts ("log-{{ … }}-actor"), which
    # the tag cannot take as one argument yet.
    "hosted-files.html": 2,
    "logs.html": 3,
    "family-timeline.html": 1,
    # A compound disabled condition (paused or no rehearsal or no rows).
    "response-list.html": 1,
}

COMMENT = re.compile(r"{% comment %}.*?{% endcomment %}", re.S)
# A raw <button>, a link whose class list starts with "button" (either quote
# style), or a submit or button <input>.
RAW = re.compile(
    r"<button\b"
    r"""|<a\b[^>]*\bclass=["']button\b"""
    r"""|<input\b[^>]*\btype=["']?(?:submit|button)\b"""
)


def raw_buttons(name):
    """How many raw buttons a template draws outside the shared tag."""
    return len(RAW.findall(COMMENT.sub("", (TEMPLATES / name).read_text())))


def test_the_scan_finds_the_portal_templates():
    """A moved template directory must not make the guard pass vacuously."""
    assert len(ALL_TEMPLATES) > 100
    assert set(RAW_ALLOWED) <= set(ALL_TEMPLATES)
    assert (
        sum("{% button " in (TEMPLATES / name).read_text() for name in ALL_TEMPLATES)
        > 80
    )


@pytest.mark.parametrize("name", ALL_TEMPLATES)
def test_buttons_use_the_shared_tag(name):
    """No template adds raw button markup beyond its reviewed allowance."""
    assert raw_buttons(name) <= RAW_ALLOWED.get(name, 0), (
        f"{name}: draw buttons with {{% button %}} (web/buttons.py)"
    )


@pytest.mark.parametrize("name", sorted(RAW_ALLOWED))
def test_raw_button_allowances_are_still_needed(name):
    """An allowance shrinks as soon as its template moves to the tag."""
    assert raw_buttons(name) == RAW_ALLOWED[name]


def test_the_guard_catches_what_it_describes():
    """Both raw forms match; the tag and a plain link do not."""
    assert RAW.search('<button type="submit">')
    assert RAW.search('<a href="/x" class="button button-secondary">')
    assert not RAW.search('{% button type="submit" %}Save{% endbutton %}')
    assert not RAW.search('<a href="/x" class="buttonish">')
    assert RAW.search("<a href='/x' class='button'>")
    assert RAW.search('<input type="submit" value="Go">')
    assert RAW.search("<input type=button value=Go>")
    assert not RAW.search('<input type="checkbox" name="submit">')


def render(source, **context):
    """Render a snippet with the stewardship tag library loaded."""
    return Template("{% load stewardship %}" + source).render(Context(context))


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            '{% button type="submit" %}Save{% endbutton %}',
            '<button type="submit">Save</button>',
        ),
        (
            '{% button type="submit" variant="secondary" data-bulk-action %}'
            "Go{% endbutton %}",
            '<button type="submit" class="button-secondary" data-bulk-action>'
            "Go</button>",
        ),
        (
            '{% button class="extra" variant="link" type="submit" %}x{% endbutton %}',
            '<button class="link-button extra" type="submit">x</button>',
        ),
        (
            "{% button href=url data-live-follow %}Open{% endbutton %}",
            '<a class="button" href="/a?b=1&amp;c=&lt;" data-live-follow>Open</a>',
        ),
        (
            '{% button variant="secondary" href=url hidden=yes %}x{% endbutton %}',
            '<a class="button button-secondary" href="/a?b=1&amp;c=&lt;" hidden>x</a>',
        ),
    ],
)
def test_the_tag_renders_the_existing_markup(source, expected):
    """Attributes keep their written order; values are escaped."""
    assert render(source, url="/a?b=1&c=<", yes=True) == expected


@pytest.mark.parametrize(
    "flags,expected",
    [
        ({"ok": True, "busy": False, "who": None}, '<button type="submit">'),
        (
            {"ok": False, "busy": 1, "who": ""},
            '<button type="submit" disabled data-server-disabled hidden name="">',
        ),
    ],
)
def test_boolean_and_optional_attributes(flags, expected):
    """enabled/disabled/hidden follow truthiness; None omits a value."""
    html = render(
        '{% button type="submit" enabled=ok hidden=busy name=who %}x{% endbutton %}',
        **flags,
    )
    assert html.startswith(expected + "x")


def test_only_a_disabled_submit_button_is_marked_as_the_servers():
    """ui-v1.js enables a disabled submit button the server did not mark
    (Firefox restored a script's disabled, #921); other buttons no gate
    touches keep their markup."""
    assert render('{% button type="submit" disabled %}x{% endbutton %}') == (
        '<button type="submit" disabled data-server-disabled>x</button>'
    )
    plain = render('{% button type="button" enabled=no %}x{% endbutton %}', no=False)
    assert plain == '<button type="button" disabled>x</button>'


@pytest.mark.parametrize(
    "source",
    [
        "{% button %}x{% endbutton %}",
        '{% button type="submit" href="/x" %}x{% endbutton %}',
        '{% button type="submit" type="button" %}x{% endbutton %}',
        '{% button type="submit" Bad=1 %}x{% endbutton %}',
        '{% button href="/x" enabled=ok %}x{% endbutton %}',
        '{% button href="/x" disabled %}x{% endbutton %}',
    ],
)
def test_misuse_fails_loudly(source):
    """A missing type, a type on a link, repeats, bad names and a disabled
    link are errors."""
    with pytest.raises(TemplateSyntaxError):
        render(source)


@pytest.mark.parametrize(
    "source,context",
    [
        ('{% button type="submit" variant="huge" %}x{% endbutton %}', {}),
        ('{% button variant="link" href="/x" %}x{% endbutton %}', {}),
        ("{% button href=missing %}x{% endbutton %}", {}),
    ],
)
def test_bad_variants_and_empty_links_fail_loudly(source, context):
    """An unknown variant or a link with no address never renders silently."""
    with pytest.raises(ValueError):
        render(source, **context)


@pytest.mark.parametrize(
    "value,expected",
    [(True, ' aria-pressed="true"'), (False, ' aria-pressed="false"'), (None, "")],
)
def test_aria_states_are_spelled_out(value, expected):
    """True and False are ARIA tokens, never a bare or dropped attribute."""
    html = render(
        '{% button type="button" aria-pressed=state %}x{% endbutton %}', state=value
    )
    assert html == f'<button type="button"{expected}>x</button>'
