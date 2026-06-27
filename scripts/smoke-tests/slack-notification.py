#!/usr/bin/env python3
"""Send a manual Slack smoke-test notification."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from parishkit.logging import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slack-token-file", required=True, type=Path)
    parser.add_argument("--slack-channel", required=True)
    parser.add_argument(
        "--message",
        default="ParishKit manual Slack smoke test",
        help="non-secret message text to send",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="actually send the Slack message after previewing it",
    )
    args = parser.parse_args()

    print(f"Slack smoke-test message target: {args.slack_channel!r}")
    print(f"Slack smoke-test message text: {args.message}")
    if not args.send:
        print("Dry run only. Re-run with --send to post the message.")
        return 0

    logger = setup_logging(
        logger_name="parishkit.smoke.slack",
        verbose=True,
        slack_token_file=args.slack_token_file,
        slack_channel=args.slack_channel,
        slack_level="INFO",
    )
    logger.info(args.message)
    logging.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
