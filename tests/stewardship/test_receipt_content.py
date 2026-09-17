"""Submission receipts are credential-free, timezone-explicit and mode-routed."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.jobs.receipt_content import ReceiptTemplate, render_receipt
from parishkit.stewardship.web.content import SafeContent


def render(*, testing=False, **changes):
    """Use public facts only; no answer payload or credential is supplied."""
    campaign = uuid4()
    identity = DeliveryIdentity(
        scope_id=campaign,
        campaign_id=campaign,
        family_id=uuid4(),
        semantic_key=uuid4(),
        mode="testing" if testing else "production",
        routing="testing_override" if testing else "production",
        purpose="receipt",
    )
    arguments = dict(
        identity=identity,
        configuration_id=uuid4(),
        template_id=None,
        template=ReceiptTemplate(),
        block=SafeContent("", ""),
        values={
            "parish_name": "Example Parish",
            "parish_phone": "+12025550100",
            "parish_website": "https://example.org/",
            "campaign_name": "Annual census",
            "family_name": "A & <B>",
        },
        submitted_at=datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        campaign_timezone="America/New_York",
        sender="parish@example.org",
        intended_recipients=("a@example.org", "b@example.org"),
        testing_recipient="test@example.org" if testing else None,
    )
    return render_receipt(**(arguments | changes))


@pytest.mark.parametrize("testing", [False, True])
def test_required_facts_and_optional_separate_block(testing):
    """Empty template bodies cannot remove the required confirmation facts."""
    result = render(
        testing=testing,
        template=ReceiptTemplate("Received", "", ""),
        block=SafeContent("<p>Parish-authored thanks.</p>", "Parish-authored thanks."),
    )
    for value in (
        "Example Parish",
        "Annual census",
        "+12025550100",
        "https://example.org/",
        "November 1, 2026 at 01:30:00 AM EDT",
        "Parish-authored thanks.",
    ):
        assert value in result.html and value in result.text
    assert "A &amp; &lt;B&gt;" in result.html and "A & <B>" in result.text
    assert result.routed_recipients == (
        ("test@example.org",) if testing else result.intended_recipients
    )
    assert result.subject.startswith("[TEST]") is testing
    if testing:
        assert "instead of A & <B> (a@example.org, b@example.org)" in result.text
        assert "<h2>TEST</h2>" in result.html


def test_repeated_dst_hour_has_explicit_distinct_abbreviation():
    """Stored instants disambiguate the two occurrences of the same local hour."""
    first = render()
    second = render(submitted_at=datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    assert "01:30:00 AM EDT" in first.text
    assert "01:30:00 AM EST" in second.text


@pytest.mark.parametrize("part", ["subject", "html", "text"])
@pytest.mark.parametrize(
    "private",
    [
        "{{ family_code }}",
        "{{ family_url }}",
        "PARISHKIT_REDACTED_FAMILY_CODE",
        "https://parishkit.invalid/redacted-family-link",
        "PARISHKIT_PENDING_RECEIPT",
    ],
)
def test_private_placeholders_and_markers_are_rejected(part, private):
    """Every receipt alternative rejects the credential-bearing mail contract."""
    with pytest.raises(ValueError):
        replace(ReceiptTemplate(), **{part: private})


@pytest.mark.parametrize("part", ["html", "text"])
def test_confirmation_block_cannot_bypass_template_privacy(part):
    """An optional body block cannot reintroduce a forbidden credential slot."""
    block = replace(SafeContent("", ""), **{part: "{{ family_code }}"})
    with pytest.raises(ValueError, match="access credentials"):
        render(block=block)


@pytest.mark.parametrize(
    "prefix,suffix",
    [("PARISHKIT_REDACTED_", "FAMILY_CODE"), ("PARISHKIT_PENDING_", "RECEIPT")],
)
def test_combined_text_cannot_assemble_a_reserved_marker(prefix, suffix):
    """Validate the final body, not just individually safe template fragments."""
    with pytest.raises(ValueError, match="reserved markers"):
        render(
            template=ReceiptTemplate("Received", prefix, "Received"),
            block=SafeContent(suffix, ""),
        )


def test_public_substitution_cannot_assemble_the_allocation_seed_marker():
    """Preview and worker rendering share the dispatch guard's reserved vocabulary."""
    with pytest.raises(ValueError, match="reserved markers"):
        render(
            template=ReceiptTemplate("PARISHKIT_PENDING_{{ campaign_name }}"),
            values={
                "parish_name": "Example Parish",
                "parish_phone": "+12025550100",
                "parish_website": "https://example.org/",
                "campaign_name": "RECEIPT",
                "family_name": "Family",
            },
        )


@pytest.mark.parametrize(
    "change",
    [
        {"submitted_at": datetime(2026, 1, 1)},
        {"values": {"annual_pledge": "123.00"}},
        {"values": {"family_code": "ABCDEFGH"}},
        {"values": {}},
        {"testing_recipient": "test@example.org"},
        {"intended_recipients": ()},
        {"intended_recipients": ("b@example.org", "a@example.org")},
        {"template": None},
        {"block": None},
        {"identity": None},
        {"block": SafeContent("<script>unsafe()</script>", "")},
    ],
)
def test_invalid_receipt_inputs_fail_without_rendering_private_payloads(change):
    """No naive time, arbitrary answers, unsafe block or mode fallback is allowed."""
    with pytest.raises((ValueError, TypeError)):
        render(**change)


def test_testing_subject_keeps_marker_for_maximum_length_subject():
    """Long authored subjects retain the mode indicator within the header limit."""
    result = render(testing=True, template=ReceiptTemplate("A" * 254))
    assert result.subject.startswith("[TEST] ") and len(result.subject) == 254
    assert result.subject.endswith("…")


def test_template_repr_does_not_include_authored_text():
    """Incidental template diagnostics omit authored private prose."""
    assert "Private authored text" not in repr(ReceiptTemplate("Private authored text"))
