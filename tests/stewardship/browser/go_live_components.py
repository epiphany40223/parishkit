"""Real readiness/cleanup templates with synthetic nonprivate view projections."""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import window_table

# The real addresses of Go-live readiness, Testing cleanup, Prepare Family
# links and Production activation, where their Refresh links lead (#519 PR
# 7b-2). A test answers each in-place read with a later version of the page
# (the "-fresh" paths below), so the refreshed region differs.
GO_LIVE = reverse("admin:go_live")
CLEANUP = reverse("admin:go_live_cleanup", args=[UUID(int=59)])
LINKS = reverse("admin:go_live_links", args=[UUID(int=59)])
PRODUCTION = reverse("admin:production_progress")

# Rows as content_views.resave_recommended builds them; the long subject
# checks wrapping at phone width.
RESAVE = (
    {
        "label": "Family welcome",
        "subject": None,
        "url": "/admin/campaign/content/page/welcome/",
        "blocked": False,
    },
    {
        "label": "Initial invitation",
        "subject": "Your annual census and stewardship renewal for the coming year",
        "url": "/admin/campaign/content/email/initial/"
        "00000000-0000-0000-0000-000000000061/",
        "blocked": True,
    },
)


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
        # Saved content to re-save (#838): a page and a blocked invitation.
        resave=RESAVE,
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
        "fresh": True,
    }
    # A stale sign-in (#547): the step-up in place of the Start button.
    stale = ready | {"fresh": False}
    progress = {
        "campaign": campaign,
        "status": Value(
            state="cleanup_failed", checkpoint_sequence=5, request_id=UUID(int=59)
        ),
        "completion": Percentage(5000, 12000),
        "task": Value(state="failed", updated_at=context["server_now"]),
        "controls": {"cancel": "synthetic-cancel", "retry": "synthetic-retry"},
    }
    links = {
        "campaign": campaign,
        "transition": Value(pk=UUID(int=59)),
        # The listed preparation can still be cancelled, so Prepare says so.
        "unavailable": "prepared",
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
    }
    links["table"] = window_table(PageWindow(1, 25), links["records"], False)
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
    # Later versions of each page, for its Refresh to bring in place: the
    # cleanup finished, the links prepared, the checks passed, and the
    # Production mail prepared once go-live can no longer be cancelled.
    finished = Value(state="succeeded", updated_at=context["server_now"])
    cleaned = progress | {
        "status": Value(
            state="cleanup_complete", checkpoint_sequence=9, request_id=UUID(int=59)
        ),
        "completion": Percentage(12000, 12000),
        "task": finished,
        "controls": {},
    }
    prepared = links | {
        "records": [
            links["records"][0]
            | {
                "task": finished,
                "generation": Value(state="ready"),
                "completion": Percentage(5000, 5000),
                "controls": {},
            }
        ],
    }
    prepared["table"] = window_table(PageWindow(1, 25), prepared["records"], False)
    produced = production | {
        "demand": Value(
            phase="done", groups_completed=2000, items_completed=9000, failure_code=""
        ),
        "task": finished,
        "complete": True,
        "control": None,
        "withdrawal_available": False,
    }
    unchecked = ready | {"origin_verified": None, "cleanup_token": None}
    problems = unchecked | {
        "problems": ["Save exactly one initial invitation schedule."]
    }
    return {
        path: (
            "text/html",
            render_to_string(template, context | {"admin_chrome": admin} | values),
        )
        for path, template, values in (
            ("/go-live", "stewardship/go-live-readiness.html", ready),
            ("/go-live-stale", "stewardship/go-live-readiness.html", stale),
            (
                "/go-live-stale-local",
                "stewardship/go-live-readiness.html",
                stale | {"local_environment": True},
            ),
            # Before a passing check, and with a problem (#563).
            (
                "/go-live-unchecked",
                "stewardship/go-live-readiness.html",
                ready | {"origin_verified": None, "cleanup_token": None},
            ),
            (
                "/go-live-problems",
                "stewardship/go-live-readiness.html",
                ready
                | {
                    "problems": ["Save exactly one initial invitation schedule."],
                    "cleanup_token": None,
                },
            ),
            (GO_LIVE, "stewardship/go-live-readiness.html", problems),
            ("/go-live-fresh", "stewardship/go-live-readiness.html", unchecked),
            (CLEANUP, "stewardship/go-live-cleanup.html", progress),
            ("/go-live-cleanup-fresh", "stewardship/go-live-cleanup.html", cleaned),
            (LINKS, "stewardship/go-live-links.html", links),
            ("/go-live-links-fresh", "stewardship/go-live-links.html", prepared),
            (
                PRODUCTION,
                "stewardship/production-progress.html",
                production | {"withdrawal_available": True},
            ),
            (
                "/production-progress-fresh",
                "stewardship/production-progress.html",
                produced,
            ),
            (
                "/production-confirmation-unchecked",
                "stewardship/production-confirmation.html",
                confirmation | {"confirmation_token": None, "origin_verified": None},
            ),
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
                "/production-scheduled",
                "stewardship/production-progress.html",
                production
                | {
                    "campaign": Value(**(vars(campaign) | {"state": "scheduled"})),
                    "demand": None,
                    "withdrawal_available": True,
                },
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
