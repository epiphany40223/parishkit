"""Real readiness/cleanup templates with synthetic nonprivate view projections."""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.campaigns.domain import Percentage


def components(context, admin):
    """Keep acknowledgement and durable status browser tests free of infrastructure."""
    campaign = Value(
        pk=UUID(int=58),
        state="active",
        active_configuration=Value(
            name="Annual census",
            starts_at=context["server_now"],
            ends_at=context["deadline"],
        ),
    )
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
    links = {
        "campaign": campaign,
        "transition": Value(pk=UUID(int=59)),
        "records": [
            {
                "preparation": Value(
                    pk=UUID(int=60),
                    created_at=context["server_now"],
                    eligible_count=5000,
                ),
                "task": Value(state="failed", updated_at=context["server_now"]),
                "current": True,
                "completion": Percentage(2500, 5000),
                "controls": {"retry": "synthetic-retry", "cancel": "synthetic-cancel"},
            }
        ],
        "page": 1,
    }
    confirmation = ready | {
        "state": Value(
            target_state="active",
            observed_at=context["server_now"],
            impact_revision=1234,
        ),
        "transition": links["transition"],
        "confirmation_token": "synthetic-confirmation",
        "fresh": True,
    }
    production = {
        "campaign": campaign,
        "receipt": Value(created_at=context["server_now"]),
        "demand": Value(
            phase="families",
            groups_completed=1234,
            items_completed=5678,
            failure_code="recovery_required",
        ),
        "task": progress["task"],
        "complete": False,
        "control": "synthetic-retry",
        "outcomes": [
            {
                "label": "Family message candidates",
                "preview": 5000,
                "actual": 4800,
                "difference": -200,
            }
        ],
    }
    return {
        path: (
            "text/html",
            render_to_string(template, context | {"admin_chrome": admin} | values),
        )
        for path, template, values in (
            ("/go-live", "stewardship/go-live-readiness.html", ready),
            ("/go-live-cleanup", "stewardship/go-live-cleanup.html", progress),
            ("/go-live-links", "stewardship/go-live-links.html", links),
            (
                "/production-confirmation",
                "stewardship/production-confirmation.html",
                confirmation,
            ),
            (
                "/production-progress",
                "stewardship/production-progress.html",
                production,
            ),
            (
                "/production-withdrawal",
                "stewardship/production-withdrawal.html",
                {
                    "campaign": campaign,
                    "available": True,
                    "fresh": True,
                    "preview": {
                        "reason": "Correct the schedule",
                        "inventory": {
                            "occurrences": 1234,
                            "cancellable": 1200,
                            "messages": 1200,
                            "failed": 34,
                            "delivered": 0,
                            "blocking": 0,
                        },
                    },
                    "withdrawal_token": "synthetic-withdrawal",
                },
            ),
        )
    }
