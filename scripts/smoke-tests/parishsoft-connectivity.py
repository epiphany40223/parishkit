#!/usr/bin/env python3
"""Run a manual read-only ParishSoft connectivity smoke test."""

from __future__ import annotations

import argparse
from pathlib import Path

from parishkit.parishsoft import ParishSoftClient, ParishSoftConfig, parse_cache_limit


def count_items(payload) -> int:
    """Count items returned by a ParishSoft endpoint."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return len(payload["data"])
    return len(payload)


def main() -> int:
    """Run.

    The top-level command keeps user-facing error handling here and
    delegates behavior to smaller helpers.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--expected-organization")
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("/tmp/parishkit-ps-smoke")
    )
    parser.add_argument(
        "--cache-limit",
        default="0s",
        help="cache age for smoke-test responses; default 0s forces live checks",
    )
    parser.add_argument("--send", action="store_true", help="execute read-only request")
    parser.add_argument(
        "--deep",
        action="store_true",
        help=(
            "also probe representative read-only family/member/ministry/offering "
            "endpoints"
        ),
    )
    args = parser.parse_args()

    print(f"ParishSoft API key file: {args.api_key_file}")
    print(f"Expected organization: {args.expected_organization or '(not checked)'}")
    if not args.send:
        print("Dry run only. Re-run with --send to call organizations/search.")
        return 0

    api_key = args.api_key_file.read_text(encoding="utf-8").strip()
    client = ParishSoftClient(
        ParishSoftConfig(
            api_key=api_key,
            cache_dir=args.cache_dir,
            expected_organization=args.expected_organization,
            cache_limit=parse_cache_limit(args.cache_limit),
        )
    )
    org_id = client.validate_organization()
    print(f"ParishSoft organization validated: {org_id}")
    if args.deep:
        families = client.post(
            "families/search",
            {"organizationIDs": [org_id], "PageNumber": 1, "Limit": 1},
        )
        members = client.post(
            "members/search",
            {"organizationIDs": [org_id], "startRowIndex": 1, "maximumRows": 1},
        )
        contactinfos = client.post(
            "members/contact/list",
            {"organizationIDs": [org_id], "Offset": 1, "Limit": 1},
        )
        family_groups = client.get("families/group/lookup/list")
        member_workgroups = client.get(
            "members/workgroup/lookup/list",
            {"PageNumber": 1, "Limit": 1},
        )
        ministry_types = client.get(
            "ministry/type/list",
            {"PageNumber": 1, "Limit": 1},
        )
        funds = client.get(f"offering/{org_id}/funds")
        print(
            "Deep probe counts: "
            f"families={count_items(families)}, members={count_items(members)}, "
            f"contactinfos={count_items(contactinfos)}, "
            f"family_groups={count_items(family_groups)}, "
            f"member_workgroups={count_items(member_workgroups)}, "
            f"ministry_types={count_items(ministry_types)}, funds={count_items(funds)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
