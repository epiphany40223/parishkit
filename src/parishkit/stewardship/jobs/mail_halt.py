"""Clear a halted mail sender: what the mail consumers do (ADM-13 PR 4a, #530).

A SYSTEMIC fault halts Family mail in both mail consumers of the
``mail-dispatch`` container (``DeliveryCircuit``). The halt's identity and
kind live in the container's stop marker; this module carries them to the
database and back, always from a consumer's idle loop (``idle``), on a short
connection of its own and never inside a send's transaction:

- ``sync`` records the consumer's current halt (``stewardship_mail_halt``)
  so the web can name it, then reads the clear signal for that halt. When an
  Administrator has cleared it, the circuit lifts the halt
  (``DeliveryCircuit.lift``): the marker goes only if it still names that
  halt, and one probe email per consumer is then sent.
- ``run_mailbox_check`` (the main process only), while a mail consumer in
  the container is halted (as this process knows it, or as a
  ``mail-dispatch`` status record says), claims a waiting mailbox check and
  runs it through the private provider-check helper, which signs in to
  Gmail as the delegated user and sends nothing, then stores the outcome.

Halted consumers send nothing, so they are idle and these run about every
``processes.IDLE_SECONDS``. Every failure is logged and absorbed: the halt
stands until a later idle pass (or a restart) lifts it. Each statement has
its own short SQL limits, and a write they stop is recorded as a timeout.
"""

import json
import logging
from time import monotonic
from uuid import UUID

LOG = logging.getLogger(__name__)

# The mailbox check helper's own time limit, in seconds (the helper allows
# at most 30); the page calls a check unanswered after LIVE_CHECK_SECONDS.
CHECK_SECONDS = 20
LIVE_CHECK_SECONDS = 120
# A halt recorded late (the database was out) is dated at most this many
# seconds back, inside the guard's one-hour window.
OLDEST_HALT = 3540.0
# The SQL limits of each short statement here, in seconds.
STATEMENT_SECONDS = 2
LOCK_SECONDS = 1


def halt_marker(halt_id, kind, at):
    """The stop marker's contents for one halt: its identity, kind and the
    monotonic moment it began (one clock for the whole container)."""
    return json.dumps(
        {"halt": str(halt_id), "kind": kind, "at": at}, sort_keys=True
    ).encode("ascii")


def read_marker(value):
    """The ``(identity, kind, at)`` a stop marker names, or None.

    A marker written by an older release says only ``stopped``, and one
    being written at this moment may still be empty: neither names a halt,
    so the caller keeps the halt it has and reads the marker again later.
    """
    from .mail_halt_models import HALT_KINDS

    try:
        record = json.loads(value.decode("ascii"))
        halt, kind, at = UUID(record["halt"]), record["kind"], float(record["at"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return (halt, kind, at) if kind in HALT_KINDS else None


def _limits(cursor):
    """Give this transaction's statements their short SQL limits."""
    cursor.execute(f"SET LOCAL statement_timeout='{STATEMENT_SECONDS}s'")
    cursor.execute(f"SET LOCAL lock_timeout='{LOCK_SECONDS}s'")


def _failed(error, started, what):
    """Log one failed step: a timeout through the timeout log, else a warning.

    ``what`` names the step in the warning only; the failure's own text is
    never logged (``emit_failure``).
    """
    from parishkit.stewardship.observability import emit_failure
    from parishkit.stewardship.service_status import record_limit

    if record_limit(error, monotonic() - started):
        return
    LOG.warning("The mail halt %s step failed; it is tried again later.", what)
    emit_failure(error, level=logging.WARNING, name_class=True)


def sync(circuit):
    """Record ``circuit``'s halt and lift it once an Administrator cleared it.

    Returns True when the halt was lifted here. A circuit that is not halted,
    or whose halt has no identity yet (the other consumer's marker was still
    being written), does nothing.
    """
    from django.db import connection, transaction

    halt = circuit.current_halt()
    if halt is None:
        return False
    halt_id, kind, at = halt
    started = monotonic()
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            _limits(cursor)
            if circuit.recorded != halt_id:
                # The start is the database clock less the time since the
                # halt began; the guard keeps it within the last hour. Both
                # consumers may record the shared halt: the first row stands.
                cursor.execute(
                    "INSERT INTO public.stewardship_mail_halt (id,began_at,kind) "
                    "VALUES (%s,statement_timestamp()-make_interval(secs=>%s),%s) "
                    "ON CONFLICT (id) DO NOTHING",
                    [halt_id, min(OLDEST_HALT, max(0.0, monotonic() - at)), kind],
                )
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM public.stewardship_mail_halt_clear "
                "WHERE halt_id=%s)",
                [halt_id],
            )
            (cleared,) = cursor.fetchone()
    except Exception as error:
        _failed(error, started, "record")
        return False
    circuit.recorded = halt_id
    return cleared and circuit.lift(halt_id)


def _claim(halted):
    """Claim the oldest live waiting mailbox check; return its id or None.

    Only while a mail consumer in the container is halted: ``halted`` (this
    process's own view), or a ``mail-dispatch`` status record that reported
    Halted within the last three minutes. The record covers a halt the
    second process holds with no marker (its marker write failed, or a split
    halt's marker went with the other halt's clear), which this process
    cannot see locally; the clear's guard reads the same records.
    """
    from django.db import connection, transaction

    with transaction.atomic(), connection.cursor() as cursor:
        _limits(cursor)
        # The guard sets the claim time and refuses a check the page has
        # already called unanswered; the same window is chosen here.
        cursor.execute(
            "UPDATE public.stewardship_mailbox_check SET state='running' "
            "WHERE id=(SELECT id FROM public.stewardship_mailbox_check "
            "WHERE state='waiting' AND created_at>statement_timestamp()"
            "-make_interval(secs=>%s) ORDER BY created_at LIMIT 1) "
            "AND state='waiting' AND (%s OR EXISTS(SELECT 1 FROM "
            "public.stewardship_service_status s WHERE s.service='mail-dispatch' "
            "AND s.sender_state='halted' "
            "AND s.reported_at>statement_timestamp()-interval '3 minutes')) "
            "RETURNING id",
            [LIVE_CHECK_SECONDS - 5, bool(halted)],
        )
        row = cursor.fetchone()
    return None if row is None else row[0]


def _settle(check_id, outcome):
    """Store a running check's outcome (the guard sets the finish time)."""
    from django.db import connection, transaction

    with transaction.atomic(), connection.cursor() as cursor:
        _limits(cursor)
        cursor.execute(
            "UPDATE public.stewardship_mailbox_check SET state=%s "
            "WHERE id=%s AND state='running'",
            [outcome, check_id],
        )


def _workspace_settings():
    """The active configuration's Google Workspace settings for the check.

    The helper's mailbox check signs in as ``delegated_email`` only; its
    request shape also names the sender, reply-to and recipient that the
    delivery check uses, so they carry the same address here and are never
    used.
    """
    from django.db import transaction

    from parishkit.stewardship.accounts.configuration_models import (
        AppliedIntegration,
    )
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    with transaction.atomic():
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        delegated = AppliedIntegration.objects.get(
            configuration_id=configuration_id, kind="google_workspace"
        ).settings["delegated_email"]
    return dict.fromkeys(
        ("delegated_email", "sender", "reply_to", "recipient"), delegated
    )


def check_mailbox(credential_path, profile, *, check):
    """Sign in to the mailbox through the private helper; name the outcome.

    ``passed`` when Google accepted the credential and its delegation,
    ``refused`` when it refused either, ``unavailable`` for an outage, the
    helper's time limit (which the helper logs) or anything the check could
    not explain. Database connections are closed before the helper runs.
    """
    from django.db import connections

    from parishkit.stewardship.accounts.credential_errors import (
        CredentialValidationUnavailable,
    )
    from parishkit.stewardship.accounts.key_files import read_private
    from parishkit.stewardship.observability import emit_failure
    from parishkit.stewardship.provider_checks import check_candidate

    try:
        settings = _workspace_settings()
        value = read_private(credential_path)
        connections.close_all()
        valid = check_candidate(
            "google_workspace",
            settings,
            value,
            seconds=CHECK_SECONDS,
            check=check,
            profile=profile,
        )
    except CredentialValidationUnavailable:
        return "unavailable"
    except Exception as error:
        emit_failure(error, level=logging.WARNING, name_class=True)
        return "unavailable"
    return "passed" if valid else "refused"


def run_mailbox_check(credential_path, profile, *, check, halted):
    """Claim one waiting mailbox check, run it and store its outcome.

    Only while a mail consumer in this container is halted: ``halted``
    (this process's own view, see ``idle``) or a status record that says so
    (``_claim``). A request left from before a restart, or from just before
    a clear, is never run, so it cannot hold up normal sending for the
    check's 20 seconds. It stays waiting until it is no longer live, and
    the page by then says that nothing is halted. Returns the outcome, or
    None when nothing was halted, no check was waiting or the claim failed.
    A claimed check whose outcome cannot be stored stays running; the page
    calls it unanswered after two minutes.
    """
    started = monotonic()
    try:
        check_id = _claim(halted)
    except Exception as error:
        _failed(error, started, "mailbox check claim")
        return None
    if check_id is None:
        return None
    outcome = check_mailbox(credential_path, profile, check=check)
    started = monotonic()
    try:
        _settle(check_id, outcome)
    except Exception as error:
        _failed(error, started, "mailbox check outcome")
    LOG.info("A mailbox check an Administrator asked for: %s.", outcome)
    return outcome


def _halted(circuit):
    """Whether a mail consumer in this container is halted: this one, or
    the other, whose stop marker this idle consumer has not followed yet
    (a consumer follows the marker only when it next admits a message)."""
    if circuit is None:
        return False
    return circuit.stopped or (
        circuit.shared_stop is not None and circuit.shared_stop.exists()
    )


def idle(*, main, credential_path=None, profile=None, check=None):
    """One idle pass of a mail consumer: its halt, then (main) a mailbox check.

    Never raises an ordinary error: the consumer's idle loop must keep
    running. A lease lost during the mailbox check (``check``, the
    process's own lease check) stops the helper and is left to the
    consumer's liveness tick, which stops the process; a helper the system
    cannot drain still ends it (``ProviderCheckDrainFailure``).

    Returns True when this pass lifted the consumer's halt, so the caller
    writes its status record at once rather than up to a minute later.
    """
    from parishkit.stewardship.provider_checks import ProviderCheckOwnershipLost

    from . import family_mail_delivery_tasks

    circuit = family_mail_delivery_tasks.FAMILY_CIRCUIT
    lifted = False
    try:
        if circuit is not None:
            lifted = sync(circuit)
        if main and credential_path is not None:
            run_mailbox_check(
                credential_path,
                profile,
                check=check,
                halted=_halted(circuit),
            )
    except ProviderCheckOwnershipLost:
        LOG.warning("The mailbox check stopped: this process lost its lease.")
    except Exception as error:
        from parishkit.stewardship.observability import emit_failure

        emit_failure(error, level=logging.WARNING, name_class=True)
    return lifted
