"""Guards that keep Admin pages to the layered-help pattern (#227).

The Admin portal spec's Page help section asks that longer explanation sit in
an "About this page" panel or a click-to-open field tip, that a page's
introduction (the text between its heading and its data) be help hidden in
that closed panel rather than paragraphs above the data, and that no Admin
page shows internal identifiers and bookkeeping fields or a "Technical
details" pane (#2199). These tests read the Admin templates as text, so they need no
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
# The pane and internal-term guards also read the nested templates
# (components/, widgets/), where a removed pane once lived; the other guards
# are written for whole pages. Family pages are skipped as above.
ALL_ADMIN_TEMPLATES = sorted(
    str(path.relative_to(TEMPLATES))
    for path in TEMPLATES.rglob("*.html")
    if path.name not in FAMILY_PAGES
)

COMMENT = re.compile(r"{% comment %}.*?{% endcomment %}", re.S)
ABOUT = re.compile(r"{% aboutpage .*?{% endaboutpage %}", re.S)
# Any disclosure (About panel markup, export panels and similar) is one
# deliberate click away, so its text is not "always visible".
DETAILS = re.compile(r"<details\b.*?</details>", re.S)
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
# ``{% else %}`` (generic) kind. The Check your other email addresses kinds
# (#2343) carry the reviewed text of the design (#2361), which is the
# sign-in page itself: what the check asks Google for and what is kept.
INTRO_ALLOWED = {
    "denied.html": {
        "address_check": 101,
        "address_check_again": 36,
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

# Labels for internal identifiers and worker bookkeeping. No Admin page shows
# them (UX conventions, "No Technical details panes", #2199): the logs and
# pk-admin do.
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
    "configuration digest",
    "configuration identity",
    "checksum",
    "sha-256",
    "delivery id",
    "test reference",
)
# Reviewed exceptions: filter fields an Admin types an identifier into, and
# prose that names a kept record without showing it. Each is (template, term).
INTERNAL_ALLOWED = {
    ("logs.html", "correlation"),
    # Sent mail's recipient filter accepts a delivery ID typed by hand.
    ("deliveries.html", "delivery id"),
    # Remove's dialog says the import's record (with its checksum) is kept.
    ("past-campaigns.html", "checksum"),
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


@pytest.mark.parametrize("name", ALL_ADMIN_TEMPLATES)
def test_no_admin_template_has_a_technical_details_pane(name):
    """No Admin page draws a Technical details pane (#2199).

    The whole source is read, template comments included, so a pane cannot
    come back under a comment's cover, and both the words and the class the
    panes used count.
    """
    source = (TEMPLATES / name).read_text()
    assert not re.search(r"technical[ -]details", source, re.I), (
        f"{name}: Admin pages have no Technical details pane (UX conventions, "
        '"No Technical details panes"); keep a fact a reader needs as a plain '
        "line, and leave internal references to the logs and pk-admin"
    )


def test_the_admin_scripts_and_styles_draw_no_technical_details():
    """Neither the Admin scripts nor the styles keep a pane's code (#2199)."""
    static = TEMPLATES.parents[1] / "static/stewardship"
    files = sorted([*static.glob("*.js"), *static.glob("*.css")])
    assert files
    found = [
        path.name
        for path in files
        if re.search(r"technical[ -]details", path.read_text(), re.I)
    ]
    assert not found, f"Technical details code is left in {found}"


def internal_terms_shown(name):
    """The internal terms ``name`` shows that are not reviewed exceptions."""
    return sorted(
        {
            term
            for message in messages(read(name))
            for term in INTERNAL_TERMS
            if term in message.lower() and (name, term) not in INTERNAL_ALLOWED
        }
    )


@pytest.mark.parametrize("name", ALL_ADMIN_TEMPLATES)
def test_internal_field_names_are_not_shown(name):
    """Internal identifier and worker fields are not on any Admin page."""
    found = internal_terms_shown(name)
    assert not found, (
        f"{name}: {', '.join(found)} shown on an Admin page; leave internal "
        "references to the logs and pk-admin"
    )


@pytest.mark.parametrize(
    ("name", "term"), sorted(INTERNAL_ALLOWED), ids=lambda value: value
)
def test_internal_term_exceptions_are_still_needed(name, term):
    """A stale exception is removed rather than left to hide a new use."""
    assert any(term in message.lower() for message in messages(read(name))), (
        f"{name}: remove ({name!r}, {term!r}) from INTERNAL_ALLOWED"
    )


def test_the_guards_catch_what_they_describe():
    """Each check flags a sample it should, and passes the fixed version."""
    wall = '<p>{% translate "' + "word " * 60 + '" %}</p>'
    about = '{% aboutpage "x" %}' + wall + "{% endaboutpage %}"
    assert max(len(m.split()) for m in messages(wall)) > LONG_PARAGRAPH_WORDS
    assert not PARAGRAPH.findall(DETAILS.sub("", ABOUT.sub("", about)))
    internal = '<dt>{% translate "Lease expires" %}</dt>'
    assert "lease expires" in messages(internal)[0].lower()


def test_the_internal_term_guard_flags_a_bad_template(tmp_path, monkeypatch):
    """A template showing an internal label fails the guard; a plain one passes."""
    (tmp_path / "bad.html").write_text('<dt>{% translate "Lease expires" %}</dt>')
    (tmp_path / "fine.html").write_text('<dt>{% translate "Started" %}</dt>')
    monkeypatch.setitem(globals(), "TEMPLATES", tmp_path)
    with pytest.raises(AssertionError):
        test_internal_field_names_are_not_shown("bad.html")
    test_internal_field_names_are_not_shown("fine.html")


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


def test_the_pane_guard_catches_what_it_describes(tmp_path, monkeypatch):
    """The pane guard flags the words or the class, even in a comment."""
    pages = {
        "words.html": '<details><summary>{% translate "Technical details" %}'
        "</summary></details>",
        "class.html": '<details class="compact technical-details"></details>',
        "comment.html": "{% comment %}Technical details live here.{% endcomment %}",
        "fine.html": '<details class="inline-help"><summary>Help</summary></details>',
    }
    for page, source in pages.items():
        (tmp_path / page).write_text(source)
    monkeypatch.setitem(globals(), "TEMPLATES", tmp_path)
    for page in ("words.html", "class.html", "comment.html"):
        with pytest.raises(AssertionError):
            test_no_admin_template_has_a_technical_details_pane(page)
    test_no_admin_template_has_a_technical_details_pane("fine.html")


# One help line under each Admin field (#2130; UX conventions, "Page
# help"): a field's visible help is one short line, about 25 words at most,
# in one paragraph; anything longer sits in the field's "i" toggletip or is
# left out. Two scans: the help lines Admin templates draw (``class="help"``
# and Django's ``helptext``), and the help every Admin form field shows as
# its form builds it.
FIELD_HELP_WORDS = 25
HELP_LINE = re.compile(
    r'<(p|div|span)\b[^>]*\bclass="(?:[^"]*\s)?(?:help|helptext)(?=[\s"])[^"]*"'
    r"[^>]*>(.*?)</\1>",
    re.S,
)
TAG = re.compile(r"<[^>]+>")
# The tags that start or end one branch of a help line that says one of
# several things.
BRANCH = re.compile(r"{% (?:if|elif|else|endif)\b[^%]*%}")
# Help lines that are not a field's: a section's or a list's own guidance,
# or the reason an action is unavailable, each (template, its first words).
# They are outside #2130's field rule; never add a field's help here.
HELP_LINE_ALLOWED = {
    ("admin-banners.html", "Acknowledging hides the problems"),
    ("integration-settings.html", "Test access is not"),
    ("integrations.html", "A new key is"),
    ("ministry-packet.html", "Ministry leaders receive the"),
    ("production-progress.html", "Items processed counts work"),
    ("refresh-schedule-preview.html", "Times skipped around Family"),
    ("schedule-review.html", "A schedule that already"),
    ("schedule-settings.html", "Report emails to Administrators"),
    ("system-health-backup-button.html", "The server checks every"),
    ("system-health-clear-halt-button.html", "A mail sender reports"),
    ("users.html", "People ParishSoft lists as"),
    ("users.html", "Send each chosen Ministry"),
    # Lines that sit right after another help line but are not a second
    # paragraph of one field's help: a section's stacked guidance, a
    # status line or the alternative of two states of which one shows.
    ("refresh-schedule-editor.html", "Refreshes never overlap: one"),
    ("refresh-schedule-editor.html", "Times are in the"),
    ("report-email-entry.html", "Every block is in"),
    ("schedule-email.html", "The generated plain text"),
    ("users.html", "{{ count }} Ministry"),
}


def help_words(text):
    """Words of help in ``text``: markup, values and bare punctuation left out."""
    plain = VALUE.sub("", TAG.sub(" ", str(text)))
    return sum(1 for token in plain.split() if re.search(r"\w", token))


def help_key(name, message):
    """A help line's allowlist key: its template and its first four words."""
    return (name, " ".join(TAG.sub(" ", message).split()[:4]))


def adjacent_help(source):
    """The body of each help element that directly follows another one.

    Only whitespace may lie between the two; a tag or an ``{% if %}``
    between them means alternatives or separate things.
    """
    matches = list(HELP_LINE.finditer(source))
    return [
        after.group(2)
        for before, after in zip(matches, matches[1:], strict=False)
        if not source[before.end() : after.start()].strip()
    ]


def long_help_lines(name):
    """Each help line of a template longer than one line, as an allowlist key.

    A line is too long when its messages together (two sentences in one
    help paragraph count as one line's) have more than FIELD_HELP_WORDS
    words. A line that says one of several things (``{% if %}``,
    ``{% elif %}`` and ``{% else %}``) is counted per branch, since only one
    shows at a time; text around the branches is counted on its own.
    """
    found = []
    for _, body in HELP_LINE.findall(read(name)):
        for branch in BRANCH.split(body):
            texts = messages(branch)
            if sum(help_words(text) for text in texts) > FIELD_HELP_WORDS:
                found.append(help_key(name, texts[0]))
    # Help split into several short paragraphs is still more than one line:
    # a help element right after another one counts as long, whatever its
    # own length. Text between them (a tag, an ``{% if %}``) means they are
    # alternatives or separate things and is not counted.
    for body in adjacent_help(read(name)):
        texts = messages(body)
        if texts:
            found.append(help_key(name, texts[0]))
    return found


@pytest.mark.parametrize("name", ADMIN_TEMPLATES)
def test_each_help_line_is_one_line(name):
    """A help line under an Admin field is one short line (#2130)."""
    found = [key for key in long_help_lines(name) if key not in HELP_LINE_ALLOWED]
    assert not found, (
        f"{name}: help lines longer than about {FIELD_HELP_WORDS} words: "
        f"{[words for _, words in found]}; keep one short line under the "
        "field and move the rest into its toggletip "
        "(components/toggletip.html), or leave it out"
    )


def test_help_line_exceptions_are_still_needed():
    """A stale exception is removed rather than left to hide a new line."""
    found = {key for name in ADMIN_TEMPLATES for key in long_help_lines(name)}
    stale = sorted(HELP_LINE_ALLOWED - found)
    assert not stale, f"remove these HELP_LINE_ALLOWED entries: {stale}"


def admin_form_fields():
    """Every Admin form field and its visible help, as (form.field, help).

    Each form class of the package (the classes ``test_admin_forms`` finds)
    is built with no arguments, as a page first draws it, so the help its
    own ``__init__`` sets and moves into a tip is what is checked; a class
    that needs arguments is checked by its declared fields. Help a view
    sets after building a form is not seen here: keep that to one line, or
    give it a tip with ``field_tips``.
    """
    from django import forms

    from .test_admin_forms import stewardship_classes

    found = []
    for cls in stewardship_classes():
        if not issubclass(cls, forms.BaseForm):
            continue
        try:
            fields = cls().fields
        except Exception:  # Needs arguments: check what it declares.
            fields = cls.base_fields
        found.extend(
            (f"{cls.__module__}.{cls.__qualname__}.{name}", field.help_text)
            for name, field in fields.items()
        )
    return sorted(found)


def test_each_admin_form_field_shows_one_help_line():
    """No Admin form field shows more than one short line of help (#2130)."""
    fields = admin_form_fields()
    assert len(fields) > 150
    found = [
        f"{name} ({help_words(text)} words)"
        for name, text in fields
        if help_words(text) > FIELD_HELP_WORDS
        or re.search(r"<(?:p|br|ul|ol)\b|\n\s*\n", str(text))
    ]
    assert not found, (
        "keep one short line of help under each field and move the rest "
        f"into its tip (accounts/field_tips.py): {found}"
    )


def test_the_help_line_guard_catches_what_it_describes():
    """A long line and two sentences in one line fail; a short one passes."""
    long_line = '<p class="help">{% translate "' + "word " * 26 + '" %}</p>'
    two = (
        '<p class="help" id="x">{% translate "' + "word " * 15 + '" %} '
        "{% blocktranslate with n=3 %}At most {{ n }} "
        + "word " * 12
        + "{% endblocktranslate %}</p>"
    )
    short = (
        '<div class="label-row"><label>L</label>{% translate "'
        + "word " * 40
        + '" as tip %}</div><p class="help">{% translate "One line." %}</p>'
    )
    split = (
        '<p class="help">{% translate "' + "word " * 20 + '" %}</p>\n'
        '<p class="help">{% translate "Second line here." %}</p>'
    )
    either = (
        '{% if a %}<p class="help">{% translate "One." %}</p>{% endif %}'
        '{% if b %}<p class="help">{% translate "Two." %}</p>{% endif %}'
    )
    assert len(adjacent_help(split)) == 1 and not adjacent_help(either)
    assert [len(messages(b)) for _, b in HELP_LINE.findall(long_line)] == [1]
    assert help_words(messages(long_line)[0]) > FIELD_HELP_WORDS
    assert sum(map(help_words, messages(HELP_LINE.findall(two)[0][1]))) == 29
    assert [b for _, b in HELP_LINE.findall(short)] == ['{% translate "One line." %}']
