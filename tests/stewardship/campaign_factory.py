"""Fresh campaign and schedule records for pure and disposable database tests."""

from uuid import uuid4


def campaign(**overrides):
    """A complete census-only draft in the synthetic parish timezone."""
    return {
        "id": str(uuid4()),
        "values": {
            "name": "Annual campaign",
            "year_label": "2027",
            "timezone": "America/New_York",
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "modules": ["census"],
            "ministry_duids": [],
            "financial": None,
            "share_options": [],
            "content_versions": {},
            "additional_information": True,
            **overrides,
        },
    }


def schedule(owner_id, **overrides):
    """A one-time initial invitation; callers explicitly supply its owning identity."""
    return {
        "id": str(uuid4()),
        "values": {
            "campaign_id": owner_id,
            "kind": "initial",
            "date": "2026-10-01",
            "time": "09:00:00",
            "weekday": None,
            "subject": "Campaign invitation",
            "template_version": str(uuid4()),
            **overrides,
        },
    }


def financial(**overrides):
    """Exact-year proposed/comparison periods with explicit independent fund
    mappings.
    """
    return {
        "start": "2027-01-01",
        "end": "2027-12-31",
        "comparison_start": "2026-01-01",
        "comparison_end": "2026-12-31",
        "fund_duids": [1],
        "comparison_fund_duids": [2],
        "overlap_confirmed": False,
        **overrides,
    }
