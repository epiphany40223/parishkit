"""The shared email document around sanitized stewardship email bodies."""

from uuid import uuid4

import pytest

from parishkit.stewardship.family_delivery import FamilyDeliveryMail
from parishkit.stewardship.mail_layout import (
    FAMILY,
    FONT,
    LINK_STYLE,
    NOTICE_STYLE,
    REPORT_WIDTH,
    SHARED,
    TAG_STYLES,
    email_document,
    notice_html,
)
from parishkit.stewardship.web.content import prepare_content
from parishkit.stewardship.web.design_tokens import COLORS

BODY = prepare_content(
    "<h2>Welcome</h2><p>Dear Alex,</p><ul><li>One</li></ul>"
    '<p><a href="https://example.org/">Begin</a></p><blockquote>Q</blockquote>'
)
# Colours only Family mail's own look uses, never the shared tokens.
FAMILY_ONLY = ("#1f2933", "#f4f5f7", "#e4e7ec", "#111827", "#1a56db", "#fff8db")


def test_document_is_a_complete_centered_readable_page():
    """Charset, font stack, size, line height and a 600px centered container."""
    page = email_document(BODY.html, family=False)
    assert page.startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in page
    assert f"font-family:{FONT};font-size:16px;line-height:1.5;" in page
    assert "max-width:600px" in page and 'align="center"' in page
    assert '<table role="presentation" width="600"' in page  # Outlook width
    assert page.endswith("</body></html>")
    assert "<style>" in page and ".pk-body p{margin:0 0 16px;}" in page


def test_report_mail_uses_the_wider_desktop_column():
    """Admin report emails are desktop-first (#720); Outlook gets the same width."""
    page = email_document(BODY.html, family=False, width=REPORT_WIDTH)
    assert REPORT_WIDTH == 960
    assert "max-width:960px" in page and "max-width:600px" not in page
    assert '<table role="presentation" width="960"' in page


@pytest.mark.parametrize("family", [False, True])
def test_bare_sanitized_tags_get_inline_styles_and_text_is_unchanged(family):
    """Mail programs that drop <style> still show spacing, sizes and links."""
    look = FAMILY if family else SHARED
    page = email_document(BODY.html, family=family)
    for tag in ("p", "h2", "ul", "li", "blockquote"):
        assert f'<{tag} style="{look.tag_styles()[tag]}">' in page
        assert f"<{tag}>" not in page
    assert f'<a style="{look.link_style()}" href="https://example.org/"' in page
    for words in ("Welcome", "Dear Alex,", "One", "Begin", ">Q<"):
        assert words in page


def test_shared_look_is_the_portal_design_tokens():
    """Every email except Family mail uses the portal's colours and shapes (#732)."""
    assert SHARED.text == SHARED.heading == COLORS["ink"]
    assert SHARED.page == COLORS["page"] and SHARED.link == COLORS["link"]
    assert SHARED.card_border == COLORS["border"] and SHARED.card_radius == "12px"
    assert SHARED.quote_text == COLORS["muted"]
    # The portal's warning notice: its colours, thick leading edge and radius.
    assert SHARED.notice.startswith(
        f"background-color:{COLORS['warning-bg']};"
        f"border:1px solid {COLORS['warning-border']};"
        "border-left-width:6px;border-radius:8px;"
    )
    assert f"color:{COLORS['warning-ink']};" in SHARED.notice
    page = email_document("<h2>TEST</h2><p>Routed.</p>" + BODY.html, family=False)
    assert not [colour for colour in FAMILY_ONLY if colour in page]
    assert f"background-color:{COLORS['page']};" in page


def test_family_look_is_unchanged_until_its_own_slice():
    """Family mail keeps its pre-#732 look byte for byte until a Testing send."""
    assert FAMILY.text == "#1f2933" and FAMILY.page == "#f4f5f7"
    assert FAMILY.card_border == "#e4e7ec" and FAMILY.card_radius == "8px"
    assert FAMILY.heading == "#111827" and FAMILY.link == "#1a56db"
    assert FAMILY.quote_rule == "#d0d5dd" and FAMILY.quote_text == "#475467"
    assert FAMILY.tag_styles() == TAG_STYLES and FAMILY.link_style() == LINK_STYLE
    assert (
        NOTICE_STYLE
        == FAMILY.notice
        == (
            "background-color:#fff8db;border:1px solid #f0d77a;border-radius:4px;"
            "padding:8px 12px;font-size:13px;line-height:1.4;color:#5c4a00;"
        )
    )
    page = email_document(BODY.html, notice="Test", family=True)
    assert all(colour in page for colour in FAMILY_ONLY)
    assert COLORS["page"] not in page and COLORS["ink"] not in page


def test_every_sender_names_its_look():
    """No default look: a new sender must choose Family's or the shared one."""
    with pytest.raises(TypeError):
        email_document(BODY.html)  # the missing family= is the point
    with pytest.raises(TypeError):
        notice_html("Test")


def test_compiler_markup_with_attributes_is_left_alone():
    """Digest tables, images and already styled links keep their own markup."""
    digest = (
        '<table class="report"><tr><td>1,234</td></tr></table>'
        '<img src="cid:chart" alt="Chart"><a style="color:red" href="x">x</a>'
    )
    assert digest in email_document(digest, family=False)


@pytest.mark.parametrize("family", [False, True])
def test_routed_testing_banner_becomes_one_small_notice(family):
    """The retained <h2>TEST</h2> banner shows as a notice, not a big heading."""
    notice = (FAMILY if family else SHARED).notice
    html = (
        "<h2>TEST</h2><p>TEST — sent to test@example.org instead of Alex &amp; "
        "Sam (a@example.org).</p><p>Body</p>"
    )
    page = email_document(html, family=family)
    assert "<h2" not in page
    assert notice in page
    assert "instead of Alex &amp; Sam (a@example.org).</td>" in page
    assert page.index(notice) < page.index("Body")
    plain = email_document("<p>Body</p>", notice="Test <only>", family=family)
    assert "Test &lt;only&gt;</td>" in plain


def test_family_message_wraps_only_the_html_alternative():
    """Plain text stays first and unchanged; the HTML part is Family's document."""
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
    assert html.get_content().rstrip("\n") == email_document(BODY.html, family=True)
