"""Bounded durable lag warnings share the scheduler process health threshold."""

import json
from uuid import uuid5

from django.db import connection

from parishkit.stewardship.audit.log_contract import enforce
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.installer_health import MAX_AGE_SECONDS
from parishkit.stewardship.observability import Event

from .work_locks import require_work_order


def record_lag(occurrence, task, instant):
    """Record the first unhealthy observation per occurrence, surviving restarts.

    The schema validates this identifiers/count-only payload independently. An
    insert-only producer needs no access to historical operational log contents.
    BG-10 owns notification transport/escalation; this is durable warning input.
    """
    require_work_order()
    seconds = (instant - occurrence.due_at).total_seconds()
    if seconds <= MAX_AGE_SECONDS:
        return
    # Which boundary, its task, and how late against the limit (#633). Rows
    # written before #633 used the task schema with the lateness in "count".
    context = {
        "task_type": task.task_type,
        "task_id": task.root_id,
        "occurrence_id": occurrence.pk,
        "lag_seconds": int(seconds),
        "limit_seconds": MAX_AGE_SECONDS,
    }
    enforce(Event.BOUNDARY_LAG, "WARNING", context)
    context = sanitize(ContextKind.DUE_WORK, context)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_operational_log "
            "(id,correlation_id,event,level,schema,context) "
            "VALUES (%s,%s,%s,'WARNING',%s,%s::jsonb) ON CONFLICT DO NOTHING",
            [
                uuid5(occurrence.pk, "boundary-lag-warning"),
                occurrence.pk,
                Event.BOUNDARY_LAG.value,
                ContextKind.DUE_WORK.value,
                json.dumps(context),
            ],
        )
