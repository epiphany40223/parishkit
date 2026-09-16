"""Bounded private Family delivery transport reusing the maintained pipe owner."""

import base64
import json
import math

from .accounts.integration_candidates import _object
from .accounts.key_files import MAX_FILE_BYTES
from .family_delivery import (
    FamilyDeliveryMail,
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    delivery_settings,
)
from .readiness_delivery import DeliveryOutcome
from .readiness_delivery_process import _submit_private
from .readiness_delivery_worker import MAX_INPUT


def submit_family(value, settings, mail, *, seconds, check):
    """Missing or malformed acknowledgement is never proof of non-acceptance."""
    if not isinstance(mail, FamilyDeliveryMail):
        raise ValueError("Invalid private Family submission invocation.")
    count = len(mail.recipients)
    unknown = FamilyDeliveryResult(FamilyDeliveryStatus.UNKNOWN, count)
    try:
        settings = delivery_settings(settings)
        if (
            type(value) is not bytes
            or not 0 < len(value) <= MAX_FILE_BYTES
            or type(seconds) not in (int, float)
            or not math.isfinite(seconds)
            or not 0 < seconds <= 30
            or not callable(check)
            or any(
                getattr(mail, key) != settings[key] for key in ("sender", "reply_to")
            )
        ):
            raise ValueError("Invalid private Family submission invocation.")
        payload = json.dumps(
            {
                "candidate": base64.b64encode(value).decode("ascii"),
                "settings": settings,
                "mail": mail.payload(),
            },
            ensure_ascii=False,
        ).encode("utf-8")
    except (ValueError, TypeError):
        # This try block performs no IO. Do not relabel a deterministic local
        # rejection as possible SMTP acceptance, or suppress an untested address.
        return FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, count)
    if len(payload) > MAX_INPUT:
        return FamilyDeliveryResult(FamilyDeliveryStatus.PERMANENT, count)

    def decode(output):
        """Reject extra fields, duplicate keys and another envelope's result."""
        try:
            return FamilyDeliveryResult.from_payload(
                json.loads(output.decode("utf-8"), object_pairs_hook=_object),
                recipient_count=count,
            )
        except (ValueError, TypeError, RecursionError):
            return unknown

    result = _submit_private(
        payload,
        helper="family_delivery_worker",
        seconds=seconds,
        check=check,
        decode=decode,
    )
    if isinstance(result, FamilyDeliveryResult):
        return result
    if result is DeliveryOutcome.NOT_SENT:
        return FamilyDeliveryResult(FamilyDeliveryStatus.TRANSIENT, count)
    return unknown
