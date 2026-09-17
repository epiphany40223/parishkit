"""Fictional receipt previews share real fixed-fact rendering, never source data."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from parishkit.stewardship.web.content import (
    FAMILY_CREDENTIAL_PLACEHOLDERS,
    SafeContent,
)

from .outbox_validation import DeliveryIdentity
from .receipt_content import ReceiptTemplate, render_receipt


def confirmation_block(document, campaign_id):
    """Read the optional selected block from an already validated configuration."""
    for row in document["sections"].get("content", []):
        value = row["values"]
        if (value["campaign_id"], value["kind"], value["slot"]) == (
            str(campaign_id),
            "page",
            "submission_confirmation",
        ):
            return SafeContent(value["html"], value["text"])
    return SafeContent("", "")


def sample_receipt(value, *, substitutions, campaign, block):
    """Use the campaign's first date at noon as an explicit fictional sample time."""
    identifier = UUID(int=1)
    rendered = render_receipt(
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
        template=ReceiptTemplate(value["subject"], value["html"], value["text"])
        if value
        else ReceiptTemplate(),
        block=block or SafeContent("", ""),
        values={
            key: replacement
            for key, replacement in substitutions.items()
            if key not in FAMILY_CREDENTIAL_PLACEHOLDERS
        },
        submitted_at=datetime.fromisoformat(
            campaign["start_date"] + "T12:00:00"
        ).replace(tzinfo=ZoneInfo(campaign["timezone"])),
        campaign_timezone=campaign["timezone"],
        sender="sample@example.invalid",
        intended_recipients=("sample@example.invalid",),
    )
    return {field: getattr(rendered, field) for field in ("subject", "html", "text")}
