"""Real readiness/cleanup templates with synthetic nonprivate view projections."""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.campaigns.domain import Percentage


def components(context, admin):
    """Keep acknowledgement and durable status browser tests free of infrastructure."""
    campaign = Value(pk=UUID(int=58), active_configuration=Value(name="Annual census"))
    preview = Value(
        observed_at=context["server_now"],
        target_state="active",
        source=Value(observed_at=context["server_now"]),
        cleanup=Value(
            submissions=1234,
            families=1000,
            messages=5000,
            unresolved=0,
            inventory=Value(total=12000),
        ),
        digests=Value(
            daily_messages=2,
            weekly_messages=0,
            coalesced_slots=19,
            empty_weekly_reports=1,
            blocked_groups=0,
        ),
    )
    ready = {
        "campaign": campaign,
        "preview": preview,
        "problems": [],
        "counts": Value(
            active=1500,
            messages=1000,
            coalesced_slots=2000,
            skipped_slots=0,
            blocked_families=0,
        ),
        "email_eligibility": Percentage(1000, 1500),
        "no_email": Percentage(500, 1500),
        "inventory": [("submissions", 1234)],
        "origin_verified": True,
        "cleanup_token": "synthetic-preview",
    }
    progress = {
        "campaign": campaign,
        "status": Value(state="cleanup_failed", checkpoint_sequence=5),
        "completion": Percentage(5000, 12000),
        "task": Value(state="failed", updated_at=context["server_now"]),
        "controls": {"cancel": "synthetic-cancel", "retry": "synthetic-retry"},
    }
    return {
        path: (
            "text/html",
            render_to_string(template, context | {"admin_chrome": admin} | values),
        )
        for path, template, values in (
            ("/go-live", "stewardship/go-live-readiness.html", ready),
            ("/go-live-cleanup", "stewardship/go-live-cleanup.html", progress),
        )
    }
