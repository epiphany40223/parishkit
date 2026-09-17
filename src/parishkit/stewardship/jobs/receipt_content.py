"""Credential-free confirmation content from explicit public submission facts."""

from dataclasses import dataclass
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from parishkit.stewardship.web.content import (
    FAMILY_CREDENTIAL_PLACEHOLDERS,
    PLACEHOLDERS,
    SafeContent,
    bounded_text,
    prepare_content,
    render_template,
    validate_receipt_content,
)

from .family_mail_routing import route_family_mail
from .outbox_validation import DeliveryIdentity

REQUIRED_VALUES = frozenset(
    {"parish_name", "parish_website", "parish_phone", "campaign_name", "family_name"}
)


@dataclass(frozen=True, repr=False)
class ReceiptTemplate:
    """A selected immutable template, or the built-in non-sensitive default."""

    subject: str = "Submission received"
    html: str = "<p>Your submission has been received.</p>"
    text: str = "Your submission has been received."

    def __post_init__(self):
        """Reject unsafe authored content and every credential substitution."""
        if prepare_content(self.html, text=self.text) != SafeContent(
            self.html, self.text
        ):
            raise ValueError("Receipt template requires canonical safe content.")
        validate_receipt_content(self.subject, self.html, self.text)
        if not self.subject.strip():
            raise ValueError("Receipt template requires a subject.")


def render_receipt(
    *,
    identity,
    configuration_id,
    template_id,
    template,
    block,
    values,
    submitted_at,
    campaign_timezone,
    sender,
    intended_recipients,
    testing_recipient=None,
    reply_to=None,
):
    """Append required fixed facts independently of optional parish-authored text.

    No answer dictionary or credential enters this API. The caller supplies the
    immutable submission instant and campaign timezone, not a browser's timezone.
    The owning transaction separately pins configuration, source and submission.
    """
    if (
        not isinstance(identity, DeliveryIdentity)
        or identity.purpose != "receipt"
        or identity.credential_namespace != "none"
    ):
        raise TypeError("An exact credential-free receipt identity is required.")
    if not isinstance(template, ReceiptTemplate) or not isinstance(block, SafeContent):
        raise TypeError("Typed receipt template and confirmation block are required.")
    if (
        type(values) is not dict
        or set(values) - (PLACEHOLDERS - FAMILY_CREDENTIAL_PLACEHOLDERS)
        or not values.keys() >= REQUIRED_VALUES
    ):
        raise ValueError("Receipt rendering requires explicit public facts only.")
    for name, value in values.items():
        bounded_text(value)
        if name in REQUIRED_VALUES and not value.strip():
            raise ValueError("Receipt public facts cannot be empty.")
    if (
        not isinstance(submitted_at, datetime)
        or submitted_at.utcoffset() is None
        or type(campaign_timezone) is not str
    ):
        raise ValueError("Receipt requires an aware submission instant and timezone.")
    local = submitted_at.astimezone(ZoneInfo(campaign_timezone))
    stamp = (
        local.strftime("%B ")
        + str(local.day)
        + local.strftime(", %Y at %I:%M:%S %p %Z")
    )
    if prepare_content(block.html, text=block.text) != block:
        raise ValueError("Receipt block requires canonical safe content.")
    validate_receipt_content("", block.html, block.text)
    subject = render_template(template.subject, values, subject=True)
    html = render_template(template.html, values, html=True)
    text = render_template(template.text, values)
    html += render_template(block.html, values, html=True)
    if block.text:
        text += "\n\n" + render_template(block.text, values)
    facts = (
        f"Parish: {values['parish_name']}",
        f"Campaign: {values['campaign_name']}",
        f"Family: {values['family_name']}",
        f"Submitted: {stamp}",
        f"Questions: {values['parish_phone']} — {values['parish_website']}",
    )
    html += "".join("<p>" + escape(value) + "</p>" for value in facts)
    text += "\n\n" + "\n".join(facts)
    # Check the combined result as well: separate literals/substitutions cannot
    # assemble a reserved credential marker in an otherwise valid template.
    validate_receipt_content(subject, html, text)
    return route_family_mail(
        identity=identity,
        configuration_id=configuration_id,
        template_id=template_id,
        sender=sender,
        reply_to=reply_to,
        intended_recipients=intended_recipients,
        testing_recipient=testing_recipient,
        family_name=values.get("family_member_names") or values["family_name"],
        subject=subject,
        html=html,
        text=text,
    )
