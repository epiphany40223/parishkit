"""Finite weekly mail helper: private bytes in, closed outcome out, no ORM."""

import json
import logging
import sys

from .family_delivery_process import MAX_WEEKLY_INPUT
from .family_delivery_worker import decode_envelope
from .weekly_delivery import WeeklyDeliveryMail, deliver_weekly


def decode_request(raw):
    """Validate only the no-attachment weekly payload before credential exchange."""
    return decode_envelope(raw, mail_class=WeeklyDeliveryMail, limit=MAX_WEEKLY_INPUT)


def main():
    """Never emit private diagnostics, including for malformed direct invocation."""
    logging.disable(logging.CRITICAL)
    try:
        candidate, settings, mail = decode_request(
            sys.stdin.buffer.read(MAX_WEEKLY_INPUT + 1)
        )
        result = deliver_weekly(candidate, settings, mail)
        sys.stdout.write(json.dumps(result.payload(), separators=(",", ":")) + "\n")
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
