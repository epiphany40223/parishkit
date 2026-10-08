"""Retained content is re-sanitized at render, not refused (#385, #187).

Applied campaign content is trusted. Requiring it to equal today's sanitizer
output would make every older template fail at preparation or dispatch after
any sanitizer change, so each render template cleans its stored HTML instead.
The credential and placeholder rules still refuse.
"""

import pytest

from parishkit.stewardship.jobs.digest_content import DigestTemplate
from parishkit.stewardship.jobs.family_mail_content import (
    CODE_PLACEHOLDER,
    FamilyMailTemplate,
)
from parishkit.stewardship.jobs.receipt_content import ReceiptTemplate
from parishkit.stewardship.web.content import prepare_content

# Stored HTML that today's sanitizer would rewrite: a dropped element and
# attribute, as an older (or tampered) applied record might hold.
STALE = '<p onclick="x()">Hello</p><script>alert(1)</script>'


def test_family_email_renders_the_resanitized_body():
    """A body the sanitizer now rewrites still builds, and renders clean."""
    template = FamilyMailTemplate(
        "Subject",
        STALE + "<p>{{ family_code }} {{ family_url }}</p>",
        "Hello {{ family_code }} {{ family_url }}",
    )
    assert template.html == prepare_content(template.html).html
    assert "onclick" not in template.html and "<script" not in template.html
    assert template.text == "Hello {{ family_code }} {{ family_url }}"


def test_receipt_and_digest_templates_are_resanitized():
    """The receipt and Admin report templates follow the same rule."""
    receipt = ReceiptTemplate("Received", STALE, "Hello")
    digest = DigestTemplate("{{ campaign_name }}", STALE, "Hello")
    for template in (receipt, digest):
        assert "onclick" not in template.html and "<script" not in template.html
        assert template.html == prepare_content(template.html).html


def test_credential_rules_still_refuse():
    """Re-sanitizing never admits a reserved credential slot."""
    with pytest.raises(ValueError, match="reserved"):
        FamilyMailTemplate("Subject", "<p>" + CODE_PLACEHOLDER + "</p>", "Hello")
    with pytest.raises(ValueError):
        ReceiptTemplate("Received", "<p>{{ family_code }}</p>", "{{ family_code }}")
    with pytest.raises(ValueError):
        DigestTemplate("", "<p>Hi</p>", "Hi")


def test_a_foreign_image_or_script_in_a_digest_intro_is_dropped():
    """The Admin report introduction loses what the sanitizer forbids."""
    for html in ('<img src="https://elsewhere/">', "<script>steal()</script>"):
        template = DigestTemplate("{{ campaign_name }}", html, "")
        assert "elsewhere" not in template.html and "steal" not in template.html


def test_a_stale_template_renders_to_a_valid_delivery_mail_end_to_end():
    """Cleaned at render, then the code and link substituted as dispatch does."""
    from html import escape
    from uuid import uuid4

    from parishkit.stewardship.family_delivery import FamilyDeliveryMail
    from parishkit.stewardship.jobs.family_mail_content import (
        LINK_PLACEHOLDER,
        render_family_mail,
    )

    from .test_family_mail_content import identity

    content = render_family_mail(
        identity=identity(),
        configuration_id=uuid4(),
        template_id=uuid4(),
        template=FamilyMailTemplate(
            "Invitation",
            STALE + "<p>{{ family_code }}</p>"
            '<a href="{{ family_url }}" rel="noopener noreferrer">Respond</a>',
            "{{ family_code }} {{ family_url }}",
        ),
        values={"parish_name": "Example Parish", "family_member_names": "A"},
        sender="parish@example.org",
        intended_recipients=("a@example.org",),
    )
    assert "<script" not in content.html and "onclick" not in content.html
    url = "https://example.org/access/abc"
    mail = FamilyDeliveryMail(
        uuid4(),
        content.sender,
        content.reply_to,
        content.routed_recipients,
        content.subject,
        content.html.replace(CODE_PLACEHOLDER, "ABCDEFGH").replace(
            LINK_PLACEHOLDER, escape(url, quote=True)
        ),
        content.text.replace(CODE_PLACEHOLDER, "ABCDEFGH").replace(
            LINK_PLACEHOLDER, url
        ),
    )
    assert "ABCDEFGH" in mail.html and url in mail.text


def test_a_stale_receipt_block_is_cleaned_and_admitted():
    """The confirmation block is cleaned at render and passes the final check."""
    from uuid import uuid4

    from parishkit.stewardship.family_delivery import FamilyDeliveryMail
    from parishkit.stewardship.web.content import SafeContent

    from .test_receipt_content import render

    result = render(block=SafeContent(STALE, "Hello"))
    assert "<script" not in result.html and "onclick" not in result.html
    FamilyDeliveryMail(
        semantic_key=uuid4(),
        sender="parish@example.org",
        reply_to="parish@example.org",
        recipients=result.routed_recipients,
        subject=result.subject,
        html=result.html,
        text=result.text,
    )


@pytest.mark.parametrize(
    "html",
    [
        # Each credential slot appears only where the sanitizer removes it.
        "<p>Hi</p><!-- {{ family_code }} {{ family_url }} -->",
        '<p title="{{ family_code }}" data-x="{{ family_url }}">Hi</p>',
    ],
)
def test_a_slot_that_survives_only_in_removed_markup_is_refused(html):
    """The Family email rules run on the cleaned HTML, which lacks the slots."""
    with pytest.raises(ValueError):
        FamilyMailTemplate("Subject", html, "{{ family_code }} {{ family_url }}")


def test_receipt_and_digest_credential_checks_see_the_cleaned_html():
    """A credential slot dropped by cleaning is gone; one in the text still refuses."""
    receipt = ReceiptTemplate("Received", '<p data-x="{{ family_code }}">Hi</p>', "Hi")
    assert "family_code" not in receipt.html
    digest = DigestTemplate(
        "{{ campaign_name }}", '<p data-x="{{ family_url }}">Hi</p>', ""
    )
    assert "family_url" not in digest.html
    with pytest.raises(ValueError):
        ReceiptTemplate("Received", "<p>Hi</p>", "{{ family_code }}")
    with pytest.raises(ValueError):
        DigestTemplate("{{ campaign_name }}", "<p>Hi</p>", "{{ family_url }}")
