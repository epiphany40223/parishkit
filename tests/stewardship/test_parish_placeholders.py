"""Parish contact placeholders reach every page, email, receipt and preview path."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.content_forms import sample_render
from parishkit.stewardship.jobs.campaign_mail_values import (
    campaign_values,
    document_parish,
)
from parishkit.stewardship.jobs.digest_content import DigestTemplate
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.jobs.receipt_content import (
    REQUIRED_VALUES,
    ReceiptTemplate,
    render_receipt,
)
from parishkit.stewardship.responses.page_content import (
    public_content_dependencies,
    public_values,
)
from parishkit.stewardship.web.content import (
    ADMIN_DIGEST_PLACEHOLDERS,
    PLACEHOLDERS,
    SHARE_PLACEHOLDERS,
    SafeContent,
    validate_admin_digest_content,
)

from .campaign_factory import campaign, financial

PARISH = {
    "name": "Sample Parish",
    "website": "https://example.org/",
    "timezone": "America/New_York",
    "phone": "+12025550100",
    "branding": {},
}


def document(campaign_id="c", content=(), reply_to="office@example.org"):
    """A minimal applied document with a parish profile and mail integration."""
    integrations = (
        [
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "email",
                    "settings": {"sender": "a@example.org", "reply_to": reply_to},
                    "credential_fingerprint": None,
                },
            }
        ]
        if reply_to
        else []
    )
    return {
        "sections": {
            "parish": [{"id": "p", "values": PARISH}],
            "integrations": integrations,
            "content": list(content),
        }
    }


def test_parish_email_is_the_outgoing_mail_reply_to():
    """The placeholder is public, shared by Admin digests, and not a share label."""
    assert "parish_email" in PLACEHOLDERS & ADMIN_DIGEST_PLACEHOLDERS
    assert "parish_email" not in SHARE_PLACEHOLDERS
    parish = document_parish(document())
    assert parish["email"] == "office@example.org"
    assert document_parish(document(reply_to=None))["email"] == ""
    values = campaign_values(parish=parish, campaign=campaign()["values"])
    assert values["parish_email"] == "office@example.org"
    validate_admin_digest_content(
        "{{ parish_email }}", "<p>{{ parish_email }}</p>", "{{ parish_email }}"
    )
    DigestTemplate("{{ campaign_name }}", "<p>{{ parish_email }}</p>", "x")


def test_page_values_and_dependencies_include_parish_email():
    """Family pages render the Reply-to and pin it when a displayed block uses it."""
    values = campaign()["values"]
    assert public_values(document_parish(document()), values)["parish_email"] == (
        "office@example.org"
    )
    row = {
        "id": str(uuid4()),
        "values": {
            "campaign_id": "c",
            "kind": "page",
            "slot": "login_help",
            "subject": None,
            "html": "<p>{{ parish_email }}</p>",
            "text": "{{ parish_email }}",
        },
    }
    pinned = public_content_dependencies(document(content=[row]), values, "c")
    assert pinned == {}  # Login help is not a Family form page.
    welcome = row | {"values": row["values"] | {"slot": "welcome"}}
    selected = values | {"content_versions": {"welcome": welcome["id"]}}
    assert public_content_dependencies(document(content=[welcome]), selected, "c") == {
        "parish_email": "office@example.org"
    }


def test_sample_render_uses_the_configured_or_a_fictional_address():
    """Previews show the draft Reply-to, or a clearly fictional address."""
    value = {"subject": None, "html": "<p>{{ parish_email }}</p>", "text": "x"}
    parish = PARISH | {"email": "office@example.org"}
    configured = sample_render(value, parish=parish, campaign=campaign()["values"])
    assert configured["html"] == "<p>office@example.org</p>"
    fictional = sample_render(value, parish=PARISH, campaign=campaign()["values"])
    assert fictional["html"] == "<p>office@parish.example.invalid</p>"


def receipt(values, template=None):
    """Render one production receipt with explicit public facts."""
    identifier = uuid4()
    return render_receipt(
        identity=DeliveryIdentity(
            scope_id=identifier,
            campaign_id=identifier,
            family_id=identifier,
            semantic_key=identifier,
            mode="production",
            routing="production",
            purpose="receipt",
        ),
        configuration_id=identifier,
        template_id=None,
        template=template or ReceiptTemplate(),
        block=SafeContent("", ""),
        values=values,
        submitted_at=datetime(2026, 10, 2, 12, tzinfo=UTC),
        campaign_timezone="America/New_York",
        sender="a@example.org",
        intended_recipients=("family@example.org",),
    )


def test_receipts_require_parish_email_and_accept_financial_values():
    """Receipts show the Reply-to and may name the pledge year and period."""
    assert "parish_email" in REQUIRED_VALUES
    values = campaign_values(
        parish=document_parish(document()),
        campaign=campaign(modules=["financial"], financial=financial())["values"]
        | {"year_label": None},
    ) | {"family_name": "Family"}
    rendered = receipt(
        values,
        ReceiptTemplate(
            "Thanks",
            "<p>{{ campaign_year }} {{ financial_period }} {{ parish_email }}</p>",
            "{{ campaign_year }} {{ financial_period }} {{ parish_email }}",
        ),
    )
    assert "2027 January 1, 2027 – December 31, 2027 office@example.org" in (
        rendered.text
    )
    with pytest.raises(ValueError):
        receipt({key: value for key, value in values.items() if key != "parish_email"})
