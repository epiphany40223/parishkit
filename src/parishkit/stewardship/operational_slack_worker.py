"""Private operational Slack helper with a fixed fact schema and finite input."""

import base64
import json
import logging
import sys

from parishkit.config import ConfigError

from .accounts.integration_candidates import _object
from .operational_delivery import OperationalSlack, deliver_operational_slack
from .readiness_delivery import DeliveryOutcome

MAX_INPUT = 16384


def decode_request(raw):
    """Parse the whole bounded envelope before any credential exchange or POST."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_INPUT:
        raise ValueError("Invalid private operational notification request.")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(request) is not dict
        or set(request) != {"candidate", "notification"}
        or type(request["candidate"]) is not str
    ):
        raise ValueError("Invalid private operational notification request.")
    candidate = base64.b64decode(request["candidate"], validate=True)
    if not 0 < len(candidate) <= 4098:
        raise ValueError("Invalid private notification credential.")
    return candidate, OperationalSlack.from_payload(request["notification"])


def submit_request(raw):
    """A malformed request is unsent; exceptions after transport entry are unknown."""
    try:
        candidate, notification = decode_request(raw)
    except (ValueError, TypeError, ConfigError, RecursionError):
        return DeliveryOutcome.NOT_SENT
    try:
        result = deliver_operational_slack(candidate, notification)
        return (
            result if isinstance(result, DeliveryOutcome) else DeliveryOutcome.UNKNOWN
        )
    except Exception:
        return DeliveryOutcome.UNKNOWN


def main():
    """Emit a closed acknowledgement and no private diagnostics or logging."""
    logging.disable(logging.CRITICAL)
    result = submit_request(sys.stdin.buffer.read(MAX_INPUT + 1))
    sys.stdout.write(result.value + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
