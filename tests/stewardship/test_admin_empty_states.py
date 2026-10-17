"""Guard that Admin empty tables and lists say why and what to do (#227).

The ui-conventions spec's "Empty tables and lists" section asks that a table
or list with nothing to show say why it is empty and what the reader can do
next, rather than only that it is empty ("No users."). Like the help guards,
this is a text scan of the Admin templates, so it needs no database and covers
every branch, including branches no fixture reaches.

It checks the message of each ``{% for %}...{% empty %}`` branch that draws a
whole row, list item or paragraph: that message must hold a second sentence
or clause (a reason or a next step) after its first. Cell-level fallbacks
inside a row ("None chosen", "No phone available") are values, not empty
lists, and are not scanned. Empty states drawn by ``{% if %}...{% else %}``
are reviewed by hand, since a text scan cannot tell them from other branches.
"""

import re

import pytest

from .test_admin_help_guards import ADMIN_TEMPLATES, COMMENT, TEMPLATES

# The empty branch's own element: a row spanning the table, one list item or
# one paragraph, up to its matching close tag. An {% if %} or {% with %} may
# open first (choosing between two rows); a tag's body never holds "%", so the
# skip cannot run past the loop's end into the page's next element.
EMPTY = re.compile(
    r"{% empty %}\s*(?:{% (?:if|with)\b[^%]*%}\s*)*<(tr|li|p)\b.*?</\1>", re.S
)
# A translated message in either quote style, or a blocktranslate's text.
MESSAGE = re.compile(
    r"""{% (?:translate|trans) (?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""
    r"|{% blocktranslate[^%]*%}(.*?){% (?:plural|endblocktranslate)",
    re.S,
)
# Template tags, variables and HTML tags: what is left is literal text.
MARKUP = re.compile(r"{%.*?%}|{{.*?}}|<[^>]*>", re.S)
# A second sentence or clause after the first: a reason or a next step.
NEXT_STEP = re.compile(r"[.:;!?]\s+\S")

# Messages that may stay one bare sentence, by template. Pages other open work
# was changing when this guard was added keep theirs until it lands; remove
# an entry when its page is converted, and never add one without review.
ALLOWED = {
    # Pages deferred until the pull requests changing them merge (#227).
    "directory.html": {"No matching Families."},
    "participation.html": {"No campaign dates to show."},
    # The shared chart's exact-values table; the page around each chart
    # says what it covers.
    "components/chart.html": {"Nothing to show yet."},
}


def template(name):
    """The text of the Admin template ``name``."""
    return (TEMPLATES / name).read_text()


SCANNED = ADMIN_TEMPLATES + sorted(
    f"components/{path.name}" for path in (TEMPLATES / "components").glob("*.html")
)


def messages(branch):
    """The messages of one empty branch: its translated strings, or, when it
    has none, its literal text, so an untranslated message is still read."""
    found = ["".join(groups) for groups in MESSAGE.findall(branch)]
    literal = " ".join(MARKUP.sub(" ", branch).split())
    return found or ([literal] if literal else [])


def bare_messages(text):
    """Each empty-branch message in template ``text`` with no reason or next
    step; template comments are skipped."""
    text = COMMENT.sub("", text)
    return {
        message
        for branch in EMPTY.finditer(text)
        for message in messages(branch.group(0))
        if not NEXT_STEP.search(message)
    }


def test_the_scan_finds_the_empty_branches():
    """The scan sees known empty branches, so a silent regex change fails."""
    text = template("deliveries.html")
    branches = [branch.group(0) for branch in EMPTY.finditer(text)]
    assert any("No email matches this view." in branch for branch in branches)
    # The chart's reviewed exception stays bare, so it always has one.
    assert bare_messages(template("components/chart.html")) == {"Nothing to show yet."}


@pytest.mark.parametrize("name", SCANNED)
def test_empty_tables_and_lists_say_why_and_what_next(name):
    """An empty table or list explains itself and names the next step."""
    assert bare_messages(template(name)) <= ALLOWED.get(name, set())


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_empty_state_allowances_are_still_needed(name):
    """Each allowance still matches a bare message; remove it once converted."""
    assert bare_messages(template(name)) == ALLOWED[name]


@pytest.mark.parametrize(
    "bare",
    [
        '{% empty %}<tr><td>{% translate "No users." %}</td></tr>',
        "{% empty %}<tr><td>{% translate 'No users.' %}</td></tr>",
        "{% empty %}<li>{% blocktranslate %}No users.{% endblocktranslate %}</li>",
        '{% empty %}{% if a %}<p>{% translate "No users." %}</p>{% endif %}',
        "{% empty %}<tr><td>No users.</td></tr>",
    ],
)
def test_the_guard_catches_a_bare_message(bare):
    """A bare "No users." fails in each form a template can write it."""
    assert bare_messages(bare) == {"No users."}


def test_the_guard_passes_a_message_with_a_next_step():
    """The same message with a next step passes, and a caller's message
    (a variable, as in components/table.html) is not read as literal text."""
    good = (
        '{% empty %}<tr><td>{% translate "No users yet. Select New user." %}</td></tr>'
    )
    assert bare_messages(good) == set()
    assert bare_messages("{% empty %}<tr><td>{{ table_empty }}</td></tr>") == set()
    # A cell value after the loop's end is not the empty branch's message.
    after = '{% empty %}{% endfor %}<p>{% translate "Total." %}</p>'
    assert bare_messages(after) == set()
