"""Bounded private notification helper; emits only a closed delivery outcome."""

import base64
import json
import logging
import sys

from parishkit.config import ConfigError

from .accounts.integration_candidates import _object
from .readiness_delivery import DeliveryOutcome
from .readiness_notification import ReadinessNotification, deliver_notification

MAX_INPUT = 16384


def decode_request(raw):
    """Fixed routing and bounded candidate bytes are the only private inputs."""
    if type(raw) is not bytes or len(raw) > MAX_INPUT:
        raise ValueError("Invalid private notification request.")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(request) is not dict
        or set(request) != {"candidate", "notification"}
        or type(request["candidate"]) is not str
    ):
        raise ValueError("Invalid private notification request.")
    candidate = base64.b64decode(request["candidate"], validate=True)
    if not 0 < len(candidate) <= 4098:
        raise ValueError("Invalid private notification credential.")
    return candidate, ReadinessNotification.from_payload(request["notification"])


def submit_request(raw):
    """Malformed inputs are unsent; exceptions after adapter entry are uncertain."""
    try:
        candidate, notification = decode_request(raw)
    except (ValueError, TypeError, ConfigError, RecursionError):
        return DeliveryOutcome.NOT_SENT
    try:
        result = deliver_notification(candidate, notification)
        return (
            result if isinstance(result, DeliveryOutcome) else DeliveryOutcome.UNKNOWN
        )
    except Exception:
        return DeliveryOutcome.UNKNOWN


def main():
    """Credentials and provider diagnostics never escape through stdout or logs."""
    logging.disable(logging.CRITICAL)
    result = submit_request(sys.stdin.buffer.read(MAX_INPUT + 1))
    sys.stdout.write(result.value + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
