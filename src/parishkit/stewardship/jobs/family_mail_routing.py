"""Shared deterministic Family envelopes; rendering never authorizes delivery."""

from html import escape

from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.web.content import bounded_text

from .outbox_validation import DeliveryIdentity, RenderInput


def route_family_mail(
    *,
    identity,
    configuration_id,
    template_id,
    sender,
    intended_recipients,
    subject,
    html,
    text,
    family_name,
    testing_recipient=None,
    reply_to=None,
):
    """Apply immutable mode routing and a mandatory banner to already rendered text.

    Callers separately enforce purpose-specific content/credential contracts.
    This helper cannot create an operational exemption or dispatch a message.
    """
    if not isinstance(identity, DeliveryIdentity) or identity.purpose not in {
        "initial",
        "reminder",
        "receipt",
    }:
        raise TypeError("An exact Family delivery identity is required.")
    bounded_text(family_name)
    intended = tuple(intended_recipients)
    if (
        not intended
        or intended != tuple(sorted(set(intended)))
        or any(normalized_email(address) != address for address in intended)
    ):
        raise ValueError("Family recipients must be canonical and distinct.")
    if (
        type(subject) is not str
        or len(subject) > 254
        or any(char in subject for char in "\r\n\x00")
    ):
        raise ValueError("Invalid email subject substitution.")
    routed = intended
    if identity.mode == "testing":
        if normalized_email(testing_recipient) != testing_recipient:
            raise ValueError("Testing requires one canonical override recipient.")
        routed = (testing_recipient,)
        description = (
            "TEST — sent to "
            + testing_recipient
            + " instead of "
            + (family_name or "Family")
            + " ("
            + ", ".join(intended)
            + ")."
        )
        # The mode prefix is mandatory even for a maximum-length subject.
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
        intended_recipients=intended,
        routed_recipients=routed,
        subject=subject,
        html=html,
        text=text,
    )
