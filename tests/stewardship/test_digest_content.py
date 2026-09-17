"""Admin digest authoring and independent mode routing remain credential-free."""

from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.email.base import InlineImage
from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.jobs.digest_content import (
    DigestTemplate,
    render_digest_envelope,
)
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.reports.daily_digest import DailyDigestContent

from .configuration_factory import configuration_version
from .content_factory import content, content_document


def arguments(*, testing=False):
    """The tested routing layer receives a precompiled report, not report inputs."""
    campaign = uuid4()
    return dict(
        identity=DeliveryIdentity(
            scope_id=campaign,
            campaign_id=campaign,
            semantic_key=uuid4(),
            mode="testing" if testing else "production",
            routing="testing_override" if testing else "production",
            purpose="daily_digest",
        ),
        configuration_id=uuid4(),
        template_id=None,
        template=DigestTemplate(),
        content=DailyDigestContent(
            "Daily campaign digest",
            "<p>Required report.</p>",
            "Required report.",
            InlineImage(b"chart", "participation@parishkit"),
        ),
        values={"campaign_name": "Annual campaign", "parish_name": "A & <B>"},
        sender="parish@example.org",
        recipient="admin@example.org",
        testing_recipient="test@example.org" if testing else None,
    )


@pytest.mark.parametrize("testing", [False, True])
def test_digest_routes_one_admin_and_keeps_compiled_facts(testing):
    """No recipient list is exposed, and optional prose cannot erase the report."""
    values = arguments(testing=testing)
    values["template"] = DigestTemplate(
        "{{ campaign_name }}", "<p>{{ parish_name }}</p>", "{{ parish_name }}"
    )
    result = render_digest_envelope(**values)
    assert result.intended_recipients == ("admin@example.org",)
    assert result.routed_recipients == (
        ("test@example.org",) if testing else ("admin@example.org",)
    )
    assert "Required report." in result.text and "Required report." in result.html
    assert "A &amp; &lt;B&gt;" in result.html and "A & <B>" in result.text
    assert result.subject.startswith("[TEST]") is testing
    if testing:
        assert "<h2>TEST</h2>" in result.html
        assert "instead of Administrator admin@example.org" in result.text


def test_testing_subject_prefix_cannot_be_cropped_off():
    values = arguments(testing=True)
    values["template"] = DigestTemplate("x" * 254)
    result = render_digest_envelope(**values)
    assert result.subject.startswith("[TEST] ") and len(result.subject) == 254


@pytest.mark.parametrize("slot", ["daily_digest", "weekly_digest"])
@pytest.mark.parametrize("part", ["subject", "html", "text"])
@pytest.mark.parametrize(
    "private",
    [
        "{{ family_code }}",
        "{{ family_url }}",
        "{{ family_name }}",
        "{{ family_member_names }}",
        "{{ pronoun }}",
        "{{ generic_family_url }}",
        "PARISHKIT_REDACTED_FAMILY_CODE",
        "https://parishkit.invalid/redacted-family-link",
        "PARISHKIT_PENDING_RECEIPT",
        "PARISHKIT_PENDING_DAILY_DIGEST",
    ],
)
def test_private_placeholders_rejected_by_template_form_and_configuration(
    slot, part, private
):
    """Raw YAML cannot bypass the same restriction used by editor and renderer."""
    with pytest.raises(ValueError):
        replace(DigestTemplate(), **{part: private})
    data = dict(
        base_digest="a" * 64,
        subject="Campaign",
        html="<p>Campaign</p>",
        text="Campaign",
        generate_text="",
    ) | {part: private}
    assert not ContentForm(data, kind="email", slot=slot).is_valid()
    document = content_document()
    campaign = document["sections"]["campaigns"][0]["id"]
    document["sections"]["content"].append(
        content(campaign, kind="email", slot=slot, **{part: private})
    )
    with pytest.raises(ConfigError):
        configuration_version(document)


@pytest.mark.parametrize("slot", ["daily_digest", "weekly_digest"])
def test_public_digest_prose_is_supported_by_editor_and_configuration(slot):
    """An ordinary parish-authored digest remains configurable and previewable."""
    form = ContentForm(
        dict(
            base_digest="a" * 64,
            subject="{{ campaign_name }}",
            html="<p>{{ parish_name }}</p>",
            text="",
            generate_text="on",
        ),
        kind="email",
        slot=slot,
    )
    assert form.is_valid(), form.errors
    document = content_document()
    campaign = document["sections"]["campaigns"][0]["id"]
    document["sections"]["content"].append(content(campaign, kind="email", slot=slot))
    assert configuration_version(document)


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": ""},
        {"subject": "bad\nheader"},
        {"subject": "x" * 255},
        {"html": '<img src="https://elsewhere/">'},
        {"html": "<script>steal()</script>"},
    ],
)
def test_invalid_authored_templates_are_rejected(changes):
    with pytest.raises(ValueError):
        replace(DigestTemplate(), **changes)


@pytest.mark.parametrize(
    "key,value",
    [
        ("recipient", "Other@example.org"),
        ("recipient", ("a@example.org", "b@example.org")),
        ("testing_recipient", "test@example.org"),
        ("values", {"family_code": "ABCDEF"}),
        ("values", {"campaign_name": 3}),
    ],
)
def test_invalid_production_routing_and_values_are_rejected(key, value):
    with pytest.raises((ValueError, ConfigError)):
        render_digest_envelope(**(arguments() | {key: value}))


def test_invalid_typed_inputs_and_testing_without_recipient_are_rejected():
    for key in ("identity", "template", "content"):
        with pytest.raises(TypeError):
            render_digest_envelope(**(arguments() | {key: None}))
    with pytest.raises((ValueError, ConfigError)):
        render_digest_envelope(
            **(arguments(testing=True) | {"testing_recipient": None})
        )
    values = arguments()
    values["identity"] = replace(values["identity"], purpose="weekly_digest")
    with pytest.raises(TypeError):
        render_digest_envelope(**values)


def test_substitution_cannot_assemble_a_reserved_marker():
    """Check assembled authored content too, not just individual literals."""
    values = arguments()
    values.update(
        template=DigestTemplate("Report", "<p>PARISHKIT_{{ parish_name }}</p>", ""),
        values={"parish_name": "REDACTED_FAMILY_CODE"},
    )
    with pytest.raises(ValueError):
        render_digest_envelope(**values)
