"""Bounded background task reads, moved out of Background work's views.

Background work (``jobs.views``) reads tasks through these functions, and so
does the Admin automation command line (ADM-11), so the page and the
command see the same rows, counts, filters and sort orders.
Nothing here takes a request or checks authority: each caller authorizes
first and rechecks after. Every value is an explicit whitelist of task
metadata, never the ORM object, its arguments, results or worker exceptions.
"""

import re

from django.db.models import Count, F, FloatField, Q
from django.db.models.functions import Cast, NullIf

from parishkit.stewardship.web.contracts import (
    PageWindow,
    expected_version,
    filters,
)
from parishkit.stewardship.web.tables import Sorting, bounded_count, read_window

from .models import NONTERMINAL_STATES, TASK_STATES, TaskRun

# Every Background work column sorts on the server. Task sorts by the stable
# internal type (the name shown is a translation of it) and Progress by the
# completed fraction; created_at is the default, newest first; id is the
# unique tiebreak. No index orders any of these keys, so a page is a top-N
# sort over the filtered set. The default view lists only nonterminal work,
# which the task_due (state, not_before) index narrows to a few rows; the
# task table itself is not purged, so "All" is a sequential scan plus that
# top-N sort, the same cost the newest-first order always had there.
TASK_SORTING = Sorting.by_column(
    {
        "task": ("task_type",),
        "state": ("state",),
        "phase": ("phase",),
        "progress": (
            Cast(F("progress_current"), FloatField()) / NullIf(F("progress_total"), 0),
        ),
        "heartbeat": ("heartbeat_at",),
        "created": ("created_at",),
    },
    default="-created",
    descending_first={"progress", "heartbeat", "created"},
    tiebreak=("id",),
)


# A task page's history sorts by every column too. Version is unique within
# one run and indexed with it (task_event_version, run_id + version), so it
# is the default (newest first) and the tiebreak; the other columns are
# top-N sorts over a single task's events, a set bounded by that task.
EVENT_SORTING = Sorting.by_column(
    {
        "version": ("version",),
        "time": ("created_at",),
        "action": ("action",),
        "state": ("state",),
        "progress": (
            "phase",
            Cast(F("progress_current"), FloatField()) / NullIf(F("progress_total"), 0),
        ),
    },
    default="-version",
    descending_first={"version", "time"},
    tiebreak=("version",),
)


def parse_window(parameters, *, listing):
    """Reject repeated/unknown/oversized inputs before selecting any task rows.

    Returns ``(window, state, task_type, sort)``. ``listing`` selects the
    task list's inputs (with the state and type filters) rather than one
    task's history.
    """
    allowed = (
        {"page", "size", "sort", "state", "task_type"}
        if listing
        else {"page", "size", "sort"}
    )
    selected = filters(parameters, allowed=allowed)
    window = PageWindow(
        expected_version(selected.get("page", "1")),
        expected_version(selected.get("size", "50" if listing else "20")),
    )
    state = selected.get("state", "nonterminal")
    if state not in {*TASK_STATES, "all", "nonterminal"}:
        raise ValueError("Unknown task state filter.")
    task_type = selected.get("task_type")
    if (
        task_type is not None
        and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", task_type) is None
    ):
        raise ValueError("Invalid task type filter.")
    # The sort token is validated here with the filters; listing and detail
    # read it.
    sorting = TASK_SORTING if listing else EVENT_SORTING
    return window, state, task_type, sorting.parse(selected)


def task_progress(row):
    """Closed phases and counts, with no arguments, worker exceptions or results."""
    return {
        "phase": row.phase,
        "current": row.progress_current,
        "total": row.progress_total,
        "percent": round(100 * row.progress_current / row.progress_total, 1)
        if row.progress_total
        else None,
    }


def task_metadata(row, instant):
    """Return an explicit public metadata whitelist, never serialize the ORM object."""
    return {
        "id": str(row.pk),
        "root_id": str(row.root_id),
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "retry_sequence": row.retry_sequence,
        "type": row.task_type,
        "state": row.state,
        "action": row.action,
        "version": row.version,
        "attempt": row.attempt,
        "initiator_id": str(row.initiated_by_id) if row.initiated_by_id else None,
        "progress": task_progress(row),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "not_before": row.not_before,
        "heartbeat_at": row.heartbeat_at,
        "lease_expires_at": row.lease_expires_at,
        "active": row.state == "running" and row.lease_expires_at > instant,
    }


def counts(instant):
    """Count indexed nonterminal work without fetching task identities or payloads."""
    return TaskRun.objects.filter(state__in=NONTERMINAL_STATES).aggregate(
        active=Count("id", filter=Q(state="running", lease_expires_at__gt=instant)),
        **{state: Count("id", filter=Q(state=state)) for state in NONTERMINAL_STATES},
    )


def listing(window, state, task_type, sort, instant):
    """Bound rows and count only indexed nonterminal work for the header indicator.

    Returns ``(data, shown)``. ``matching`` is a bounded count of the
    filtered rows (see ``web.tables.bounded_count``), so the page can say
    "Page N of M".
    """
    query = TaskRun.objects.all()
    if state != "all":
        query = query.filter(
            state__in=NONTERMINAL_STATES if state == "nonterminal" else [state]
        )
    if task_type:
        query = query.filter(task_type=task_type)
    matching, capped = bounded_count(query)
    window, rows, has_next = read_window(
        window, TASK_SORTING.order(query, sort), (matching, capped)
    )
    return {
        "as_of": instant,
        "counts": counts(instant),
        "page": window.page,
        "size": window.size,
        "sort": sort,
        "has_next": has_next,
        "matching": matching,
        "matching_capped": capped,
        "tasks": [task_metadata(row, instant) for row in rows],
    }, len(rows)


def detail(identifier, window, sort, instant):
    """Freeze the event upper version to match the captured current task metadata.

    Returns ``(data, shown)``, or ``(None, 0)`` when no such task exists.
    ``matching`` is a bounded count of that history, for "Page N of M".
    """
    row = TaskRun.objects.filter(pk=identifier).first()
    if row is None:
        return None, 0
    history = row.events.filter(version__lte=row.version)
    matching, capped = bounded_count(history)
    window, events, has_next = read_window(
        window, EVENT_SORTING.order(history, sort), (matching, capped)
    )
    return {
        "as_of": instant,
        "task": task_metadata(row, instant),
        "latest_run_id": str(
            TaskRun.objects.filter(root_id=row.root_id)
            .order_by("-retry_sequence")
            .values_list("pk", flat=True)
            .first()
        ),
        "page": window.page,
        "size": window.size,
        "sort": sort,
        "has_next": has_next,
        "matching": matching,
        "matching_capped": capped,
        "events": [
            {
                "version": event.version,
                "at": event.created_at,
                "action": event.action,
                "state": event.state,
                "attempt": event.attempt,
                "progress": task_progress(event),
            }
            for event in events
        ],
    }, len(events)
