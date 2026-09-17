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
    ProviderHealth,
    delivery_settings,
)
from .readiness_delivery import DeliveryOutcome
from .readiness_delivery_process import _submit_private
from .readiness_delivery_worker import MAX_INPUT
from .web.digest_content import MAX_BODY_BYTES, MAX_CHART_BYTES

# JSON may expand each body byte into a six-byte control-character escape;
# base64 chart overhead is below 2x. Admission and the private pipe must agree.
MAX_DIGEST_INPUT = MAX_INPUT + 12 * MAX_BODY_BYTES + 2 * MAX_CHART_BYTES


def submit_family(value, settings, mail, *, seconds, check):
    """Missing or malformed acknowledgement is never proof of non-acceptance."""
    if not isinstance(mail, FamilyDeliveryMail):
        raise ValueError("Invalid private Family submission invocation.")
    return _submit_mail(
        value,
        settings,
        mail,
        seconds=seconds,
        check=check,
        helper="family_delivery_worker",
        limit=MAX_INPUT,
    )


def submit_digest(value, settings, mail, *, seconds, check):
    """Dispatch only an admitted one-Admin compiled digest, never Family payloads."""
    from .digest_delivery import DigestDeliveryMail

    if not isinstance(mail, DigestDeliveryMail):
        raise ValueError("Invalid private digest submission invocation.")
    return _submit_mail(
        value,
        settings,
        mail,
        seconds=seconds,
        check=check,
        helper="digest_delivery_worker",
        limit=MAX_DIGEST_INPUT,
    )


def _submit_mail(value, settings, mail, *, seconds, check, helper, limit):
    """Share bounded IPC and outcomes after the caller's typed mail validation."""
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
    if len(payload) > limit:
        return FamilyDeliveryResult(
            FamilyDeliveryStatus.PERMANENT, count, health=ProviderHealth.UNOBSERVED
        )

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
        helper=helper,
        seconds=seconds,
        check=check,
        decode=decode,
    )
    if isinstance(result, FamilyDeliveryResult):
        return result
    if result is DeliveryOutcome.NOT_SENT:
        return FamilyDeliveryResult(FamilyDeliveryStatus.UNAVAILABLE, count)
    return unknown
