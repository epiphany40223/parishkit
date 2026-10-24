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

import functools
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
# Lower or remove an entry when its page is converted; never raise one or add
# a page. Every page is converted now, so the list is empty.
LONG_PARAGRAPH_ALLOWED = {}

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
# Pages whose introduction keeps visible words, with the count each may
# keep: two pages both portals render (denied.html and availability.html),
# whose visible text is the error itself. Each uses the Admin layout when
# ``admin`` is set and the Family layout otherwise, and one template serves
# both, so their wording is kept as a reviewed exception. Lower or remove an
# entry when its page is converted; never raise one or add a page.
# denied.html renders one refusal kind per page ({% if kind == ... %}), so
# it is counted per kind (``intro_words_by_kind``) and each kind keeps its
# own count: a kind's text can never grow into another kind's allowance.
# The pre-#2116 kinds keep their old sizes (31 words together, as the whole
# page was once counted); the Admin sign-in refusal kinds (#2116) carry the
# Administrator-approved text that is the error itself. "" is the
# ``{% else %}`` (generic) kind.
INTRO_ALLOWED = {
    "denied.html": {
        "code": 8,
        "link": 9,
        "unavailable": 8,
        "no_access": 58,
        "unfinished": 22,
        "rate_limited": 7,
        "": 6,
    },
    "availability.html": 21,
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
# Reviewed exceptions: filter fields an Admin types an identifier into. Each
# is (template, term).
INTERNAL_ALLOWED = {
    ("logs.html", "correlation"),
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


def test_long_help_allowances_are_still_needed():
    """An allowance shrinks with its page, so the list cannot hide new text.

    One test over the whole list rather than one per entry, so an empty list
    still runs (pytest reports an empty parameter set as a skip).
    """
    stale = sorted(
        name
        for name, words in LONG_PARAGRAPH_ALLOWED.items()
        if longest_visible_message(name) != words
    )
    assert not stale, f"update or remove LONG_PARAGRAPH_ALLOWED entries: {stale}"


VALUE = re.compile(r"{{.*?}}", re.S)


def intro_words(source):
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


# One branch of the {% if kind == "..." %} chain a page renders in its
# content block; {% else %} is the default kind, named "".
KIND_BRANCH = re.compile(r'{% (?:if|elif) kind == "(\w+)" %}|{% else %}')


def intro_words_by_kind(name):
    """``intro_words`` for each ``kind`` a page renders, by kind name.

    A page whose content block starts its body with a ``{% if kind == ... %}``
    chain (denied.html) shows only one branch per render, so each branch is
    scanned as if it were the page: the text before the chain, that branch,
    and the text after the chain's ``{% endif %}``. The chain holds no nested
    ``{% if %}``, so its first ``{% endif %}`` closes it. A page without such a
    chain is one kind, "".
    """
    source = read(name)
    head, marker, content = source.partition("{% block content %}")
    first = KIND_BRANCH.search(content) if marker else None
    if first is None or first.group(0).startswith("{% else"):
        return {"": intro_words(source)}
    before, chain = content[: first.start()], content[first.start() :]
    chain, _, after = chain.partition("{% endif %}")
    marks = list(KIND_BRANCH.finditer(chain))
    return {
        mark.group(1) or "": intro_words(
            head
            + marker
            + before
            + chain[mark.end() : marks[i + 1].start() if i + 1 < len(marks) else None]
            + after
        )
        for i, mark in enumerate(marks)
    }


def visible_intro_words(name):
    """The most intro words any one rendered kind of the page shows."""
    return max(intro_words_by_kind(name).values())


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
    allowed = INTRO_ALLOWED.get(name, INTRO_WORDS)
    for kind, words in intro_words_by_kind(name).items():
        limit = allowed.get(kind, INTRO_WORDS) if isinstance(allowed, dict) else allowed
        assert words <= limit, (
            f"{name} ({kind or 'default'} kind): {words} words of help show "
            "between the heading and the page's data; move them into the "
            "page's About panel ({% aboutpage %}) and keep at most a one-line "
            "hint that prevents a likely mistake"
        )


@pytest.mark.parametrize("name", sorted(INTRO_ALLOWED))
def test_intro_allowances_are_still_needed(name):
    """An introduction allowance shrinks with its page."""
    allowed = INTRO_ALLOWED[name]
    counts = intro_words_by_kind(name)
    found = counts if isinstance(allowed, dict) else counts[""]
    assert found == allowed, f"{name}: update or remove its INTRO_ALLOWED entry"


# {% include %} of another template in this directory, by its file name.
INCLUDE = re.compile(r"""{% include ["']stewardship/([^"']+)["']""")
# A live-status mirror source: an inert <template> that live-status-v1.js
# copies into the element whose data-live-mirror-target has the same name.
# It is never shown where it sits; its copy is shown where the target is.
MIRROR = re.compile(
    r'<template\b[^>]*\bdata-live-mirror="([^"]+)"[^>]*>.*?</template>', re.S
)
MIRROR_TARGET = re.compile(r'\bdata-live-mirror-target="([^"]+)"')


@functools.cache
def technical_scan(root):
    """Every template's source, and what of it shows outside Technical details.

    Returns two dicts by file name: the comment-free source, and that source
    with its Technical details disclosures removed, along with each mirror
    source (MIRROR) whose every target, in any template, sits inside a
    Technical details disclosure, since its copy is only ever shown there. A
    target whose name is built from a value (``{{ ... }}``) outside a
    disclosure is taken to match any mirror name its fixed text allows.
    Cached by template directory, so a test can point it elsewhere.
    """
    sources = {
        path.name: COMMENT.sub("", path.read_text()) for path in root.glob("*.html")
    }
    outside = {name: TECHNICAL.sub("", source) for name, source in sources.items()}
    shown_targets = [
        re.compile(".+".join(map(re.escape, VALUE.split(target))))
        for source in outside.values()
        for target in MIRROR_TARGET.findall(source)
    ]
    hidden = {
        target
        for source in sources.values()
        for target in MIRROR_TARGET.findall(source)
        if not any(pattern.fullmatch(target) for pattern in shown_targets)
    }
    shown = {
        name: MIRROR.sub(lambda m: "" if m[1] in hidden else m[0], source)
        for name, source in outside.items()
    }
    return sources, shown


def outside_technical_details(name):
    """The part of a template that can show outside Technical details.

    A template that other templates include, and that every including
    template includes only inside a Technical details disclosure (or inside
    a mirror source copied only into one), shows nothing outside it: a
    fragment of a disclosure's rows (#2016) is checked where it is used.
    One include anywhere else makes its whole source count as shown. Only
    direct includes are followed, so a fragment of a fragment counts as
    shown; split it differently or extend the scan if that is ever needed.
    """
    sources, shown = technical_scan(TEMPLATES)
    included = any(name in INCLUDE.findall(source) for source in sources.values())
    if included and not any(name in INCLUDE.findall(s) for s in shown.values()):
        return ""
    return shown[name]


@pytest.mark.parametrize("name", ADMIN_TEMPLATES)
def test_internal_field_names_are_under_technical_details(name):
    """Internal identifier and worker fields are hidden until asked for."""
    outside = outside_technical_details(name)
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
    outside = outside_technical_details(name)
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


def test_the_intro_guard_counts_each_kind_alone(tmp_path, monkeypatch):
    """Each ``kind`` branch is counted as its own page, never summed."""
    one = '<h1>A</h1><p>{% translate "One two three." %}</p>'
    two = '<h1>B</h1><p>{% translate "One two." %}</p>'
    (tmp_path / "kinds.html").write_text(
        '{% block title %}{% if kind == "x" %}X{% endif %}{% endblock %}'
        '{% block content %}<section>{% if kind == "a" %}'
        + one
        + '{% elif kind == "b" %}'
        + two
        + '{% else %}<h1>C</h1>{% endif %}<p>{% translate "Shared tail." %}</p>'
        "<form></form></section>{% endblock %}"
    )
    monkeypatch.setitem(globals(), "TEMPLATES", tmp_path)
    assert intro_words_by_kind("kinds.html") == {"a": 5, "b": 4, "": 2}
    assert visible_intro_words("kinds.html") == 5


def test_the_technical_guard_follows_includes(tmp_path, monkeypatch):
    """A fragment used only inside Technical details passes; one other use fails.

    The fragment is used as production-progress.html uses its rows: inside
    the disclosure, and in a mirror source whose target is that disclosure.
    """
    row = '<dt>{% translate "Worker task state" %}</dt>'
    include = '{% include "stewardship/rows.html" %}'
    hidden = (
        '<details class="technical-details"><dl data-live-mirror-target="m">'
        + include
        + "</dl></details>"
    )
    mirror = '<template data-live-mirror="m">' + include + "</template>"
    cases = {
        "inside": {"page.html": hidden + mirror},
        "also-outside": {"page.html": hidden + mirror, "other.html": include},
        "mirror-shown": {
            "page.html": hidden + mirror,
            "other.html": '<dl data-live-mirror-target="{{ x }}"></dl>',
        },
        "not-included": {},
    }
    found = {}
    for case, pages in cases.items():
        root = tmp_path / case
        root.mkdir()
        (root / "rows.html").write_text(row)
        for page, source in pages.items():
            (root / page).write_text(source)
        monkeypatch.setitem(globals(), "TEMPLATES", root)
        found[case] = bool(messages(outside_technical_details("rows.html")))
    assert found == {
        "inside": False,
        "also-outside": True,
        "mirror-shown": True,
        "not-included": True,
    }
