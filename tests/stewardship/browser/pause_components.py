"""Render real pause/resume/closed templates with nonprivate sample projections."""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.delivery_control_commands import (
    resolution_choices,
)


def components(context, admin):
    """Keep browser checks independent of costly database and provider startup."""
    inventory = {
        "queued": 1234,
        "held": 1234,
        "submitting": 1,
        "unknown": 2,
        "stranded": 3,
        # Daily reports still have mail being handed over or uncertain, so
        # Cancel cannot take them (#563); receipts and weekly reports can.
        "types": {
            kind: {"queued": 1234, "held": 1234, "submitting": 0, "unknown": 0}
            | ({"submitting": 1, "unknown": 2} if kind == "daily_digest" else {})
            for kind in ("receipt", "daily_digest", "weekly_digest")
        },
    }
    values = context | {
        "admin_chrome": admin,
        "available": True,
        "fresh": True,
        "inventory": inventory,
        "health": {"ready": False},
        "next_due": {"at": context["deadline"].isoformat(), "count": 2},
        "control_token": "synthetic-pause-preview",
    }
    responses = {}
    # Closed-campaign resolution variants (#563): the provider and sender
    # check passed; nothing but stranded Family mail held (only Clear); the
    # same with an uncertain email; and held reports that all have uncertain
    # mail before the check: nothing can be resolved yet in the last two.
    counts = {"queued": 0, "held": 0, "submitting": 0, "unknown": 0}
    stranded = counts | {"held": 3, "stranded": 3, "types": {}}
    variants = {
        "resolve-ready": {
            "health": {"ready": True},
            "inventory": inventory
            | {
                "types": {
                    "receipt": counts | {"held": 5},
                    "daily_digest": counts | {"held": 4, "submitting": 1},
                    "weekly_digest": counts,
                },
            },
        },
        "resolve-stranded": {"inventory": stranded},
        "resolve-blocked": {"inventory": stranded | {"unknown": 1}},
        "resolve-stuck": {
            "inventory": inventory
            | {
                "types": {
                    "daily_digest": counts | {"held": 4, "unknown": 1},
                    "weekly_digest": counts | {"held": 2, "submitting": 1},
                },
            },
        },
    }
    for variant in (values, *variants.values()):
        current = variant.get("inventory", inventory)
        proof = variant.get("health", values["health"])
        variant["resolution"] = resolution_choices(current, proof)
    for action in ("pause", "resume", "resolve", *variants):
        campaign = Value(
            pk=UUID(int=62),
            active_configuration=Value(name="Annual campaign"),
            delivery_paused=action != "pause",
            pause_reason="Review sender settings",
            paused_at=context["server_now"],
        )
        selection = {
            "plan": "before_start" if action == "resume" else "closed",
            "decision": "cancel",
            "types": ["receipt", "weekly_digest"],
            "coverage": {"receipt": {"items": 1234, "daily_ranges": 0}},
        }
        responses[f"/delivery-{action}"] = (
            "text/html",
            render_to_string(
                "stewardship/delivery-control.html",
                values
                | variants.get(action, {})
                | {
                    "campaign": campaign,
                    "resume_available": action == "resume",
                    "resolve_available": action.startswith("resolve"),
                    "preview": {
                        "action": "resolve" if action.startswith("resolve") else action,
                        "reason": "Inspect delivery",
                        "selection": selection,
                        "inventory": inventory,
                    },
                },
            ),
        )
    return responses
