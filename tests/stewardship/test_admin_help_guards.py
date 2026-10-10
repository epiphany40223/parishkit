"""Guards that keep Admin pages to the layered-help pattern (#227).

The Admin portal spec's Page help section asks that longer explanation sit in
an "About this page" panel or a click-to-open field tip, that a page's
introduction (the text between its heading and its data) be help hidden in
that closed panel rather than paragraphs above the data, and that internal
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
# Family-facing and public pages have their own help rules; every other page
# is Admin (including the setup wizard). The maintenance page is what
# parishioners see while an Administrator has closed the Family portal, and
# the unavailable-file page is what anyone opening a deleted hosted file
# sees; neither is an Admin page (the same list as test_admin_javascript_gate).
FAMILY_PAGES = {
    "family.html",
    "family-login.html",
    "family-maintenance.html",
    "family-unavailable.html",
    "hosted-file-unavailable.html",
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
    "setup-content.html": 60,
    "setup-source-progress.html": 67,
    "users.html": 72,
}

# The page's introduction: everything between its heading and the first
# block of data or controls (a form, table, list, section, panel or included
# component). Help there must sit in the closed About panel; only a one-line
# hint that prevents a likely mistake may stay visible.
HEADER_END = re.compile(
    r"<(?:form|table|section|dl|ul|ol|div|fieldset|aside)\b|{% include "
)
# The notice class as a whole class name: "notice-error" alone is not one.
NOTICE_CLASS = r'\bclass="(?:[^"]*\s)?notice(?=[\s"])[^"]*"'
NOTICE = re.compile(r"<p\b[^>]*" + NOTICE_CLASS + r"[^>]*>.*?</p>", re.S)
# A notice block (a <div class="notice"> holding paragraphs) is skipped like
# a notice paragraph. It must not end the introduction: a page that opens with
# one would otherwise hide every help paragraph after it from this scan.
NOTICE_BLOCK = re.compile(r"<div\b[^>]*" + NOTICE_CLASS + r"[^>]*>.*?</div>", re.S)
LINK = re.compile(r"<a\b.*?</a>", re.S)
# About one line of visible introductory help.
INTRO_WORDS = 15
# Pages whose introduction is not yet converted, with the visible
# introductory words each may keep: pages other work was changing when the
# rest were converted (#227 follow-ups), and the two pages both portals render
# (error.html and denied.html), whose visible text is the error itself. Each
# uses the Admin layout when ``admin`` is set and the Family layout otherwise,
# and one template serves both, so their wording is kept as a reviewed
# exception. Lower or remove an entry when its page is converted; never raise
# one or add a page.
INTRO_ALLOWED = {
    "logs-error.html": 94,
    "ministry-followup-error.html": 38,
    "setup-content.html": 60,
    # The step-up names its return page when it is another page (#547).
    "error.html": 61,
    "setup.html": 46,
    "ministries.html": 38,
    "denied.html": 31,
    "go-live-readiness.html": 24,
    "delivery-control.html": 24,
    "availability.html": 21,
    "go-live-links.html": 20,
    "talents-report-error.html": 16,
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
    assert set(INTRO_ALLOWED) <= set(ADMIN_TEMPLATES)
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


VALUE = re.compile(r"{{.*?}}", re.S)


def visible_intro_words(name):
    """Words of help sentences shown between a page's heading and its data.

    The About panel and any other disclosure are one deliberate click away,
    so they are left out, as are safety notices (``<p class="notice">``, and
    ``<div class="notice">`` blocks, which do not end the introduction) and
    link text. Only messages that read as sentences (ending in ``.``, ``!``
    or ``?``) count, so labels such as "Status:" do not; inside a counted
    message, the values it shows (``{{ ... }}``) are not words of help.

    Limits, by design of a text scan: the introduction ends at the first
    form, table, list, section, panel, other ``<div>`` or ``{% include %}``, so help
    placed after that, or inside an included template, is not checked here
    (the long-paragraph guard still applies); help in a ``<p class="help">``
    hint counts like any paragraph; and text outside ``<p>`` elements, or not
    marked for translation, is not seen.
    """
    source = read(name)
    if "</h1>" not in source:
        return 0
    after = NOTICE_BLOCK.sub(
        "", DETAILS.sub("", ABOUT.sub("", source.split("</h1>", 1)[1]))
    )
    end = HEADER_END.search(after)
    intro = LINK.sub("", NOTICE.sub("", after[: end.start()] if end else after))
    return sum(
        # A token left as bare punctuation (" — ", "." after a value) is not a word.
        sum(1 for token in VALUE.sub("", message).split() if re.search(r"\w", token))
        for paragraph in PARAGRAPH.findall(intro)
        for message in messages(paragraph)
        if message.strip()[-1:] in ".!?"
    )


# The "About this page" control sits beside the page heading (ui-v1.css puts
# a panel that directly follows an h1 on the heading's line). The
# guarantee is structural: the tag comes right after </h1>, optionally inside
# one {% if %} that holds only the panel, so nothing can be drawn between them.
ABOUT_AFTER_HEADING = re.compile(r"</h1>\s*(?:{% if [^%]*%}\s*)?{% aboutpage ", re.S)
ABOUT_PAGES = sorted(name for name in ADMIN_TEMPLATES if "{% aboutpage " in read(name))


def test_the_about_scan_finds_the_panels():
    """A renamed tag must not make the placement guard pass vacuously."""
    assert len(ABOUT_PAGES) > 30


@pytest.mark.parametrize("name", ABOUT_PAGES)
def test_about_panel_directly_follows_the_page_heading(name):
    """One panel per page, placed right after the h1 so it sits beside it."""
    source = read(name)
    assert source.count("{% aboutpage ") == 1, f"{name}: one About panel per page"
    assert ABOUT_AFTER_HEADING.search(source), (
        f"{name}: put {{% aboutpage %}} directly after the page's </h1>, so "
        "its control sits beside the heading; included templates may not "
        "hold the panel"
    )


@pytest.mark.parametrize("name", ADMIN_TEMPLATES)
def test_page_introduction_is_in_the_about_panel(name):
    """A page's data comes first; its introductory help waits in the panel."""
    words = visible_intro_words(name)
    assert words <= INTRO_ALLOWED.get(name, INTRO_WORDS), (
        f"{name}: {words} words of help show between the heading and the "
        "page's data; move them into the page's About panel ({% aboutpage %}) "
        "and keep at most a one-line hint that prevents a likely mistake"
    )


@pytest.mark.parametrize("name", sorted(INTRO_ALLOWED))
def test_intro_allowances_are_still_needed(name):
    """An introduction allowance shrinks with its page."""
    assert visible_intro_words(name) == INTRO_ALLOWED[name], (
        f"{name}: update or remove its INTRO_ALLOWED entry"
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


def test_the_intro_guard_catches_what_it_describes(tmp_path, monkeypatch):
    """Intro help is flagged above the data, and passes in the panel or below."""
    lead = '<p>{% translate "' + "word " * 20 + 'end." %}</p>'
    pages = {
        "flagged.html": "<h1>T</h1>" + lead + "<form></form>",
        "in-panel.html": '<h1>T</h1>{% aboutpage "t" %}'
        + lead
        + "{% endaboutpage %}<form></form>",
        "below-data.html": "<h1>T</h1><table></table>" + lead,
        "data-line.html": "<h1>T</h1><p>{% blocktranslate %}Data through "
        + "{{ day }}.{% endblocktranslate %}</p>",
        "notice.html": '<h1>T</h1><p class="notice">'
        + lead[3:-4]
        + "</p><form></form>",
        "notice-block.html": '<h1>T</h1><div class="notice" role="alert">'
        + lead
        + "</div><form></form>",
        "not-a-notice.html": '<h1>T</h1><p class="notice-error">'
        + lead[3:-4]
        + "</p><form></form>",
        "after-notice-block.html": '<h1>T</h1><div class="notice">'
        + lead
        + "</div>"
        + lead
        + "<form></form>",
    }
    for page, source in pages.items():
        (tmp_path / page).write_text(source)
    monkeypatch.setitem(globals(), "TEMPLATES", tmp_path)
    assert visible_intro_words("flagged.html") > INTRO_WORDS
    for page in (
        "in-panel.html",
        "below-data.html",
        "notice.html",
        "notice-block.html",
    ):
        assert visible_intro_words(page) == 0, page
    # A notice block is skipped, but does not end the introduction.
    assert visible_intro_words("after-notice-block.html") > INTRO_WORDS
    # Only the whole class name "notice" marks a notice.
    assert visible_intro_words("not-a-notice.html") > INTRO_WORDS
    # A value is not words of help; the words around it still count.
    assert visible_intro_words("data-line.html") == 2
