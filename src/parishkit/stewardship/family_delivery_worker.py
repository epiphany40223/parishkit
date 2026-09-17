"""One finite private Family SMTP helper; no retained credentials or diagnostics."""

import base64
import json
import logging
import sys

from .accounts.integration_candidates import _object
from .accounts.key_files import MAX_FILE_BYTES
from .family_delivery import FamilyDeliveryMail, deliver_family, delivery_settings
from .readiness_delivery_worker import MAX_INPUT


def decode_request(raw):
    """Validate the entire closed request before any token exchange or submission."""
    return _decode_request(raw, mail_class=FamilyDeliveryMail, limit=MAX_INPUT)


def _decode_request(raw, *, mail_class, limit):
    """Share envelope parsing; compiled helpers choose their own closed mail type."""
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError("Invalid private Family delivery request.")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(request) is not dict
        or set(request) != {"settings", "candidate", "mail"}
        or type(request["candidate"]) is not str
    ):
        raise ValueError("Invalid private Family delivery request.")
    candidate = base64.b64decode(request["candidate"], validate=True)
    if not 0 < len(candidate) <= MAX_FILE_BYTES:
        raise ValueError("Invalid private Family credential size.")
    settings = delivery_settings(request["settings"])
    mail = mail_class.from_payload(request["mail"])
    if any(getattr(mail, key) != settings[key] for key in ("sender", "reply_to")):
        raise ValueError("Private Family delivery context differs.")
    return candidate, settings, mail


def main():
    """Malformed invocation emits no outcome; the parent conservatively holds it."""
    logging.disable(logging.CRITICAL)
    try:
        candidate, settings, mail = decode_request(sys.stdin.buffer.read(MAX_INPUT + 1))
        result = deliver_family(candidate, settings, mail)
        sys.stdout.write(json.dumps(result.payload(), separators=(",", ":")) + "\n")
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
