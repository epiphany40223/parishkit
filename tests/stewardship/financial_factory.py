"""Synthetic financial configuration/coverage fixtures; no provider credentials."""

from uuid import UUID

CAMPAIGN = UUID("11111111-1111-4111-8111-111111111111")


def configuration():
    """One upcoming period and an explicitly different comparison fund mapping."""
    return {
        "name": "Synthetic campaign",
        "year_label": None,
        "timezone": "America/New_York",
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "modules": ["financial"],
        "ministry_duids": [],
        "financial": {
            "start": "2027-01-01",
            "end": "2027-12-31",
            "comparison_start": "2026-01-01",
            "comparison_end": "2026-12-31",
            "fund_duids": [4],
            "comparison_fund_duids": [9],
            "overlap_confirmed": False,
        },
        "share_options": [],
        "content_versions": {},
        "additional_information": False,
    }


def cursor(definition):
    """A full giving observation retained through a later Family-only delta."""
    digest = definition.window.digest
    return {
        "schema": "source-refresh-v1",
        "window_digest": digest,
        "full_snapshot_id": "22222222-2222-4222-8222-222222222222",
        "full_started_at": "2026-10-15T04:00:00+00:00",
        "watermark": "2026-10-16T04:00:00+00:00",
        "load": {
            "schema": "source-load-v1",
            "window_digest": digest,
            "as_of_date": "2026-10-16",
            "giving_as_of_date": "2026-10-15",
        },
    }


def record(amount="1.00", **changes):
    """Only Family-level canonical giving fields, not raw provider transactions."""
    return {
        "schema_version": 1,
        "family_key": "1",
        "fund_key": "9",
        "amount": amount,
        "effective_date": "2026-10-01",
        **changes,
    }
