"""The setup wizard's quick parish data check (#2201).

Check parish data replaced the setup load, which read the whole ParishSoft
corpus only to unlock Review. The web queues one check bound to the staged
ParishSoft key's version and fingerprint and to its chosen organization
(``start_check``); the isolated ParishSoft installer, the only process that
can open the key, asks ParishSoft for the organization's name and its Family
and Member totals (``run_pending``); the newest ready check of the current
key and organization is what the step, Review and final confirmation require
(``checked_source``). The final load at Finish setup is the only full load.
The SQL guard of frozen migration 0183 admits each move for its one owner.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection, connections
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StorageInvariantError

from .setup_check_models import WAITING_STATES, SetupSourceCheck
from .setup_lookup_models import OPEN_STATES, SetupParishSoftLookup
from .setup_policy import SetupState
from .setup_secret_models import SetupSealedCredential

# How long a claimed check may run before recovery fails it: the helper's
# own limit (provider_checks.LOOKUP_SECONDS) and its stop margin, with room.
CLAIM_SECONDS = 150


@dataclass(frozen=True)
class CheckStatus:
    """What the Check parish data page shows of the attempt's newest check.

    ``state`` is the check's; ``current`` is whether it checked the staged
    key and organization that are saved now, so only a current ready check
    completes the step. ``organization_name``, ``families`` and
    ``members`` are a ready check's answer; ``error_code`` a failed one's
    stable code.
    """

    identifier: UUID
    state: str
    current: bool
    organization_id: int
    organization_name: str | None
    families: int | None
    members: int | None
    error_code: str | None

    @property
    def waiting(self):
        """Whether the installer has not answered yet (the page re-reads)."""
        return self.state in WAITING_STATES

    @property
    def checked(self):
        """Whether this check completes the step: ready and current."""
        return self.state == "ready" and self.current


def _staged_key(attempt):
    """The attempt's unscrubbed staged ParishSoft key (no ciphertext), or None."""
    return (
        SetupSealedCredential.objects.defer("ciphertext")
        .filter(attempt=attempt, target="parishsoft", scrubbed_at=None)
        .first()
    )


def _current(row, credential):
    """Whether ``row`` checked exactly the staged key and organization saved now."""
    return (
        credential is not None
        and credential.pk == row.credential_id
        and credential.version == row.credential_version
        and credential.fingerprint == row.fingerprint
        and credential.settings.get("organization_id") == row.organization_id
    )


def _newest(attempt):
    """The attempt's newest check that was not discarded, or None.

    A discarded check never answered (a new key, or setup ended), so the
    check before it still says what the page shows.
    """
    return (
        SetupSourceCheck.objects.filter(attempt=attempt)
        .exclude(state="discarded")
        .order_by("-created_at", "-id")
        .first()
    )


def current_check(attempt):
    """The attempt's newest check as the page shows it, or None.

    The caller has admitted the attempt (a passive read takes no lock).
    """
    row = _newest(attempt)
    if row is None:
        return None
    return CheckStatus(
        row.pk,
        row.state,
        _current(row, _staged_key(attempt)),
        row.organization_id,
        row.organization_name,
        row.family_count,
        row.member_count,
        row.error_code,
    )


def checked_source(attempt):
    """The check that completes the step, Review's gate and readiness's source.

    The attempt's newest check, when it is ready and checked the staged key
    and organization saved now; otherwise None. The SQL readiness guard
    repeats this predicate.
    """
    row = _newest(attempt)
    if row is None or row.state != "ready" or not _current(row, _staged_key(attempt)):
        return None
    return row


def _context(**values):
    """Attribution columns for one check write."""
    return dict(
        correlation_id=current_correlation(), version=F("version") + 1, **values
    )


def discard_waiting(attempt, *, actor_id):
    """Discard the attempt's check still waiting for its answer, if any.

    Called when a ParishSoft key is pasted (``setup_lookups.queue_lookup``):
    the check was of the key or organization that may change. The caller
    holds the work order and has locked and admitted ``attempt`` for its
    owner (``actor_id``).
    """
    require_work_order()
    SetupSourceCheck.objects.filter(attempt=attempt, state__in=WAITING_STATES).update(
        **_context(state="discarded", actor_id=actor_id)
    )


def start_check(request, service, attempt_id, *, expected_version):
    """Check parish data, or Check again: queue a check of the current key.

    A check still waiting is returned as it is. Otherwise the attempt must be
    collecting with its Parish profile saved and a staged ParishSoft key whose
    organization is chosen, and no organization lookup still open; anything
    else is a ``ValueError`` the page shows. No provider call or credential
    read happens in the web process. The attempt's version advances, so a
    Review marked for the earlier version is needed again. Returns the
    check's page view.
    """
    from parishkit.stewardship.web.contracts import check_version

    from .sessions import authenticated_admin, database_now
    from .setup_drafts import _owned
    from .setup_models import SetupAttempt, SetupDraftSection
    from .setup_staging import _expiry

    with work_transaction():
        actor, attempt = _owned(request, service, attempt_id)
        if _expiry(attempt, database_now()) is not None:
            raise PermissionError("Setup has expired.")
        waiting = SetupSourceCheck.objects.filter(
            attempt=attempt, state__in=WAITING_STATES
        ).exists()
        if waiting:
            return current_check(attempt)
        check_version(attempt, expected_version)
        if attempt.state == SetupState.LOADING:
            # A load started before #2201 is still running.
            raise PermissionError("Available after the parish data load finishes.")
        credential = _staged_key(attempt)
        if (
            attempt.state != SetupState.COLLECTING
            or not SetupDraftSection.objects.filter(
                attempt=attempt, step="parish", scrubbed_at=None
            ).exists()
            or credential is None
            or type(credential.settings.get("organization_id")) is not int
            or SetupParishSoftLookup.objects.filter(
                attempt=attempt, state__in=OPEN_STATES
            ).exists()
        ):
            raise ValueError("Save the parish profile and ParishSoft connection first.")
        SetupSourceCheck.objects.create(
            id=uuid4(),
            attempt=attempt,
            credential_id=credential.pk,
            credential_version=credential.version,
            fingerprint=credential.fingerprint,
            organization_id=credential.settings["organization_id"],
            actor_id=actor.identity,
        )
        changed = SetupAttempt.objects.filter(
            pk=attempt.pk, version=attempt.version
        ).update(
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            version=F("version") + 1,
        )
        if changed != 1:
            raise StorageInvariantError("Setup changed while its check was queued.")
        authenticated_admin(request, store=service.store, activity=True)
        return current_check(attempt)


# -- The isolated ParishSoft installer's half ----------------------------------


def _claimable():
    """Queued checks whose staged key is still the one they checked.

    The web discards a waiting check whenever a key is pasted, so a queued
    check of a changed key is a defect; it is never claimed, so it cannot
    hold up every later check (setup's end discards it).
    """
    return SetupSourceCheck.objects.filter(
        state="queued",
        credential__scrubbed_at=None,
        credential__version=F("credential_version"),
        credential__fingerprint=F("fingerprint"),
    )


def has_pending():
    """Whether a check can be claimed or is running: a lock-free read (#639)."""
    return (
        _claimable().exists()
        or SetupSourceCheck.objects.filter(state="running").exists()
    )


def recover_pending():
    """Fail every claimed check whose deadline passed, as unavailable.

    Its helper was stopped or its installer restarted; Check again repeats
    it. Returns how many were failed.
    """
    from .sessions import database_now

    require_work_order()
    return SetupSourceCheck.objects.filter(
        state="running", deadline_at__lte=database_now()
    ).update(
        **_context(
            state="failed",
            error_code="parishsoft_unavailable",
            finished_at=database_now(),
            actor_id=None,
        )
    )


def _claim(worker_id):
    """Claim the oldest queued check and return it with the sealed key.

    Commits the claim before any ParishSoft request, so recovery can fail
    it if this installer stops. Returns None when nothing is queued.
    """
    from .credential_database import admit_installer_database
    from .sessions import database_now

    if connection.in_atomic_block:
        raise StorageInvariantError("A parish data check needs its own commit.")
    admit_installer_database("parishsoft")
    with work_transaction():
        recover_pending()
        row = (
            _claimable()
            .select_for_update(of=("self",))
            .order_by("created_at", "id")
            .first()
        )
        if row is None:
            return None
        credential = SetupSealedCredential.objects.only(
            "id", "ciphertext", "version"
        ).get(pk=row.credential_id)
        now = database_now()
        SetupSourceCheck.objects.filter(pk=row.pk, version=row.version).update(
            **_context(
                state="running",
                worker_id=worker_id,
                started_at=now,
                deadline_at=now + timedelta(seconds=CLAIM_SECONDS),
                actor_id=None,
            )
        )
        row.refresh_from_db()
        return row, credential.ciphertext


def _check(row, check):
    """Whether this installer still owns its claimed check, else raise.

    Run while the helper works: the lease check, then the row still
    running for this worker.
    """
    from .credential_database import admit_installer_database

    try:
        check()
        admit_installer_database("parishsoft")
        with work_transaction():
            current = SetupSourceCheck.objects.filter(pk=row.pk).first()
            if (
                current is None
                or current.state != "running"
                or current.worker_id != row.worker_id
            ):
                raise PermissionError("Parish data check ownership has ended.")
    finally:
        connections.close_all()


# The error code each failed helper outcome records, as the spec names them.
OUTCOME_ERRORS = {
    "refused": "parishsoft_key_refused",
    "not_reached": "parishsoft_organization_not_reached",
    "unreadable": "parishsoft_check_unreadable",
    "unavailable": "parishsoft_unavailable",
}


def outcome(answer):
    """The check's result from the helper's answer: (error code, values).

    Exactly one is set. ``values`` are the ready row's answer columns. An
    organization ParishSoft holds no Families for fails with
    ``parishsoft_no_families``.
    """
    if answer["outcome"] in OUTCOME_ERRORS:
        return OUTCOME_ERRORS[answer["outcome"]], None
    if answer["families"] == 0:
        return "parishsoft_no_families", None
    return None, {
        "organization_name": answer["name"],
        "family_count": answer["families"],
        "member_count": answer["members"],
    }


def run_pending(private, *, check, profile, base_url=None):
    """Answer one queued check as the isolated ParishSoft installer.

    Claims it, opens the staged key, runs the helper
    (``provider_checks.check_source``) outside any transaction, and records
    the answer or the error code while the claim is still its own. Returns
    whether there was a check to answer.
    """
    from parishkit.stewardship.provider_checks import check_source

    from .credential_database import admit_installer_database
    from .credential_handoff import PrivateHandoff
    from .cryptography import CryptographicError
    from .metrics_credentials import credential_receipt
    from .sessions import database_now

    if (
        not isinstance(private, PrivateHandoff)
        or private.target != "parishsoft"
        or not callable(check)
    ):
        raise TypeError("The isolated ParishSoft target and owner check are required.")
    check()
    admit_installer_database("parishsoft")
    if not has_pending():
        return False
    claimed = _claim(uuid4())
    if claimed is None:
        return False
    row, ciphertext = claimed
    answer = {"outcome": "unavailable"}
    try:
        value = private.open(row.credential_id, ciphertext)
        if credential_receipt(value, "parishsoft") != row.fingerprint:
            raise CryptographicError("The staged key differs.")
    except (CryptographicError, ValueError):
        # Never sent: a key that cannot be opened is retried with Check again.
        pass
    else:
        connections.close_all()
        answer = check_source(
            value,
            organization=row.organization_id,
            check=lambda: _check(row, check),
            profile=profile,
            base_url=base_url,
        )
    error, values = outcome(answer)
    check()
    admit_installer_database("parishsoft")
    with work_transaction():
        SetupSourceCheck.objects.filter(
            pk=row.pk, state="running", worker_id=row.worker_id
        ).update(
            **_context(
                state="failed" if error else "ready",
                error_code=error,
                finished_at=database_now(),
                actor_id=None,
                **(values or {}),
            )
        )
    return True
