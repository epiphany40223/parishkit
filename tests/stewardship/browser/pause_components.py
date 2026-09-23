"""Render real pause/resume/closed templates with nonprivate sample projections."""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string


def components(context, admin):
    """Keep browser checks independent of costly database and provider startup."""
    inventory = {
        "queued": 1234,
        "held": 1234,
        "submitting": 1,
        "unknown": 2,
        "stranded": 3,
        "types": {
            kind: {"queued": 1234, "held": 1234, "submitting": 1, "unknown": 2}
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
    for action in ("pause", "resume", "resolve"):
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
                | {
                    "campaign": campaign,
                    "resume_available": action == "resume",
                    "resolve_available": action == "resolve",
                    "preview": {
                        "action": action,
                        "reason": "Inspect delivery",
                        "selection": selection,
                        "inventory": inventory,
                    },
                },
            ),
        )
    return responses
