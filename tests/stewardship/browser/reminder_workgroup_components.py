"""The Reminder WorkGroup setting (#861), rendered from its real templates.

Each path shows one state of the page (what the newest refresh found) or the
review step, so the browser checks need no database or ParishSoft. Review,
apply and the change's status happen in place (#750): ``EDITOR`` is the
page with no WorkGroup set, ``REVIEW`` its review of setting ``NAME`` (the
browser tests answer the form's Review POST with it), and ``POSTS`` sends
Apply's POST to ``PENDING``, the page naming the change, whose status polls
``STATUS``; that answers Applied and follows to ``SETTLED``, the page with
``NAME`` saved, awaiting the next refresh.
"""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import review_region
from parishkit.stewardship.accounts.reminder_workgroup_views import WorkGroupForm

NAME = "Active: Stewardship 2027"
DIGEST = "a" * 64
EDITOR = "/reminder-workgroup-off"
REVIEW = "/reminder-workgroup-preview"
STATUS = "/reminder-workgroup-status"
REQUEST = UUID("75000000-0000-4000-8000-000000000750")
PENDING = f"{EDITOR}?request={REQUEST}"
SETTLED = f"{EDITOR}?request={REQUEST}&settled=1"
POSTS = {EDITOR: (303, PENDING, "")}
STEPS = ("Make changes", "Review", "Apply")


def _steps(current):
    """The flow indicator with ``current`` (0-based) as the current step."""
    return [
        {
            "label": label,
            "state": "done"
            if index < current
            else "current"
            if index == current
            else "upcoming",
        }
        for index, label in enumerate(STEPS)
    ]


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
    pending = Value(state="staged", request_id=REQUEST, failure_code="")
    applied = Value(state="applied", request_id=REQUEST, failure_code="")

    def page(current, evidence, step=0, digest=DIGEST, **region):
        """The page at ``step`` with ``region``'s review context (#750)."""
        form = WorkGroupForm(initial={"name": current or "", "base_digest": digest})
        extra = review_region("reminder_workgroup", None, **region)
        if region.get("receipt"):
            # The fixture server serves Change status here.
            extra["status_url"] = STATUS
        return (
            "text/html",
            render_to_string(
                "stewardship/reminder-workgroup.html",
                values
                | {
                    "admin_chrome": admin | {"flow_steps": _steps(step)},
                    "form": form,
                    "current": current,
                    "evidence": evidence,
                }
                | extra,
            ),
        )

    responses = {
        f"/reminder-workgroup-{state}": page(current, evidence)
        for state, (current, evidence) in states.items()
    }
    responses[REVIEW] = page(
        None,
        None,
        1,
        review={
            "template": "stewardship/reminder-workgroup-review.html",
            "notes": [],
            "before": None,
            "after": NAME,
            "preview": "synthetic",
        },
    )
    responses[PENDING] = page(None, None, 2, receipt=pending)
    responses[STATUS] = (
        "text/html",
        render_to_string(
            "stewardship/configuration-request.html",
            context
            | {"admin_chrome": admin, "receipt": applied, "follow_url": SETTLED},
        ),
    )
    responses[SETTLED] = page(NAME, None, 2, digest="b" * 64, receipt=applied)
    return responses
