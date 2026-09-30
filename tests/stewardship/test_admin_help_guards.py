"""Guards that keep Admin pages to the layered-help pattern (#227).

The Admin portal spec's Page help section asks that longer explanation sit in
an "About this page" panel or a click-to-open field tip, and that internal
identifiers and bookkeeping fields sit in a closed "Technical details"
disclosure. These tests read the Admin templates as text, so they need no
database: they check each translatable message against where it is placed.

Both checks are deliberately simple text scans rather than a template render,
so they cover every branch of every template, including branches no fixture
reaches. Their allowlists name the known, reviewed exceptions; a new entry
should be rare and explained.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
# Family-facing pages have their own help rules; every other page is Admin
# (including the setup wizard and the Admin's Family portal maintenance page).
FAMILY_PAGES = {
    "family.html",
    "family-login.html",
    "family-unavailable.html",
}
ADMIN_TEMPLATES = sorted(
    path.name for path in TEMPLATES.glob("*.html") if path.name not in FAMILY_PAGES
)

COMMENT = re.compile(r"{% comment %}.*?{% endcomment %}", re.S)
ABOUT = re.compile(r"{% aboutpage .*?{% endaboutpage %}", re.S)
# Any disclosure (About panel markup, Technical details, export panels and
# similar) is one deliberate click away, so its text is not "always visible".
DETAILS = re.compile(r"<details\b.*?</details>", re.S)
# Any <details> whose class list includes technical-details, whatever other
# attributes or classes it gains later.
TECHNICAL = re.compile(
    r'<details\b[^>]*\bclass="[^"]*\btechnical-details\b[^"]*"[^>]*>.*?</details>',
    re.S,
)
PARAGRAPH = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S)
MESSAGE = re.compile(
    r'{% (?:translate|trans) "((?:[^"\\]|\\.)*)"'
    r"|{% blocktranslate[^%]*%}(.*?){% (?:plural|endblocktranslate)",
    re.S,
)

# About two short sentences. Longer always-visible help belongs in an About
# panel or a field tip.
LONG_PARAGRAPH_WORDS = 50
# Pages not yet converted, with the longest visible message each may keep.
# Lower or remove an entry when its page is converted; never raise one.
LONG_PARAGRAPH_ALLOWED = {
    "chair-confirmation-preview.html": 51,
    "chair-review-preview.html": 55,
    "schedule-settings.html": 53,
    "setup-content.html": 60,
    "setup-credential.html": 53,
    "setup-preview.html": 52,
    "setup-source-progress.html": 67,
    "setup-source.html": 61,
    "users.html": 99,
}

# Labels for internal identifiers and worker bookkeeping. They may appear
# only inside a Technical details disclosure.
INTERNAL_TERMS = (
    "heartbeat",
    "lease expires",
    "initiator id",
    "retry sequence",
    "source generation",
    "submission sequence",
    "watermark",
    "impact revision",
    "committed checkpoint",
    "committed group",
    "worker task state",
    "task id",
    "request id",
    "refusal id",
    "correlation",
)
# Reviewed exceptions: filter fields an Admin types an identifier into, and a
# sortable list column. Each is (template, term).
INTERNAL_ALLOWED = {
    ("logs.html", "correlation"),
    ("delivery-refusals.html", "refusal id"),
}


def messages(source):
    """Every translatable message in ``source``, as plain text."""
    return [single or block for single, block in MESSAGE.findall(source)]


def read(name):
    """A template's source with template comments removed."""
    return COMMENT.sub("", (TEMPLATES / name).read_text())


def test_the_scan_finds_the_admin_templates():
    """A moved template directory must not make both guards pass vacuously."""
    assert len(ADMIN_TEMPLATES) > 100
    assert "background-task.html" in ADMIN_TEMPLATES
    assert set(LONG_PARAGRAPH_ALLOWED) <= set(ADMIN_TEMPLATES)
    assert {name for name, _ in INTERNAL_ALLOWED} <= set(ADMIN_TEMPLATES)


def longest_visible_message(name):
    """Word count of the longest message in a paragraph shown without a click."""
    visible = DETAILS.sub("", ABOUT.sub("", read(name)))
    return max(
        (
            len(message.split())
            for paragraph in PARAGRAPH.findall(visible)
            for message in messages(paragraph)
        ),
        default=0,
    )


@pytest.mark.parametrize("name", ADMIN_TEMPLATES)
def test_long_help_is_not_always_visible(name):
    """No visible paragraph holds a message longer than about two sentences."""
    longest = longest_visible_message(name)
    assert longest <= LONG_PARAGRAPH_ALLOWED.get(name, LONG_PARAGRAPH_WORDS), (
        f"{name}: a visible paragraph has a {longest}-word message; move it "
        "into an About panel ({% aboutpage %}) or a field tip, or shorten it"
    )


@pytest.mark.parametrize("name", sorted(LONG_PARAGRAPH_ALLOWED))
def test_long_help_allowances_are_still_needed(name):
    """An allowance shrinks with its page, so the list cannot hide new text."""
    assert longest_visible_message(name) == LONG_PARAGRAPH_ALLOWED[name], (
        f"{name}: update or remove its LONG_PARAGRAPH_ALLOWED entry"
    )


@pytest.mark.parametrize("name", ADMIN_TEMPLATES)
def test_internal_field_names_are_under_technical_details(name):
    """Internal identifier and worker fields are hidden until asked for."""
    outside = TECHNICAL.sub("", read(name))
    found = sorted(
        {
            term
            for message in messages(outside)
            for term in INTERNAL_TERMS
            if term in message.lower() and (name, term) not in INTERNAL_ALLOWED
        }
    )
    assert not found, (
        f"{name}: {', '.join(found)} shown outside a Technical details "
        'disclosure (<details class="technical-details">)'
    )


@pytest.mark.parametrize(
    ("name", "term"), sorted(INTERNAL_ALLOWED), ids=lambda value: value
)
def test_internal_term_exceptions_are_still_needed(name, term):
    """A stale exception is removed rather than left to hide a new use."""
    outside = TECHNICAL.sub("", read(name))
    assert any(term in message.lower() for message in messages(outside)), (
        f"{name}: remove ({name!r}, {term!r}) from INTERNAL_ALLOWED"
    )


def test_the_guards_catch_what_they_describe():
    """Each check flags a sample it should, and passes the fixed version."""
    wall = '<p>{% translate "' + "word " * 60 + '" %}</p>'
    about = '{% aboutpage "x" %}' + wall + "{% endaboutpage %}"
    assert max(len(m.split()) for m in messages(wall)) > LONG_PARAGRAPH_WORDS
    assert not PARAGRAPH.findall(DETAILS.sub("", ABOUT.sub("", about)))
    internal = '<dt>{% translate "Lease expires" %}</dt>'
    hidden = '<details class="technical-details">' + internal + "</details>"
    assert "lease expires" in messages(internal)[0].lower()
    assert not messages(TECHNICAL.sub("", hidden))
    tagged = '<details data-x class="compact technical-details">' + internal
    assert not messages(TECHNICAL.sub("", tagged + "</details>"))
