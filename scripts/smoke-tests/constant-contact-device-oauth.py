#!/usr/bin/env python3
"""Run a manual Constant Contact device OAuth bootstrap.

Upstream OAuth device-flow docs:
https://developer.constantcontact.com/api_guide/device_flow.html
"""

from __future__ import annotations

import argparse
from pathlib import Path

from parishkit.constant_contact import (
    load_client_id,
    run_device_oauth_flow,
    save_access_token,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id-file", required=True, type=Path)
    parser.add_argument("--access-token-file", required=True, type=Path)
    parser.add_argument("--send", action="store_true", help="run the device flow")
    args = parser.parse_args()

    print(f"Constant Contact client file: {args.client_id_file}")
    print(f"Token output file: {args.access_token_file}")
    if not args.send:
        print("Dry run only. Re-run with --send to start device authorization.")
        return 0

    token = run_device_oauth_flow(load_client_id(args.client_id_file))
    save_access_token(args.access_token_file, token)
    print(f"Saved Constant Contact access token: {args.access_token_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
