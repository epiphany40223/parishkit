"""Maintained private IPC for operational-only mail and Slack attempts."""

import base64
import json

from .family_delivery_process import _submit_mail
from .operational_delivery import OperationalMail, OperationalSlack
from .operational_slack_worker import MAX_INPUT as MAX_SLACK_INPUT
from .readiness_delivery_process import _submit_private
from .readiness_delivery_worker import MAX_INPUT as MAX_MAIL_INPUT
from .security_delivery import SecurityMail


def submit_operational_mail(value, settings, mail, *, seconds, check):
    """Use the shared certainty boundary only after the owner commits submission."""
    if not isinstance(mail, OperationalMail):
        raise ValueError("Invalid operational mail invocation.")
    return _submit_mail(
        value,
        settings,
        mail,
        seconds=seconds,
        check=check,
        helper="operational_mail_worker",
        limit=MAX_MAIL_INPUT,
    )


def submit_security_mail(value, settings, mail, *, seconds, check):
    """A security alert crosses its own private helper with the same bounds."""
    if not isinstance(mail, SecurityMail):
        raise ValueError("Invalid security mail invocation.")
    return _submit_mail(
        value,
        settings,
        mail,
        seconds=seconds,
        check=check,
        helper="security_mail_worker",
        limit=MAX_MAIL_INPUT,
    )


def submit_operational_slack(value, notification, *, seconds, check):
    """Keep credentials out of argv/environment and forbid arbitrary message text."""
    if (
        type(value) is not bytes
        or not 0 < len(value) <= 4098
        or not isinstance(notification, OperationalSlack)
    ):
        raise ValueError("Invalid operational Slack invocation.")
    payload = json.dumps(
        {
            "candidate": base64.b64encode(value).decode("ascii"),
            "notification": notification.payload(),
        }
    ).encode("utf-8")
    if len(payload) > MAX_SLACK_INPUT:
        raise ValueError("Private operational notification exceeds its bound.")
    return _submit_private(
        payload, helper="operational_slack_worker", seconds=seconds, check=check
    )
