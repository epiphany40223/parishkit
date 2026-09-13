"""Target-owner pipe invocation for an explicitly journaled Slack readiness test."""

import base64
import json

from .readiness_delivery_process import _submit_private
from .readiness_notification import ReadinessNotification
from .readiness_notification_worker import MAX_INPUT


def submit_notification(value, notification, *, seconds, check):
    """Supply private stdin only after the caller commits its one-way send marker."""
    if (
        type(value) is not bytes
        or not 0 < len(value) <= 4098
        or not isinstance(notification, ReadinessNotification)
    ):
        raise ValueError("Invalid readiness notification invocation.")
    payload = json.dumps(
        {
            "candidate": base64.b64encode(value).decode("ascii"),
            "notification": notification.payload(),
        }
    ).encode("utf-8")
    if len(payload) > MAX_INPUT:
        raise ValueError("Private notification input exceeds its bound.")
    return _submit_private(
        payload, helper="readiness_notification_worker", seconds=seconds, check=check
    )
