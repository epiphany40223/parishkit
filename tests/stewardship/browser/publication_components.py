"""Publish to ParishSoft (#528, ADM-09): the page as the view draws it.

Check ParishSoft posts to the page itself, and the real view redirects back
to it, remembering the check in the session; the static fixture server
cannot remember, so its redirect adds a query that stands in for the
session (the same path, so the answer is still this page to ui-v1.js). The
check's status region polls the task's passive status fragment, which a
test answers with the finished check, and the page's in-place re-read with
the page showing the plan.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.jobs.views import PUBLICATION_CHECK
from parishkit.stewardship.reports import census_publication as publication
from parishkit.stewardship.reports.census_changes import shape
from parishkit.stewardship.web.tables import paginate

PUBLICATION = reverse("admin:census_publication")
REQUESTED = PUBLICATION + "?check=requested"
CHECK_RUN = UUID(int=5280)
CHECK_STATUS = reverse("admin:background_task_status", args=[CHECK_RUN])
# The passive status a test answers the poll with once the check ended, and
# the page the in-place re-read then brings.
CHECK_FINISHED_STATUS = "/publication-check-finished-status"
CHECKED = "/publication-checked"
OFF = "/publication-off"
RUNNING = "/publication-running"
POSTS = {PUBLICATION: (303, REQUESTED + f"#{PUBLICATION_CHECK}", "")}


def components(context, admin):
    """The page switched off, ready, checking, checked and while a run is
    unfinished, and the check's status fragment, at their addresses."""
    now = context["server_now"]
    page = context | {"admin_chrome": admin}

    def change(index, outcome, blocked=False):
        """One plan row for Member 100 + ``index``, worded by the view's code."""
        shaped = shape(
            {
                "id": str(index),
                "entity_kind": "member",
                "entity_key": str(100 + index),
                "field": "mobile_phone",
                "baseline_available": True,
                "baseline_value": "555-0100",
                "submitted_value": f"555-{index:04d}",
                "current_available": True,
                "current_value": "555-0100",
                "admin_value_set": False,
                "admin_value": None,
                "handling": "api",
                "decision": "approved",
                "execution": "pending",
                "submitted_at": datetime(2054, 10, 5, 12, tzinfo=UTC),
                "family_duid": 1000 + index,
                "family_name": f"Family {index:02d}",
                "member_name": f"Member {index:02d}",
                "version": 2,
            }
        )
        return publication.plan_row(
            SimpleNamespace(outcome=outcome), shaped, blocked=blocked, accepted=False
        )

    def task(state):
        """The check's task values in ``state``, as ``_task_read`` gives them."""
        return {
            "id": CHECK_RUN,
            "name": "Checking census changes against ParishSoft",
            "type": "census_publication_preflight",
            "state": state,
            "active": True,
            "attempt": 1,
            "retry_sequence": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "progress": {
                "phase": "unspecified",
                "current": 0,
                "total": 0,
                "display": Percentage(0, 0),
            },
        }

    def run(state):
        """The status region's values for the check in ``state``."""
        return {
            "task": task(state),
            "work": {"events": []},
            "run_task_id": CHECK_RUN,
            "region_id": PUBLICATION_CHECK,
            "publication_check": True,
            "read_unconfirmed": False,
            "status_url": CHECK_STATUS + "?view=publication",
        }

    def draw(*, enabled=True, gate=None, state=None, plan=False, key=5281):
        """The page with ``gate`` holding Check ParishSoft, the check in
        ``state`` below it, and with ``plan``, the newest check's table."""
        view = table = None
        if plan:
            rows = [
                change(0, "write"),
                change(1, "write", blocked=True),
                change(1, "cannot_check", blocked=True),
            ]
            view = {
                "plan": SimpleNamespace(
                    created_at=now - timedelta(minutes=2), testing=False
                ),
                "checked_by": "admin@example.org",
                "rows": rows,
                "others": 0,
                "summary": publication.summary(
                    {
                        "write_fields": 1,
                        "write_entities": 1,
                        "blocked_entities": 1,
                        "already": 0,
                    }
                ),
            }
            table = paginate(rows, {}, sorting=publication.SORTING)
        return render_to_string(
            "stewardship/census-publication.html",
            page
            | (run(state) if state else {})
            | {
                "enabled": enabled,
                "waiting": {"changes": 3, "members": 2} if enabled else None,
                "gate": gate if enabled else publication.OFF,
                "view": view,
                "table": table,
                "request_key": UUID(int=key),
            },
        )

    def fragment(state):
        """The check's passive status fragment in ``state``."""
        return render_to_string("stewardship/background-task-status.html", run(state))

    pages = {
        PUBLICATION: draw(),
        REQUESTED: draw(state="queued"),
        CHECK_STATUS: fragment("running"),
        CHECK_FINISHED_STATUS: fragment("succeeded"),
        CHECKED: draw(state="succeeded", plan=True, key=5282),
        OFF: draw(enabled=False),
        RUNNING: draw(gate=publication.RUNNING, plan=True),
    }
    return {path: ("text/html", body) for path, body in pages.items()}
