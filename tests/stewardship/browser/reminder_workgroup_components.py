"""The Reminder WorkGroup setting (#861), rendered from its real templates.

Each path shows one state of the page (what the newest refresh found) or the
review step, so the browser checks need no database or ParishSoft.
"""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.reminder_workgroup_views import WorkGroupForm

NAME = "Active: Stewardship 2027"
DIGEST = "a" * 64


def components(context, admin):
    """The page with the WorkGroup found, missing and unset, and its review."""
    campaign = Value(
        pk=UUID(int=86), active_configuration=Value(name="Annual campaign")
    )
    values = context | {"admin_chrome": admin, "campaign": campaign}
    states = {
        "found": (NAME, {"name": NAME, "found": True, "family_duids": [1, 2]}),
        "missing": (NAME, {"name": NAME, "found": False, "family_duids": []}),
        "off": (None, None),
    }
    responses = {}
    for state, (current, evidence) in states.items():
        form = WorkGroupForm(
            initial={"name": current or "", "base_digest": DIGEST},
        )
        responses[f"/reminder-workgroup-{state}"] = (
            "text/html",
            render_to_string(
                "stewardship/reminder-workgroup.html",
                values | {"form": form, "current": current, "evidence": evidence},
            ),
        )
    responses["/reminder-workgroup-preview"] = (
        "text/html",
        render_to_string(
            "stewardship/reminder-workgroup-preview.html",
            values | {"before": None, "after": NAME, "preview": "synthetic"},
        ),
    )
    return responses
