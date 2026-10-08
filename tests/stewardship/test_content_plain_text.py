"""Generated plain text and the Welcome default's apostrophe (#385)."""

import hashlib

from parishkit.stewardship.accounts import content_defaults
from parishkit.stewardship.web.content import (
    _legacy_generated,
    _PlainText,
    generated_text_matches,
    prepare_content,
    sanitize_html,
)

INDENTED = "<ul>\n  <li>x</li>\n  <li>y</li>\n</ul>"


def _parts(html):
    """The extracted parts of sanitized HTML, as prepare_content reads them."""
    parser = _PlainText()
    parser.feed(sanitize_html(html))
    return parser.parts


def test_indented_source_leaves_no_whitespace_only_lines():
    """An indented list gives clean lines, as an unindented one does."""
    assert prepare_content(INDENTED).text == "- x\n- y"
    assert prepare_content("<ul><li>x</li><li>y</li></ul>").text == "- x\n- y"


def test_text_from_the_earlier_generator_still_counts_as_generated():
    """Content saved before #385 does not suddenly look hand-edited."""
    legacy = _legacy_generated(_parts(INDENTED))
    assert legacy != prepare_content(INDENTED).text
    assert generated_text_matches(INDENTED, legacy)
    assert generated_text_matches(INDENTED, prepare_content(INDENTED).text)
    assert not generated_text_matches(INDENTED, "Hand-written")


def test_every_default_generates_the_same_text_either_way():
    """The defaults have no indented source, so matches_default is unaffected.

    Covers the current and retired defaults, including both confirmation
    variants (with and without a pledge).
    """
    retired_emails = [e for es in content_defaults.RETIRED_EMAILS.values() for e in es]
    confirmations = [
        content_defaults._confirmation(content_defaults._GIVE_ONLINE),
        content_defaults._confirmation(content_defaults._GIVE_ONLINE_PLEDGE),
    ]
    for html in (
        *content_defaults.PAGES.values(),
        *(email.html for email in content_defaults.EMAILS.values()),
        *(html for pages in content_defaults.RETIRED_PAGES.values() for html in pages),
        *(email.html for email in [*retired_emails, *confirmations]),
    ):
        assert _legacy_generated(_parts(html)) == prepare_content(html).text


def test_the_welcome_heading_uses_a_curly_apostrophe():
    """The new default is curly; the straight one is a retired default."""
    assert "{{ parish_name }}’s" in content_defaults.PAGES["welcome"]
    retired = content_defaults.retired_data("page", "welcome")
    assert retired and "{{ parish_name }}'s" in retired[0]["html"]


def test_the_retired_welcome_default_is_frozen():
    """The retired Welcome page is exactly the text shipped before #385."""
    (retired,) = content_defaults.RETIRED_PAGES["welcome"]
    digest = hashlib.sha256(retired.encode()).hexdigest()
    assert digest == "53673b6a9e0fb83d8d2def926c858ec6f6421accbe9cea669554b1de110a5b9f"
