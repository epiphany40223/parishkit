"""What every WARNING-or-above operational log entry must say (#633).

The Administrator's rule: anything logged at WARNING or above carries enough
structured context for System logs to say exactly what went wrong. Each event
that a producer records at those levels names the context keys it must carry
here; ``audit.services.operational`` refuses an entry without them, and the
contract tests check the SQL producers' statements and every Python call site
against this table. The keys are still the closed, reviewed fields of
``audit.schemas``: counts, durations, closed words and ids, never names,
addresses or exception text.

An event missing here may not be recorded at WARNING or above at all; add it,
with the keys its plain-language detail (``log_details``) reads.

A failure-logging call must never itself become a failure. So outside the
test settings (``STEWARDSHIP_LOG_CONTRACT_STRICT``) a contract miss does not
raise: the entry is still written with what it has (the closed schema and the
database's allowlist still check every field it carries), and the process log
records the miss as a WARNING ``log_contract_incomplete`` line naming the
event. The test settings make it raise, so a gap fails a test before release.
"""

import logging

from django.conf import settings

from parishkit.stewardship.observability import Event, FailureKind, emit

# The levels the rule covers.
SERIOUS = frozenset({"WARNING", "ERROR", "CRITICAL"})

_SOURCE_TASK = frozenset({"failure", "task_id", "attempt", "outcome"})
_SOURCE = frozenset({"failure", "outcome"})
_TIMEOUT = frozenset({"what"})

REQUIRED = {
    # Background work that failed: what failed and whether it will retry.
    Event.TASK_FAILED: frozenset({"failure", "outcome"}),
    Event.FACT_DRIFT: frozenset({"task_id", "count"}),
    Event.FAMILY_ENGAGEMENT_FAILED: frozenset({"failure", "failure_kind"}),
    # A display-only step failed and the page showed without it (the Admin
    # menu's open counts, #585): what kind of failure it was.
    Event.REPORT_SHAPING_FAILED: frozenset({"failure_kind"}),
    # ParishSoft refreshes. A task-bound read also says which attempt it was;
    # the source health check and the intake have no task of their own.
    Event.SOURCE_PROVIDER_FAILED: _SOURCE_TASK,
    Event.SOURCE_CREDENTIAL_FAILED: _SOURCE_TASK,
    Event.SOURCE_HELD: _SOURCE_TASK,
    Event.SOURCE_INVALID: _SOURCE,
    Event.SOURCE_TENANT_MISMATCH: _SOURCE,
    Event.SOURCE_DESTRUCTIVE_CHANGE: _SOURCE,
    Event.SOURCE_RETENTION_SKIPPED: frozenset({"failure", "task_id", "count"}),
    Event.SOURCE_MEMBER_UNUSABLE: frozenset({"family_duid", "member_duid", "field"}),
    # Mail.
    Event.MAIL_PROVIDER_FAILED: frozenset({"failure", "task_id", "message_id"}),
    Event.DELIVERY_UNKNOWN: frozenset({"message_id"}),
    # Late work.
    # The scan names what was late; when it cannot, the entry still carries
    # the limit it broke (jobs.due_work_health.FALLBACK_CONTEXT).
    Event.DUE_WORK_LAG: frozenset({"limit_seconds"}),
    Event.BOUNDARY_LAG: frozenset(
        {"occurrence_id", "task_id", "lag_seconds", "limit_seconds"}
    ),
    Event.PRODUCTION_CLEANUP_FAILED: frozenset({"task_id", "count"}),
    # Web stopped answering (jobs.web_health): how it failed, and for how
    # many minutes in a row.
    Event.WEB_UNHEALTHY: frozenset({"failure", "failure_kind", "count"}),
    # An Admin command-line command that failed unexpectedly (#617): what
    # failed, its category and which command.
    Event.ADMIN_COMMAND_FAILED: frozenset({"failure", "failure_kind", "command"}),
    # A backup sealed to a different key (backup_health); its outcome
    # ``changed`` selects the sentence that says what to check.
    Event.CONFIG_MISMATCH: frozenset({"outcome"}),
    # Work stopped by a time limit names the limit (audit.timeouts).
    Event.TASK_TIMED_OUT: _TIMEOUT,
    Event.HELPER_TIMED_OUT: _TIMEOUT,
    Event.WORK_BUDGET_REACHED: _TIMEOUT,
    Event.TASK_LEASE_LOST: _TIMEOUT,
}


def missing(event, level, context):
    """The required keys an entry lacks, or an empty set when it is complete.

    Entries below WARNING have no requirement. A serious entry for an event
    without a row in ``REQUIRED`` lacks everything, so it is refused too.
    """
    if level not in SERIOUS:
        return frozenset()
    required = REQUIRED.get(event)
    if required is None:
        return frozenset({"<unregistered event>"})
    return required - set(context or {})


def enforce(event, level, context):
    """Raise on a contract miss under the test settings, else log it and go on.

    Returns whether the entry was complete. See the module docstring for why
    production degrades rather than refusing a failure record.
    """
    if not missing(event, level, context):
        return True
    if getattr(settings, "STEWARDSHIP_LOG_CONTRACT_STRICT", False):
        raise ValueError("A serious operational entry must say what went wrong.")
    emit(
        event,
        level=logging.WARNING,
        failure_kind=FailureKind.LOG_CONTRACT_INCOMPLETE,
    )
    return False
