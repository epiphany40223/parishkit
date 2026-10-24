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
    "table-sort-control.html": 1,
    # Attribute values built from several parts ("log-{{ … }}-actor"), which
    # the tag cannot take as one argument yet.
    "hosted-files.html": 2,
    # A binary choice's two POST segments (#1535) are drawn in the shared
    # segmented look, like its link segments, never as portal buttons; each
    # is marked aria-current inside its own tag, which the block tag cannot
    # take.
    "components/segmented-choice.html": 2,
    # A report email Preview's Email | Plain text (#2035): the same
    # segmented look inside a dialog, two views switched in place with no
    # address, each marked aria-pressed, never portal buttons.
    "report-email-preview.html": 2,
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


# ---------------------------------------------------------------- labels
# Button labels don't end in an ellipsis, even when a review step follows
# (ui-conventions Button labels, #1268). The scan reads only what a button
# says: the body of each {% button %}, raw <button> and button-class link,
# one {% if %}/{% elif %}/{% else %} branch (or {% plural %} form) at a
# time. A body that shows only a variable ({{ accept }} in the confirm
# dialog, {{ submit }}, {{ send }} ...) is followed to where templates set
# that name: {% translate "…" as accept %} or accept="…" on an
# {% include %} or {% with %}. Progress text ("Saving…", data-working) and
# truncation sit outside those bodies, so they need no exception here.
#
# Not covered: labels built in Python and passed in a context
# ({{ command.label }}, {{ action.label }}), and text JavaScript writes
# into a button. Neither ends in an ellipsis today.
#
# ELLIPSIS_ALLOWED names reviewed exceptions, template -> reason; it is
# empty, and each new entry needs the reason a label must trail off.
ELLIPSIS_ALLOWED: dict[str, str] = {}

ANY_COMMENT = re.compile(r"{% comment %}.*?{% endcomment %}|{#.*?#}|<!--.*?-->", re.S)
BUTTON_BODY = re.compile(
    r"{% button\b.*?%}(?P<tag>.*?){% endbutton %}"
    r"|<button\b[^>]*>(?P<raw>.*?)</button>"
    r"""|<a\b[^>]*\bclass=["']button\b[^>]*>(?P<link>.*?)</a>""",
    re.S,
)
# Where one shown branch ends and the next begins.
BRANCH = re.compile(r"{% (?:if|elif|else|endif|plural)\b.*?%}", re.S)
# {% translate "…" as name %} sets a variable and shows nothing.
TRANSLATE_AS = re.compile(r"""{% translate (["'])(.*?)\1[^%]*?\bas (\w+) %}""")
TRANSLATED = re.compile(r"""{% translate (["'])(.*?)\1[^%]*%}""")
MARKUP = re.compile(r"{%.*?%}|{{.*?}}|<[^>]*>", re.S)
# A branch that shows a single plain variable, such as {{ accept }}.
ONLY_VARIABLE = re.compile(r"\s*{{\s*(\w+)\s*}}\s*")
# name="…" (or name=_("…")) on an {% include %} or {% with %} tag.
SETTING_TAG = re.compile(r"{% (?:include|with)\b.*?%}", re.S)
SET_LITERAL = re.compile(r"""\b(\w+)=(?:_\()?(["'])(.*?)\2""")
TRAILING_ELLIPSIS = ("…", "...", "&hellip;")


def button_branches(source):
    """Each piece of text a button in ``source`` can show on its own: one
    per {% if %} branch or plural form, with every translated literal put
    in place and {% translate … as name %} assignments dropped."""
    branches = []
    for match in BUTTON_BODY.finditer(ANY_COMMENT.sub("", source)):
        body = match["tag"] or match["raw"] or match["link"] or ""
        body = TRANSLATED.sub(r"\2", TRANSLATE_AS.sub("", body))
        branches += BRANCH.split(body)
    return branches


def label_variables(source):
    """Names a button in ``source`` shows as its whole label."""
    return {
        found[1]
        for branch in button_branches(source)
        if (found := ONLY_VARIABLE.fullmatch(branch))
    }


# Every variable a portal template's button shows as its whole label; a
# caller can set it from any template, so the set is shared.
LABEL_VARIABLES = frozenset().union(
    *(label_variables((TEMPLATES / name).read_text()) for name in ALL_TEMPLATES)
)


def assigned_labels(source, names):
    """Literal values ``source`` gives any of ``names``."""
    source = ANY_COMMENT.sub("", source)
    found = [text for _, text, name in TRANSLATE_AS.findall(source) if name in names]
    for tag in SETTING_TAG.findall(source):
        found += [text for name, _, text in SET_LITERAL.findall(tag) if name in names]
    return found


def trailing_ellipsis_labels(source):
    """Button labels in a template source that end in an ellipsis: shown in
    a button there, or given to a variable some button shows as its label."""
    texts = [MARKUP.sub("", branch) for branch in button_branches(source)]
    texts += assigned_labels(source, LABEL_VARIABLES | label_variables(source))
    return [text.strip() for text in texts if text.strip().endswith(TRAILING_ELLIPSIS)]


@pytest.mark.parametrize("name", ALL_TEMPLATES)
def test_button_labels_do_not_end_in_an_ellipsis(name):
    """No button label trails off, even before a review step (#1268)."""
    if name in ELLIPSIS_ALLOWED:
        return
    labels = trailing_ellipsis_labels((TEMPLATES / name).read_text())
    assert not labels, (
        f"{name}: drop the trailing ellipsis from {labels} "
        "(ui-conventions spec, Button labels)"
    )


def test_ellipsis_allowances_are_still_needed():
    """An exception names a real template and a reason, and goes as soon as
    its label loses the ellipsis. (One test, not one per entry, so the empty
    map runs rather than skips.)"""
    for name, reason in ELLIPSIS_ALLOWED.items():
        assert name in ALL_TEMPLATES and reason.strip(), name
        assert trailing_ellipsis_labels((TEMPLATES / name).read_text()), name


def test_the_label_variables_include_the_confirm_dialog():
    """The variable scan finds the label names the templates use today."""
    assert {"accept", "submit", "send"} <= LABEL_VARIABLES


@pytest.mark.parametrize(
    "source",
    [
        '{% button type="submit" %}{% translate "Take a backup now…" %}{% endbutton %}',
        '{% button href=url %}{% translate "Open..." %}{% endbutton %}',
        '{% button type="button" %}{% if a %}{% translate "Send these again…" %}'
        '{% else %}{% translate "Send" %}{% endif %}{% endbutton %}',
        '<button type="submit">Release the site&hellip;</button>',
        '<a href="/x" class="button button-secondary">Review… </a>',
        # A plain-text branch, and a blocktranslate branch, before an else.
        '{% button type="submit" %}{% if a %}Review…{% else %}Send{% endif %}'
        "{% endbutton %}",
        '{% button type="submit" %}{% if a %}{% blocktranslate %}Send {{ n }} '
        "again…{% endblocktranslate %}{% else %}Send{% endif %}{% endbutton %}",
        '{% button type="submit" %}{% elif b %}Check…{% endif %}{% endbutton %}',
        '{% button type="submit" %}{% blocktranslate count n=n %}Send one…'
        "{% plural %}Send {{ n }}{% endblocktranslate %}{% endbutton %}",
        # A label set where the confirm dialog (or another caller) is used.
        '{% translate "Revoke…" as accept %}'
        '{% include "stewardship/components/confirm-dialog.html" %}',
        '{% include "stewardship/components/confirm-dialog.html" with '
        'accept="Revoke…" %}',
        '{% with submit=_("Finish...") %}{% include "x.html" %}{% endwith %}',
        # A variable this source itself shows in a button.
        '{% translate "Go…" as go_label %}'
        '{% button type="submit" %}{{ go_label }}{% endbutton %}',
    ],
)
def test_the_label_guard_catches_a_trailing_ellipsis(source):
    """Each button form, each branch and each way a label is set is read."""
    assert trailing_ellipsis_labels(source)


@pytest.mark.parametrize(
    "source",
    [
        '{% button type="submit" %}{% translate "Take a backup now" %}{% endbutton %}',
        '<p data-working="{% translate \'Working…\' %}">{% translate "Saving…" %}</p>',
        '{% button type="submit" %}{% translate "Use … in a name" %}{% endbutton %}',
        '{% comment %}{% button type="submit" %}Old…{% endbutton %}{% endcomment %}',
        # An assignment inside a button shows nothing; comments are not markup.
        '{% button type="submit" %}{% translate "Working…" as w %}Save{% endbutton %}',
        '{# <button type="submit">Old…</button> #}',
        '<!-- <button type="submit">Old…</button> -->',
        # A variable no button shows may end in an ellipsis.
        '{% translate "Checking…" as status_text %}<p>{{ status_text }}</p>',
        '{% include "x.html" with message="Saving…" %}',
    ],
)
def test_the_label_guard_leaves_other_ellipses_alone(source):
    """Progress text, a mid-label ellipsis, assignments that are not labels
    and comments are not labels."""
    assert not trailing_ellipsis_labels(source)


STYLESHEET = TEMPLATES.parents[1] / "static/stewardship/ui-v1.css"


def test_only_the_class_makes_a_button_secondary():
    """The secondary look is chosen where a button is drawn (#732): the
    stylesheet's secondary rules select .button-secondary alone, never a
    button by its place, name or data- attribute."""
    css = re.sub(r"/\*.*?\*/", "", STYLESHEET.read_text(), flags=re.S)
    rules = re.findall(r"([^{}]*\.button-secondary[^{}]*){", css)
    selectors = sorted({part.strip() for rule in rules for part in rule.split(",")})
    assert selectors == [
        ".button-secondary",
        ".button-secondary:not(:disabled):not(:where([aria-disabled=true])):hover",
    ]
