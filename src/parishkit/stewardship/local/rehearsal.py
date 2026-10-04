"""The local bulk-send rehearsal's database steps (BG-12 PR 1).

``tools/stewardship-local.sh rehearse`` measures one scheduled Family send on
a seeded LOCAL deployment, as the BG-12 rehearsal protocol describes
(docs/plans/stewardship/background-processing.md, "Rehearsal protocol"). It
runs two seeder steps:

- ``reminder``, under the web identity in the seed's one-shot container,
  adds one Reminder schedule due at ``--due-at`` through one configuration
  change request that the running configuration installer applies, exactly
  as an Administrator's edit would; the real scheduler, worker and
  mail-dispatch then plan, prepare and send it.
- ``measure``, under the offline migration identity (as the seed's
  invariant ``check`` runs; the web login cannot read the outcome events'
  submission instants and evidence), reads in one READ ONLY snapshot what
  that send left: every Initial or Reminder occurrence due at
  ``--due-at``, its message, the outcome events with their send
  statistics, the correctness counts and the seed's timestamp-ordering
  invariants. It prints one JSON document that the offline report
  (``rehearsal_report.py``) reads beside the lock samples and timing lines
  the operator script collects.

The operator script's own waits read ``PREPARED_SQL`` and ``SETTLED_SQL``
from the image (``image_constant``), so the predicates it polls with are
the ones the PostgreSQL tests exercise.

Neither step changes how anything is sent. Both refuse outside the local
profile.
"""

import json
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from .seeder import (
    MONOTONE_CHECKS,
    SeedRefused,
    admin,
    configure_web_process,
    current_campaign,
    database_now,
    pending_configuration_requests,
    reminder_reference,
    wait_until,
)

# A Reminder added closer than this to its due time could be planned before
# the configuration installer has applied it; the operator script asks for
# a few minutes.
MINIMUM_LEAD = timedelta(minutes=1)
# How long the configuration installer may take to apply the request.
CONFIGURATION_WAIT_SECONDS = 300


def reminder_patch(document, campaign_id, due_at, timezone):
    """The configuration patch that adds one Reminder schedule due at ``due_at``.

    The schedule's date and time are the due instant on the campaign's wall
    clock (``timezone``, its configured zone name); it names the Reminder
    email revision the seed's Reminders use (``seeder.reminder_reference``).
    Returns the patch and the new schedule's id.
    """
    campaign_id = str(campaign_id)
    template, subject = reminder_reference(document, campaign_id)
    local = due_at.astimezone(ZoneInfo(timezone))
    schedule_id = str(uuid4())
    return [
        {
            "operation": "add",
            "section": "schedules",
            "id": schedule_id,
            "values": {
                "campaign_id": campaign_id,
                "kind": "reminder",
                "date": local.date().isoformat(),
                "time": local.time().isoformat(timespec="seconds"),
                "weekday": None,
                "subject": subject,
                "template_version": template,
            },
        }
    ], schedule_id


def reminder_step(request, configuration):
    """Add one Reminder due at ``request.due_at`` and wait until it is applied.

    Runs as the web service runs (``configure_web_process``, LOCAL only),
    signs in the deployment's Administrator through the shared identity
    core and records one configuration change request, as the Admin pages
    would. The running configuration installer applies it; the step waits
    (at most CONFIGURATION_WAIT_SECONDS, logged on expiry) until no request
    recorded since it began is pending, and refuses a due time less than
    MINIMUM_LEAD ahead, which could be planned before it is applied.
    """
    configure_web_process(configuration)
    from parishkit.stewardship.accounts.admin_editing import principal
    from parishkit.stewardship.accounts.configuration_requests import record_request
    from parishkit.stewardship.observability import current_correlation

    from . import seed_web

    now = database_now()
    if request.due_at - now < MINIMUM_LEAD:
        raise SeedRefused("--due-at must be at least a minute in the future.")
    campaign = current_campaign()
    web = admin(request)
    service = seed_web.admin_runtime()
    actor = principal(web, service).identity
    active = service.store.active()
    patch, schedule_id = reminder_patch(
        active.document(),
        campaign.pk,
        request.due_at,
        campaign.active_configuration.timezone,
    )
    since = now
    record_request(
        base_digest=active.digest,
        patch=patch,
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=current_correlation(),
    )
    waited = wait_until(
        "the Reminder's configuration change request to be installed",
        lambda: (
            pending_configuration_requests(since) == 0
            or "a configuration request is still pending"
        ),
        limit=CONFIGURATION_WAIT_SECONDS,
    )
    return {
        "step": "reminder",
        "due_at": request.due_at.isoformat(),
        "schedule_id": schedule_id,
        "campaign_state": campaign.state,
        "configuration_wait_seconds": round(waited, 1),
    }


# The scheduled Family mail occurrences (an Initial invitation or a
# Reminder) due at one instant in the current campaign, with the message
# each produced (its semantic key is the occurrence).
OCCURRENCES_SQL = """
SELECT o.id, o.target, o.state, o.mode, o.created_at,
       m.id, m.state, m.created_at, m.finished_at, d.kind
FROM stewardship_schedule_occurrence o
JOIN stewardship_schedule_definition d ON d.id = o.definition_id
LEFT JOIN stewardship_outbox_message m ON m.semantic_key = o.id
WHERE d.campaign_id = %s AND d.kind IN ('initial', 'reminder') AND o.due_at = %s
ORDER BY o.created_at, o.id
"""

# Every outcome the mail worker recorded for those messages: the events that
# leave "submitting", as the mail send report selects them. An evidence note
# that is not valid JSON yields no statistics rather than failing the read.
OUTCOMES_SQL = """
SELECT e.message_id, e.created_at, e.submitted_at, e.reason, e.state,
       CASE WHEN e.evidence_note LIKE '{%%'
                 AND pg_input_is_valid(e.evidence_note, 'jsonb')
            THEN (e.evidence_note::jsonb)->'stats' END
FROM stewardship_outbox_event e
WHERE e.message_id = ANY(%s)
  AND e.previous_state = 'submitting'
  AND (e.reason LIKE 'smtp%%' OR e.reason = 'recovery_unknown')
ORDER BY e.created_at
"""

# Fulfillments per Family target among those occurrences; more than one is
# a second fulfillment.
FULFILLMENTS_SQL = """
SELECT o.target, count(*)
FROM stewardship_schedule_fulfillment f
JOIN stewardship_schedule_occurrence o ON o.id = f.occurrence_id
WHERE o.id = ANY(%s)
GROUP BY o.target
"""

# Failed or abandoned tasks created since the first occurrence: any is an
# error of the run.
FAILED_TASKS_SQL = """
SELECT task_type, state, count(*)
FROM stewardship_task_run
WHERE created_at >= %s AND state IN ('failed', 'abandoned')
GROUP BY task_type, state
"""


# The operator script's two waits, polled with psql (`-v due=...`, so the
# due instant is quoted by psql as :'due'); see ``bind_due`` for running them
# from Python. A send is prepared once, a minute past its due time, its
# occurrences exist and none is still waiting for its message (a prepared
# occurrence stays pending, with its outbox message set), and no Family mail
# preparation task is queued, running or due for retry.
PREPARED_SQL = """
SELECT clock_timestamp() >= :'due'::timestamptz + interval '1 minute'
  AND EXISTS (SELECT 1 FROM stewardship_schedule_occurrence
      WHERE due_at = :'due'::timestamptz)
  AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_occurrence
      WHERE due_at = :'due'::timestamptz AND state = 'pending' AND outbox_id IS NULL)
  AND NOT EXISTS (SELECT 1 FROM stewardship_task_run
      WHERE task_type = 'family_mail_prepare' AND (state IN ('queued', 'running')
          OR (state = 'retry_wait' AND not_before <= clock_timestamp())))
"""
# A send has settled once it is past its due time, its occurrences exist,
# none is pending or running, and none of their messages is pending,
# submitting or waiting to retry.
SETTLED_SQL = """
SELECT clock_timestamp() >= :'due'::timestamptz
  AND EXISTS (SELECT 1 FROM stewardship_schedule_occurrence
      WHERE due_at = :'due'::timestamptz)
  AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_occurrence
      WHERE due_at = :'due'::timestamptz AND state IN ('pending', 'running'))
  AND NOT EXISTS (SELECT 1 FROM stewardship_outbox_message m
      JOIN stewardship_schedule_occurrence o ON o.id = m.semantic_key
      WHERE o.due_at = :'due'::timestamptz
        AND m.state IN ('pending', 'submitting', 'retry_wait'))
"""


def bind_due(sql):
    """A psql wait predicate with ``:'due'`` as a driver parameter, ``%(due)s``."""
    return sql.replace(":'due'", "%(due)s")


def _iso(value):
    """An ISO instant, or None."""
    return None if value is None else value.isoformat()


def measure(cursor, campaign_id, due_at):
    """Read one send's rows into the JSON document ``measure_step`` prints.

    ``cursor`` is a database cursor; every statement is a plain read. The
    document holds no names, addresses, codes or content: occurrence and
    message ids, Family targets only as counts, states, instants and the
    send statistics, which hold whole numbers and fixed words only.
    """
    cursor.execute(OCCURRENCES_SQL, [campaign_id, due_at])
    rows = cursor.fetchall()
    occurrences, messages, per_target = [], [], {}
    occurrence_ids, message_ids = [], []
    for (
        occurrence_id,
        target,
        state,
        _mode,
        created,
        message_id,
        message_state,
        message_created,
        message_finished,
        _kind,
    ) in rows:
        occurrence_ids.append(occurrence_id)
        occurrences.append(
            {"id": str(occurrence_id), "state": state, "created_at": _iso(created)}
        )
        if message_id is not None:
            message_ids.append(message_id)
            per_target[target] = per_target.get(target, 0) + 1
            messages.append(
                {
                    "id": str(message_id),
                    "occurrence_id": str(occurrence_id),
                    "state": message_state,
                    "created_at": _iso(message_created),
                    "finished_at": _iso(message_finished),
                }
            )
    cursor.execute(OUTCOMES_SQL, [message_ids])
    outcomes = [
        {
            "message_id": str(message_id),
            "settled_at": _iso(settled),
            "submitted_at": _iso(submitted),
            "reason": reason,
            "state": state,
            "stats": stats if isinstance(stats, dict) else _stats(stats),
        }
        for message_id, settled, submitted, reason, state, stats in cursor.fetchall()
    ]
    cursor.execute(FULFILLMENTS_SQL, [occurrence_ids])
    fulfillments = dict(cursor.fetchall())
    # The seed's timestamp-ordering invariants (each a count that must be
    # zero). Its full check also compares counts against the seeded now and
    # the seed's timeline, which no longer hold once a rehearsal has moved
    # the deployment on, so only these apply here.
    invariants = {}
    for query, label in MONOTONE_CHECKS:
        cursor.execute(query)
        invariants[label] = cursor.fetchone()[0]
    failed = []
    if occurrences:
        cursor.execute(FAILED_TASKS_SQL, [min(row[4] for row in rows)])
        failed = [
            {"task_type": task_type, "state": state, "count": count}
            for task_type, state, count in cursor.fetchall()
        ]
    return {
        "step": "measure",
        "due_at": due_at.isoformat(),
        "kinds": sorted({row[9] for row in rows}),
        "modes": sorted({row[3] for row in rows}),
        "targets": len({row[1] for row in rows}),
        "occurrences": occurrences,
        "messages": messages,
        "outcomes": outcomes,
        "targets_with_two_messages": sum(1 for n in per_target.values() if n > 1),
        "targets_with_two_fulfillments": sum(1 for n in fulfillments.values() if n > 1),
        "failed_tasks": failed,
        "invariant_violations": {k: n for k, n in invariants.items() if n},
    }


def _stats(value):
    """Send statistics from a JSON text column, or None."""
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def measure_snapshot(campaign_id, due_at):
    """``measure`` in one read-only snapshot, so its counts agree.

    ``campaign_id`` None reads the current campaign inside that snapshot.
    """
    from django.db import connection, transaction

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if campaign_id is None:
            campaign_id = current_campaign().pk
        return measure(cursor, campaign_id, due_at)


def measure_step(request, configuration):
    """Print what the send due at ``request.due_at`` left (read only).

    Runs under the offline migration identity, as the seed's ``check``
    does: the outcome events' submission instants and evidence are not in
    the web login's grants, and this step must not widen them. The
    snapshot is READ ONLY, so the schema owner's login writes nothing.
    """
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.offline_boundaries import admit_offline_service
    from parishkit.stewardship.operator_commands import configure_operator_database

    if admit_offline_service(configuration) is not ServiceRole.MIGRATION:
        raise SeedRefused("The measure step requires the migration profile.")
    configure_operator_database(configuration)
    return measure_snapshot(None, request.due_at)
