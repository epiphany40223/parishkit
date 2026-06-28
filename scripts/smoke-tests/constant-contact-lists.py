#!/usr/bin/env python3
"""Run a manual read-only Constant Contact list smoke test.

Upstream contacts/lists docs:
https://developer.constantcontact.com/api_guide/contacts_overview.html
"""

from __future__ import annotations

import argparse
from pathlib import Path

from parishkit.constant_contact import (
    ConstantContactClient,
    ConstantContactConfig,
    get_access_token,
    load_client_id,
)


def main() -> int:
    """Run.

    The top-level command keeps user-facing error handling here and
    delegates behavior to smaller helpers.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id-file", required=True, type=Path)
    parser.add_argument("--access-token-file", required=True, type=Path)
    parser.add_argument("--send", action="store_true", help="execute read-only request")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="also read contacts and custom fields without mutating data",
    )
    args = parser.parse_args()

    print(f"Constant Contact client file: {args.client_id_file}")
    print(f"Constant Contact token file: {args.access_token_file}")
    if not args.send:
        print("Dry run only. Re-run with --send to read contact lists.")
        return 0

    client_id = load_client_id(args.client_id_file)
    access_token = get_access_token(args.access_token_file, client_id)
    client = ConstantContactClient(
        ConstantContactConfig(
            client_id=client_id,
            access_token=access_token,
        )
    )
    lists = client.get_all("contact_lists", "lists")
    print(f"Read {len(lists)} Constant Contact list(s).")
    if args.deep:
        contacts = client.get_all("contacts", "contacts", include="list_memberships")
        custom_fields = client.get_all("contact_custom_fields", "custom_fields")
        print(
            "Deep probe counts: "
            f"contacts={len(contacts)}, custom_fields={len(custom_fields)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
