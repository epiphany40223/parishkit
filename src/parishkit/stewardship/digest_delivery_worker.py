"""One finite private Admin digest helper with no retained data or diagnostics."""

import json
import logging
import sys

from .digest_delivery import DigestDeliveryMail, deliver_digest
from .family_delivery_process import MAX_DIGEST_INPUT
from .family_delivery_worker import _decode_request


def decode_request(raw):
    """Validate only compiled one-Admin chart mail before any credential exchange."""
    return _decode_request(raw, mail_class=DigestDeliveryMail, limit=MAX_DIGEST_INPUT)


def main():
    """Emit only the closed delivery outcome; malformed invocation emits nothing."""
    logging.disable(logging.CRITICAL)
    try:
        candidate, settings, mail = decode_request(
            sys.stdin.buffer.read(MAX_DIGEST_INPUT + 1)
        )
        result = deliver_digest(candidate, settings, mail)
        sys.stdout.write(json.dumps(result.payload(), separators=(",", ":")) + "\n")
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
