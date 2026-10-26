"""The Check parish data page in each state it shows (#2201).

Each is drawn from the real template with the stepper of a setup whose
earlier steps are done: before any check, while a check waits for the
installer's answer, with a current ready check, and after a failed one.
"""

from datetime import timedelta
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.setup_views import check_page

from .setup_back_path_components import ORGANIZATION, check, wizard

CHECK_INITIAL = "/setup-check-initial"
CHECK_WAITING = "/setup-check-waiting"
CHECK_READY = "/setup-check-ready"
CHECK_FAILED = "/setup-check-failed"
CHECK_UNAVAILABLE = "/setup-check-unavailable"
PAGES = (CHECK_INITIAL, CHECK_WAITING, CHECK_READY, CHECK_FAILED, CHECK_UNAVAILABLE)


def components(context, admin, now):
    """The Check parish data page before, during and after a check."""

    def page(shown, *, checked=False):
        """One Check parish data page showing the check ``shown`` (or none)."""
        steps = wizard(None, checked=checked)
        values = {
            "draft": {
                "status": {"attempt_id": uuid4(), "state": "collecting", "version": 4},
                "sections": {},
                "idle_at": now + timedelta(minutes=30),
                "absolute_at": now + timedelta(hours=12),
                "watchdog_at": None,
                "source_task_id": None,
                "source_task_state": None,
                "source_checked": checked,
            },
            "source_step": steps.step("source"),
        } | check_page(shown, ORGANIZATION)
        return (
            "text/html",
            render_to_string(
                "stewardship/setup-source.html",
                context | {"admin_chrome": admin, "wizard": steps} | values,
            ),
        )

    return {
        CHECK_INITIAL: page(None),
        CHECK_WAITING: page(check("queued")),
        CHECK_READY: page(check("ready"), checked=True),
        CHECK_FAILED: page(
            check("failed", error="parishsoft_organization_not_reached")
        ),
        CHECK_UNAVAILABLE: page(check("failed", error="parishsoft_unavailable")),
    }
