"""Private operational SMTP helper: validated facts in, closed outcome out."""

import json
import logging
import sys

from .family_delivery_worker import decode_envelope
from .operational_delivery import OperationalMail, deliver_operational_mail
from .readiness_delivery_worker import MAX_INPUT


def decode_request(raw):
    """No ORM or raw rendered content is accepted by this standalone process."""
    return decode_envelope(raw, mail_class=OperationalMail, limit=MAX_INPUT)


def main():
    """Never expose input, provider diagnostics or credentials on either output."""
    logging.disable(logging.CRITICAL)
    try:
        candidate, settings, mail = decode_request(sys.stdin.buffer.read(MAX_INPUT + 1))
        result = deliver_operational_mail(candidate, settings, mail)
        sys.stdout.write(json.dumps(result.payload(), separators=(",", ":")) + "\n")
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
