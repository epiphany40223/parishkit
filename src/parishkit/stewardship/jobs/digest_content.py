"""Individually routed Administrator digests with mandatory compiled facts."""

from dataclasses import dataclass
from html import escape

from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.reports.daily_digest import DailyDigestContent
from parishkit.stewardship.web.content import (
    ADMIN_DIGEST_PLACEHOLDERS,
    SafeContent,
    bounded_text,
    prepare_content,
    render_template,
    validate_admin_digest_content,
)

from .outbox_validation import DeliveryIdentity, RenderInput


@dataclass(frozen=True, repr=False)
class DigestTemplate:
    """Parish-authored introduction cannot replace the required compiled report."""

    subject: str = "{{ campaign_name }} — daily report"
    html: str = ""
    text: str = ""

    def __post_init__(self):
        """Only canonical authored HTML and public substitutions are admitted."""
        validate_admin_digest_content(self.subject, self.html, self.text)
        if not self.subject.strip() or prepare_content(
            self.html, text=self.text
        ) != SafeContent(self.html, self.text):
            raise ValueError("Admin digest template requires canonical safe content.")


def render_digest_envelope(
    *,
    identity,
    configuration_id,
    template_id,
    template,
    content,
    values,
    sender,
    recipient,
    testing_recipient=None,
    reply_to=None,
):
    """Route exactly one intended Admin; current authorization belongs to the owner.

    The same intended recipient remains visible in Testing while the actual
    envelope contains only the designated test mailbox. No Family identity or
    credential enters this rendering path. The separately compiled chart is
    retained by the snapshot owner and supplied only to the typed MIME adapter.
    """
    if (
        not isinstance(identity, DeliveryIdentity)
        or identity.purpose != "daily_digest"
        or identity.credential_namespace != "none"
        or not isinstance(template, DigestTemplate)
        or not isinstance(content, DailyDigestContent)
    ):
        raise TypeError(
            "An exact Admin digest identity and typed content are required."
        )
    if type(values) is not dict or not values.keys() <= ADMIN_DIGEST_PLACEHOLDERS:
        raise ValueError("Admin digest requires public campaign substitutions.")
    for value in values.values():
        bounded_text(value)
    if normalized_email(recipient) != recipient:
        raise ValueError("Admin digest requires one canonical intended recipient.")
    subject = render_template(template.subject, values, subject=True)
    html = render_template(template.html, values, html=True)
    text = render_template(template.text, values)
    # Validate authored substitutions together before appending compiler-owned
    # markup, whose report values are not interpreted as template expressions.
    validate_admin_digest_content(subject, html, text)
    html += content.html
    text += ("\n\n" if text else "") + content.text
    routed = recipient
    if identity.mode == "testing":
        if normalized_email(testing_recipient) != testing_recipient:
            raise ValueError("Testing requires one canonical override recipient.")
        routed = testing_recipient
        description = f"TEST — sent to {routed} instead of Administrator {recipient}."
        subject = "[TEST] " + (subject if len(subject) <= 247 else subject[:246] + "…")
        html = "<h2>TEST</h2><p>" + escape(description) + "</p>" + html
        text = description + "\n\n" + text
    elif testing_recipient is not None:
        raise ValueError("Production mail cannot have a Testing override.")
    return RenderInput(
        configuration_id=configuration_id,
        template_id=template_id,
        sender=sender,
        reply_to=reply_to,
        intended_recipients=(recipient,),
        routed_recipients=(routed,),
        subject=subject,
        html=html,
        text=text,
    )
