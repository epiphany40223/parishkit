"""Update ParishSoft (#528, ADM-09; #1688): the page as the view draws it.

Check ParishSoft posts to the page itself, and the real view redirects back
to it, remembering the check in the session; the static fixture server
cannot remember, so its redirect adds a query that stands in for the
session (the same path, so the answer is still this page to ui-v1.js). The
check's status region polls the task's passive status fragment, which a
test answers with the finished check, and the page's in-place re-read with
the page showing the plan.

Update N changes and Run again post their review step to the confirmation
and retries addresses, which answer with the dialog's fragment as the view
draws it; Apply posts to the same address (the fixture answers it the same
way, a 200 with no live status), and the page's in-place re-read is then
answered by a test with the page showing the run.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.jobs.views import PUBLICATION_CHECK, UPDATE_RUN
from parishkit.stewardship.reports import census_publication as publication
from parishkit.stewardship.reports.census_changes import shape
from parishkit.stewardship.web.tables import paginate

PUBLICATION = reverse("admin:update_parishsoft")
CONFIRMATION = reverse("admin:update_parishsoft_confirmation")
RETRIES = reverse("admin:update_parishsoft_retries")
REQUESTED = PUBLICATION + "?check=requested"
CHECK_RUN = UUID(int=5280)
CHECK_STATUS = reverse("admin:background_task_status", args=[CHECK_RUN])
UPDATE_TASK = UUID(int=16880)
UPDATE_STATUS = reverse("admin:background_task_status", args=[UPDATE_TASK])
PLAN = UUID(int=16881)
# The passive status a test answers the poll with once the check ended, and
# the page the in-place re-read then brings.
CHECK_FINISHED_STATUS = "/publication-check-finished-status"
CHECKED = "/publication-checked"
OFF = "/publication-off"
RUNNING = "/publication-running"
# Update's states (#1688): an hour-old check, the run under way and its
# finished status, the run done, and a run that stopped before it finished.
EXPIRED = "/publication-expired"
UPDATING = "/publication-updating"
UPDATE_FINISHED_STATUS = "/publication-update-finished-status"
UPDATED = "/publication-updated"
STOPPED = "/publication-stopped"
COUNTS = {"write_fields": 2, "write_entities": 2, "blocked_entities": 1}


def _review(context):
    """A review dialog answer, as the view's fragment draws it."""
    return render_to_string("stewardship/update-parishsoft-review.html", context)


POSTS = {
    PUBLICATION: (303, REQUESTED + f"#{PUBLICATION_CHECK}", ""),
}


def components(context, admin):
    """The page switched off, ready, checking, checked, held and updating,
    the check's and the run's status fragments, at their addresses; and the
    dialog's answers, which ``POSTS`` gains."""
    now = context["server_now"]
    page = context | {"admin_chrome": admin}

    def change(index, outcome, blocked=False, result=None, stage="ready_to_update"):
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
            SimpleNamespace(outcome=outcome),
            shaped | {"stage": stage},
            blocked=blocked,
            accepted=False,
            result=result,
        )

    def task(run_id, name, kind, state, current=0, total=0):
        """A task's values in ``state``, as ``_task_read`` gives them."""
        return {
            "id": run_id,
            "name": name,
            "type": kind,
            "state": state,
            "active": True,
            "attempt": 1,
            "retry_sequence": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "initiator_id": "admin",
            "progress": {
                "phase": "unspecified",
                "current": current,
                "total": total,
                "display": Percentage(current, total),
            },
        }

    def run(state):
        """The status region's values for the check in ``state``."""
        return {
            "task": task(
                CHECK_RUN,
                "Checking census changes against ParishSoft",
                "census_publication_preflight",
                state,
            ),
            "work": {"events": []},
            "run_task_id": CHECK_RUN,
            "region_id": PUBLICATION_CHECK,
            "publication_check": True,
            "read_unconfirmed": False,
            "status_url": CHECK_STATUS + "?view=publication",
        }

    def update_run(state):
        """The plan region's values for the update run in ``state``."""
        done = 2 if state != "running" else 1
        return {
            "task": task(
                UPDATE_TASK,
                "Updating ParishSoft with census changes",
                "census_publication",
                state,
                done,
                2,
            ),
            "work": {"events": []},
            "region_id": UPDATE_RUN,
            "update_run": True,
            "status_url": UPDATE_STATUS + "?view=update",
        }

    def draw(
        *,
        enabled=True,
        gate=None,
        state=None,
        plan=False,
        offer=None,
        update=None,
        rows=None,
        refreshing=False,
        key=5281,
    ):
        """The page with ``gate`` holding Check ParishSoft, the check in
        ``state`` below it, and with ``plan``, the newest check's table,
        its ``offer`` (Update by default), the update run in ``update`` and
        the table's ``rows`` after that run."""
        view = table = None
        if plan:
            rows = rows or [
                change(0, "write"),
                change(1, "write", blocked=True),
                change(1, "cannot_check", blocked=True),
            ]
            view = {
                "plan": SimpleNamespace(
                    pk=PLAN, created_at=now - timedelta(minutes=2), testing=False
                ),
                "checked_by": "admin@example.org",
                "rows": rows,
                "others": 0,
                "summary": publication.summary(COUNTS | {"already": 0}),
                "update_label": publication.update_label(COUNTS),
                "offer": offer or publication.Offer("update"),
                "refreshing": refreshing,
            }
            table = paginate(rows, {}, sorting=publication.SORTING)
        extra = (
            {"update_status": update_run(update), "update_task_id": UPDATE_TASK}
            if update
            else {}
        )
        return render_to_string(
            "stewardship/update-parishsoft.html",
            page
            | (run(state) if state else {})
            | extra
            | {
                "enabled": enabled,
                "waiting": {"changes": 3, "members": 2, "away": 0} if enabled else None,
                "gate": gate if enabled else publication.OFF,
                "view": view,
                "table": table,
                "request_key": UUID(int=key),
            },
        )

    def fragment(values):
        """A task's passive status fragment."""
        return render_to_string("stewardship/background-task-status.html", values)

    after = [
        change(0, "write", result=(publication.UPDATED, ""), stage="confirmed"),
        change(2, "write", result=(publication.FAILED, "write_rejected")),
        change(1, "write", blocked=True),
    ]
    pages = {
        PUBLICATION: draw(),
        REQUESTED: draw(state="queued"),
        CHECK_STATUS: fragment(run("running")),
        CHECK_FINISHED_STATUS: fragment(run("succeeded")),
        CHECKED: draw(state="succeeded", plan=True, key=5282),
        OFF: draw(enabled=False),
        RUNNING: draw(
            gate=publication.RUNNING,
            plan=True,
            offer=publication.Offer("update", publication.RUNNING),
        ),
        EXPIRED: draw(
            plan=True, offer=publication.Offer("update", publication.EXPIRED)
        ),
        UPDATING: draw(
            gate=publication.RUNNING,
            plan=True,
            offer=publication.Offer(None),
            update="running",
        ),
        UPDATE_STATUS: fragment(update_run("running")),
        UPDATE_FINISHED_STATUS: fragment(update_run("succeeded")),
        UPDATED: draw(
            plan=True,
            offer=publication.Offer(None),
            update="succeeded",
            rows=after,
            refreshing=True,
        ),
        STOPPED: draw(
            plan=True,
            offer=publication.Offer("rerun", None, 1),
            update="failed",
            rows=[
                change(0, "write", result=(publication.NOT_FINISHED, "")),
            ],
        ),
    }
    POSTS[CONFIRMATION] = (
        200,
        None,
        _review(
            {
                "update": {
                    "preview": True,
                    "plan": SimpleNamespace(pk=PLAN),
                    "counts": COUNTS,
                    "title": publication.update_question(COUNTS),
                    "key": UUID(int=5283),
                }
            }
        ),
    )
    POSTS[RETRIES] = (
        200,
        None,
        _review(
            {
                "rerun": {
                    "preview": True,
                    "plan": SimpleNamespace(pk=PLAN),
                    "title": publication.rerun_question(1),
                    "key": UUID(int=5284),
                }
            }
        ),
    )
    return {path: ("text/html", body) for path, body in pages.items()}
