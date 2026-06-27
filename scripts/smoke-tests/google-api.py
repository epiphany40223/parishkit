#!/usr/bin/env python3
"""Run a manual read-only Google API credential smoke test."""

from __future__ import annotations

import argparse
from pathlib import Path

from parishkit.google.auth import (
    build_service,
    execute_google_request,
    load_service_account_credentials,
    load_user_credentials,
    run_user_oauth_flow,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-account-file", type=Path)
    parser.add_argument("--user-token-file", type=Path)
    parser.add_argument("--client-secrets-file", type=Path)
    parser.add_argument(
        "--bootstrap-user-token",
        action="store_true",
        help="run installed-app OAuth and save --user-token-file",
    )
    parser.add_argument("--delegated-subject")
    parser.add_argument("--scope", action="append", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--calendar-id")
    parser.add_argument("--drive-file-id")
    parser.add_argument("--spreadsheet-id")
    parser.add_argument("--sheet-range", default="A1:A1")
    parser.add_argument("--group-key")
    parser.add_argument(
        "--send", action="store_true", help="execute the read-only call"
    )
    args = parser.parse_args()

    print(f"Google smoke-test service: {args.service} {args.version}")
    print(f"Scopes: {', '.join(args.scope)}")
    if args.service_account_file:
        print(f"Service account file: {args.service_account_file}")
    if args.user_token_file:
        print(f"User token file: {args.user_token_file}")
    if args.client_secrets_file:
        print(f"OAuth client secrets file: {args.client_secrets_file}")
    if not args.send:
        print("Dry run only. Re-run with --send to execute a read-only API call.")
        return 0

    if args.bootstrap_user_token:
        if not args.client_secrets_file or not args.user_token_file:
            parser.error(
                "--bootstrap-user-token requires --client-secrets-file "
                "and --user-token-file"
            )
        run_user_oauth_flow(
            args.client_secrets_file,
            args.user_token_file,
            scopes=args.scope,
        )
        print(f"Saved user OAuth token: {args.user_token_file}")
        if not args.service_account_file and args.user_token_file:
            return 0

    if args.service_account_file:
        credentials = load_service_account_credentials(
            args.service_account_file,
            scopes=args.scope,
            subject=args.delegated_subject,
        )
    elif args.user_token_file:
        credentials = load_user_credentials(args.user_token_file, scopes=args.scope)
    else:
        parser.error("provide --service-account-file or --user-token-file")

    service = build_service(args.service, args.version, credentials=credentials)
    if args.service == "calendar":
        if not args.calendar_id:
            parser.error("--calendar-id is required for calendar smoke tests")
        response = execute_google_request(
            service.events().list(calendarId=args.calendar_id, maxResults=1)
        )
        print(f"Read {len(response.get('items', []))} calendar event(s).")
    elif args.service == "drive":
        if not args.drive_file_id:
            parser.error("--drive-file-id is required for drive smoke tests")
        response = execute_google_request(
            service.files().get(
                fileId=args.drive_file_id,
                fields="id,name,mimeType",
                supportsAllDrives=True,
            )
        )
        print(f"Read Drive file metadata: {response.get('name', response.get('id'))}.")
    elif args.service == "sheets":
        if not args.spreadsheet_id:
            parser.error("--spreadsheet-id is required for sheets smoke tests")
        response = execute_google_request(
            service.spreadsheets()
            .values()
            .get(spreadsheetId=args.spreadsheet_id, range=args.sheet_range)
        )
        print(f"Read {len(response.get('values', []))} sheet row(s).")
    elif args.service == "admin":
        if not args.group_key:
            parser.error("--group-key is required for admin smoke tests")
        response = execute_google_request(
            service.members().list(groupKey=args.group_key, maxResults=1)
        )
        print(f"Read {len(response.get('members', []))} group member(s).")
    else:
        parser.error(
            "supported smoke-test services are calendar, drive, sheets, and admin"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
