"""The shared email document around sanitized stewardship email bodies."""

from uuid import uuid4

from parishkit.stewardship.family_delivery import FamilyDeliveryMail
from parishkit.stewardship.mail_layout import (
    FONT,
    LINK_STYLE,
    NOTICE_STYLE,
    REPORT_WIDTH,
    TAG_STYLES,
    email_document,
)
from parishkit.stewardship.web.content import prepare_content

BODY = prepare_content(
    "<h2>Welcome</h2><p>Dear Alex,</p><ul><li>One</li></ul>"
    '<p><a href="https://example.org/">Begin</a></p><blockquote>Q</blockquote>'
)


def test_document_is_a_complete_centered_readable_page():
    """Charset, font stack, size, line height and a 600px centered container."""
    page = email_document(BODY.html)
    assert page.startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in page
    assert f"font-family:{FONT};font-size:16px;line-height:1.5;" in page
    assert "max-width:600px" in page and 'align="center"' in page
    assert '<table role="presentation" width="600"' in page  # Outlook width
    assert page.endswith("</body></html>")
    assert "<style>" in page and ".pk-body p{margin:0 0 16px;}" in page


def test_report_mail_uses_the_wider_desktop_column():
    """Admin report emails are desktop-first (#720); Outlook gets the same width."""
    page = email_document(BODY.html, width=REPORT_WIDTH)
    assert REPORT_WIDTH == 960
    assert "max-width:960px" in page and "max-width:600px" not in page
    assert '<table role="presentation" width="960"' in page


def test_bare_sanitized_tags_get_inline_styles_and_text_is_unchanged():
    """Mail programs that drop <style> still show spacing, sizes and links."""
    page = email_document(BODY.html)
    for tag in ("p", "h2", "ul", "li", "blockquote"):
        assert f'<{tag} style="{TAG_STYLES[tag]}">' in page
        assert f"<{tag}>" not in page
    assert f'<a style="{LINK_STYLE}" href="https://example.org/"' in page
    for words in ("Welcome", "Dear Alex,", "One", "Begin", ">Q<"):
        assert words in page


def test_compiler_markup_with_attributes_is_left_alone():
    """Digest tables, images and already styled links keep their own markup."""
    digest = (
        '<table class="report"><tr><td>1,234</td></tr></table>'
        '<img src="cid:chart" alt="Chart"><a style="color:red" href="x">x</a>'
    )
    assert digest in email_document(digest)


def test_routed_testing_banner_becomes_one_small_notice():
    """The retained <h2>TEST</h2> banner shows as a notice, not a big heading."""
    html = (
        "<h2>TEST</h2><p>TEST — sent to test@example.org instead of Alex &amp; "
        "Sam (a@example.org).</p><p>Body</p>"
    )
    page = email_document(html)
    assert "<h2" not in page
    assert NOTICE_STYLE in page
    assert "instead of Alex &amp; Sam (a@example.org).</td>" in page
    assert page.index(NOTICE_STYLE) < page.index("Body")
    plain = email_document("<p>Body</p>", notice="Test <only>")
    assert "Test &lt;only&gt;</td>" in plain


def test_family_message_wraps_only_the_html_alternative():
    """Plain text stays first and unchanged; the HTML part is the document."""
    mail = FamilyDeliveryMail(
        uuid4(),
        "stewardship@example.org",
        "office@example.org",
        ("family@example.org",),
        "Renewal",
        BODY.html,
        BODY.text,
    )
    message = mail.message()
    assert message.get_content_type() == "multipart/alternative"
    text, html = message.iter_parts()
    assert text.get_content_type() == "text/plain"
    assert text.get_content().rstrip("\n") == BODY.text
    assert html.get_content_type() == "text/html"
    assert html.get_content().rstrip("\n") == email_document(BODY.html)
