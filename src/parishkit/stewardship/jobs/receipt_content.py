"""Credential-free confirmation content from explicit public submission facts."""

from dataclasses import dataclass
from datetime import datetime

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.content import (
    FAMILY_CREDENTIAL_PLACEHOLDERS,
    PLACEHOLDERS,
    SafeContent,
    bounded_text,
    generated_text_matches,
    prepare_content,
    render_template,
    text_html,
    validate_receipt_content,
)

from .family_mail_routing import route_family_mail
from .outbox_validation import DeliveryIdentity

REQUIRED_VALUES = frozenset(
    {
        "parish_name",
        "parish_website",
        "parish_phone",
        "parish_email",
        "campaign_name",
        "family_name",
    }
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


def fold_parts(html, text, note_html, note_text):
    """One receipt body from an email body and a retired closing note (#260).

    The HTML is simply joined. When both plain texts are exactly what their
    HTML generates, the joined plain text is generated from the joined HTML
    too, so the folded email stays "Generate plain text from HTML" content and
    an Admin's later HTML edits carry into its plain text. Hand-written plain
    text is joined after a blank line instead, and kept word for word.
    """
    joined = html + note_html
    if generated_text_matches(html, text) and generated_text_matches(
        note_html, note_text
    ):
        return joined, prepare_content(joined).text
    return joined, text + ("\n\n" + note_text if note_text else "")


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
    banner="",
    date_format=None,
    files=None,
):
    """Append required fixed facts independently of optional parish-authored text.

    ``files`` (``HostedLinks``) expands hosted-file placeholders (#346).

    No answer dictionary or credential enters this API. The caller supplies the
    immutable submission instant and campaign timezone, not a browser's timezone.
    The owning transaction separately pins configuration, source and submission.
    ``date_format`` is that pinned configuration's parish style; None (the
    fictional Admin preview) uses the request's active style.
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
    stamp = dates.format_instant(submitted_at, campaign_timezone, date_format)
    if prepare_content(block.html, text=block.text) != block:
        raise ValueError("Receipt block requires canonical safe content.")
    validate_receipt_content("", block.html, block.text)
    subject = render_template(template.subject, values, subject=True)
    # A closing note renders exactly as the folded email that replaces it
    # would (accounts.receipt_note), so saving that email changes nothing.
    body_html, body_text = (
        fold_parts(template.html, template.text, block.html, block.text)
        if block.html or block.text
        else (template.html, template.text)
    )
    html = render_template(body_html, values, html=True, files=files)
    text = render_template(body_text, values, files=files)
    facts = (
        f"Parish: {values['parish_name']}",
        f"Campaign: {values['campaign_name']}",
        f"Family: {values['family_name']}",
        f"Submitted: {stamp}",
    )
    # One compact paragraph, a line per fact, as the email signature does.
    # Escaped as the sanitizer writes text, which the delivery check compares
    # against: "O'Brien" must not become &#x27;, nor a pasted U+00A0 stay raw.
    html += "<p>" + "<br>".join(text_html(value) for value in facts) + "</p>"
    text += "\n\n" + "\n".join(facts)
    # Check the combined result as well: separate literals/substitutions cannot
    # assemble a reserved credential marker in an otherwise valid template.
    validate_receipt_content(subject, html, text)
    # The optional campaign banner (#248) is server-built markup, added after
    # the content checks, which never admit images in parish text.
    html = banner + html
    return route_family_mail(
        identity=identity,
        configuration_id=configuration_id,
        template_id=template_id,
        sender=sender,
        reply_to=reply_to,
        intended_recipients=intended_recipients,
        testing_recipient=testing_recipient,
        # The Testing banner names the heads (or, by its older name, the same
        # value) and otherwise the household.
        family_name=values.get("head_salutation")
        or values.get("family_member_names")
        or values["family_name"],
        subject=subject,
        html=html,
        text=text,
    )
