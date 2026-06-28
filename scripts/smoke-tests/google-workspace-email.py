#!/usr/bin/env python3
"""Run a manual Google Workspace SMTP/XOAUTH2 smoke test."""

from __future__ import annotations

import argparse
from pathlib import Path

from parishkit.email.base import Email
from parishkit.email.google_workspace import GoogleWorkspaceSMTPProvider


def main() -> int:
    """Run.

    The top-level command keeps user-facing error handling here and
    delegates behavior to smaller helpers.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-account-file", required=True, type=Path)
    parser.add_argument("--delegated-user", required=True)
    parser.add_argument("--to", required=True)
    parser.add_argument("--subject", default="ParishKit email smoke test")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    print(f"Google Workspace email smoke-test from: {args.delegated_user}")
    print(f"Recipient: {args.to}")
    print(f"Subject: {args.subject}")
    if not args.send:
        print("Dry run only. Re-run with --send to load credentials and send.")
        return 0

    provider = GoogleWorkspaceSMTPProvider.from_config(
        {
            "service_account_file": str(args.service_account_file),
            "delegated_user": args.delegated_user,
        }
    )
    provider.send(
        Email(
            subject=args.subject,
            sender=args.delegated_user,
            to=[args.to],
            text="ParishKit manual Google Workspace email smoke test",
        ),
        dry_run=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
