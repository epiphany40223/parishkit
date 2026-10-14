"""The shared email document around sanitized stewardship email bodies."""

from uuid import uuid4

import pytest

from parishkit.stewardship.family_delivery import FamilyDeliveryMail
from parishkit.stewardship.mail_layout import (
    FONT,
    LINK_STYLE,
    NOTICE_STYLE,
    REPORT_WIDTH,
    SHARED,
    TAG_STYLES,
    email_document,
)
from parishkit.stewardship.web.buttons import email_button
from parishkit.stewardship.web.content import prepare_content
from parishkit.stewardship.web.design_tokens import COLORS

BODY = prepare_content(
    "<h2>Welcome</h2><p>Dear Alex,</p><ul><li>One</li></ul>"
    '<p><a href="https://example.org/">Begin</a></p><blockquote>Q</blockquote>'
)
# Family mail's colours before #732, which no email uses any more.
OLD_FAMILY = ("#1f2933", "#f4f5f7", "#e4e7ec", "#111827", "#1a56db", "#fff8db")


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
    page = email_document(BODY.html, family=family)
    for tag in ("p", "h2", "ul", "li", "blockquote"):
        assert f'<{tag} style="{TAG_STYLES[tag]}">' in page
        assert f"<{tag}>" not in page
    assert f'<a style="{LINK_STYLE}" href="https://example.org/"' in page
    for words in ("Welcome", "Dear Alex,", "One", "Begin", ">Q<"):
        assert words in page


@pytest.mark.parametrize("family", [False, True])
def test_every_email_uses_the_portal_design_tokens(family):
    """Admin and Family mail share the portal's colours and shapes (#732)."""
    assert SHARED.text == SHARED.heading == COLORS["ink"]
    assert SHARED.page == COLORS["page"] and SHARED.link == COLORS["link"]
    assert SHARED.card_border == COLORS["border"] and SHARED.card_radius == "12px"
    assert SHARED.quote_text == COLORS["muted"]
    assert (SHARED.tag_styles(), SHARED.link_style()) == (TAG_STYLES, LINK_STYLE)
    # The portal's warning notice: its colours, thick leading edge and radius.
    assert SHARED.notice == NOTICE_STYLE
    assert SHARED.notice.startswith(
        f"background-color:{COLORS['warning-bg']};"
        f"border:1px solid {COLORS['warning-border']};"
        "border-left-width:6px;border-radius:8px;"
    )
    assert f"color:{COLORS['warning-ink']};" in SHARED.notice
    page = email_document("<h2>TEST</h2><p>Routed.</p>" + BODY.html, family=family)
    assert not [colour for colour in OLD_FAMILY if colour in page]
    assert f"background-color:{COLORS['page']};" in page


CTA = prepare_content(
    '<p>Dear Alex,</p><p><strong><a href="https://example.org/f?a=1&amp;b=2">'
    "Begin your household’s renewal &amp; more</a></strong></p>"
    '<p>Or <a href="https://example.org/give">please click here</a>.</p>'
    '<p><a href="https://example.org/plain">A plain standalone link</a></p>'
).html


def test_family_call_to_action_is_the_shared_email_button():
    """A paragraph holding only one bold link becomes email_button() (#732)."""
    page = email_document(CTA, family=True)
    button = email_button(
        "Begin your household’s renewal & more", "https://example.org/f?a=1&b=2"
    )
    assert button in page
    assert page.count('<table role="presentation" cellpadding') == 1
    # The words and link are the content's own; inline and plain links stay.
    assert "Begin your household’s renewal &amp; more</a>" in page
    assert 'href="https://example.org/f?a=1&amp;b=2"' in page
    assert f'<a style="{LINK_STYLE}" href="https://example.org/give"' in page
    assert f'<a style="{LINK_STYLE}" href="https://example.org/plain"' in page


def test_admin_mail_keeps_bold_links_as_links():
    """Only Family mail draws call-to-action buttons; Admin mail is as compiled."""
    page = email_document(CTA, family=False)
    assert "border-collapse:separate" not in page
    assert (
        f'<strong><a style="{LINK_STYLE}" href="https://example.org/f?a=1&amp;b=2"'
        in page
    )


@pytest.mark.parametrize(
    "href",
    ["mailto:office@example.org", "https:///no-host", "{{ family_url }}"],
)
def test_call_to_action_without_an_absolute_web_link_stays_a_link(href):
    """email_button() links only to absolute http(s) addresses."""
    html = f'<p><strong><a href="{href}" rel="noopener noreferrer">Go</a></strong></p>'
    page = email_document(html, family=True)
    assert "border-collapse:separate" not in page and "<strong><a style=" in page


@pytest.mark.parametrize(
    "html",
    [
        # A bold link with other words in its paragraph is inline.
        '<p>See <strong><a href="https://example.org/a">here</a></strong> now.</p>',
        # Two links in one bold paragraph are not one call to action.
        '<p><strong><a href="https://example.org/a">One</a> and '
        '<a href="https://example.org/b">two</a></strong></p>',
        # A label with its own markup, or only a space, is not a button label.
        '<p><strong><a href="https://example.org/a"><em>Go</em></a></strong></p>',
        '<p><strong><a href="https://example.org/a">&nbsp;</a></strong></p>',
    ],
)
def test_only_a_lone_bold_link_paragraph_is_a_call_to_action(html):
    """Parish-written bold links that are not a lone call to action stay links."""
    body = prepare_content(html).html
    page = email_document(body, family=True)
    assert "border-collapse:separate" not in page
    assert page == email_document(body, family=False)


def test_admin_mail_body_is_exactly_as_compiled():
    """Admin mail's body is the compiled content with only inline styles added."""
    page = email_document(CTA, family=False)
    styled = CTA.replace("<p>", f'<p style="{TAG_STYLES["p"]}">').replace(
        "<a ", f'<a style="{LINK_STYLE}" '
    )
    assert styled in page
    # Outside the call to action, Family and Admin pages are identical.
    family = email_document(CTA, family=True)
    assert (
        family.replace(
            email_button(
                "Begin your household’s renewal & more", "https://example.org/f?a=1&b=2"
            ),
            f'<p style="{TAG_STYLES["p"]}"><strong><a style="{LINK_STYLE}" '
            'href="https://example.org/f?a=1&amp;b=2" rel="noopener noreferrer">'
            "Begin your household’s renewal &amp; more</a></strong></p>",
        )
        == page
    )


def test_every_sender_names_whether_it_is_family_mail():
    """No default: a new sender must say whether it sends Family mail."""
    with pytest.raises(TypeError):
        email_document(BODY.html)  # the missing family= is the point


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
    notice = SHARED.notice
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
