"""Private bounded-helper protocol for one authorized readiness mail submission.

Only a closed outcome is emitted. The parent supplies credentials through pipes,
enforces the total wall-clock limit and kills/reaps the helper on ownership loss.
The SQL/Task owner, not this internal decoder, authorizes the submission.
"""

import base64
import json
import logging
import sys

from parishkit.config import ConfigError

from .accounts.integration_candidates import _object
from .accounts.key_files import MAX_FILE_BYTES
from .accounts.provider_context import validated_context
from .readiness_delivery import DeliveryOutcome, deliver_sample
from .readiness_mail import ReadinessMail
from .web.content import MAX_TEXT_BYTES

MAX_INPUT = 2 * MAX_FILE_BYTES + 4 * MAX_TEXT_BYTES + 16384


def decode_request(raw):
    """Reject duplicate keys and unbounded private inputs before any network IO."""
    if type(raw) is not bytes or len(raw) > MAX_INPUT:
        raise ValueError("Invalid readiness delivery request.")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(request) is not dict
        or set(request) != {"settings", "candidate", "mail"}
        or type(request["candidate"]) is not str
    ):
        raise ValueError("Invalid readiness delivery request.")
    settings = validated_context("google_workspace", request["settings"])
    candidate = base64.b64decode(request["candidate"], validate=True)
    if not 0 < len(candidate) <= MAX_FILE_BYTES:
        raise ValueError("Invalid readiness credential size.")
    mail = ReadinessMail.from_payload(request["mail"])
    if any(
        getattr(mail, key) != settings[key]
        for key in ("sender", "reply_to", "recipient")
    ):
        raise ValueError("Readiness mail context differs.")
    return candidate, settings, mail


def submit_request(raw):
    """Decoder failure proves non-submission; unexpected adapter failure does not."""
    try:
        candidate, settings, mail = decode_request(raw)
    except (ValueError, TypeError, ConfigError, RecursionError):
        return DeliveryOutcome.NOT_SENT
    try:
        result = deliver_sample(candidate, settings, mail)
        return (
            result if isinstance(result, DeliveryOutcome) else DeliveryOutcome.UNKNOWN
        )
    except Exception:
        return DeliveryOutcome.UNKNOWN


def main():
    """No provider error, key or message content is sent to stdout or logging."""
    logging.disable(logging.CRITICAL)
    outcome = submit_request(sys.stdin.buffer.read(MAX_INPUT + 1))
    sys.stdout.write(outcome.value + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
