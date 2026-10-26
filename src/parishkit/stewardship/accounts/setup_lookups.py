"""The setup wizard's ParishSoft organization lookup and choice (#1554).

The ParishSoft connection step asks only for the API key. Staging it
(``setup_credentials.stage_credential``) queues one lookup bound to that
key's staged version and fingerprint; the isolated ParishSoft installer,
the only process that can open the key, asks ParishSoft which
organizations the key reaches and whether each holds Families
(``run_pending``); the setup attempt's Administrator then confirms or
picks one (``choose_organization``), which becomes the staged key's
``organization_id`` setting, exactly what the removed organization ID
field set. The other organizations' IDs and names exist only in the open
lookup's list and are scrubbed as soon as one is chosen, the key is
discarded or setup ends; they are never logged or audited. The SQL guard
of frozen migration 0174 admits each move for its one owner.
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
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.contracts import check_version

from .setup_lookup_models import OPEN_STATES, SetupParishSoftLookup
from .setup_policy import SetupState
from .setup_secret_models import SetupSealedCredential

# How long a claimed lookup may run before recovery fails it: the helper's
# own limit (provider_checks.LOOKUP_SECONDS) and its stop margin, with room.
CLAIM_SECONDS = 150


@dataclass(frozen=True)
class LookupStatus:
    """What the ParishSoft connection page shows of the current lookup.

    ``state`` is the lookup's; ``organizations`` (only while ``ready``)
    holds ``Organization`` entries in the page's order; ``error_code`` is a
    failed lookup's stable code. ``identifier`` and ``credential_version``
    bind the page's buttons to this exact lookup and staged key.
    """

    identifier: UUID
    state: str
    error_code: str | None
    organizations: tuple
    credential_version: int

    @property
    def pending(self):
        """Whether the installer has not answered yet (the page polls)."""
        return self.state in {"queued", "running"}

    @property
    def unknown(self):
        """Whether ParishSoft did not say for some listed organization."""
        return any(item.families is None for item in self.organizations)


@dataclass(frozen=True)
class Organization:
    """One organization the key reaches: its ID, name and Families result.

    ``families`` is True (holds Families), False (holds none: shown greyed
    and not selectable) or None (ParishSoft did not say).
    """

    identifier: int
    name: str | None
    families: bool | None

    @property
    def selectable(self):
        """An organization known to hold no Families cannot be chosen."""
        return self.families is not False


def ordered(organizations):
    """The list in the page's order: by name, then by ID (#1554).

    An organization ParishSoft names nothing sorts after the named ones.
    """
    return sorted(
        organizations,
        key=lambda item: (
            item["name"] is None,
            (item["name"] or "").casefold(),
            item["id"],
        ),
    )


def _status(row):
    """The detached page view of one lookup row."""
    return LookupStatus(
        row.pk,
        row.state,
        row.error_code,
        tuple(
            Organization(item["id"], item["name"], item["families"])
            for item in (row.organizations or ())
        ),
        row.credential_version,
    )


def _context(**values):
    """Attribution columns for one lookup write."""
    return dict(
        correlation_id=current_correlation(), version=F("version") + 1, **values
    )


def discard_open(attempt, *, actor_id):
    """Discard the attempt's open lookup, if any, scrubbing its list.

    The caller holds the work order and has locked and admitted
    ``attempt`` for its owner (``actor_id``).
    """
    from .sessions import database_now

    require_work_order()
    SetupParishSoftLookup.objects.filter(attempt=attempt, state__in=OPEN_STATES).update(
        **_context(
            state="discarded",
            organizations=None,
            error_code=None,
            scrubbed_at=database_now(),
            actor_id=actor_id,
        )
    )


def queue_lookup(attempt, credential, *, actor_id):
    """Queue a lookup for the staged ParishSoft key ``credential`` (#1554).

    The caller holds the work order and has just staged (or kept) the key
    for the attempt's owner. Any earlier open lookup is discarded first:
    at most one is open per attempt. So is a parish data check still
    waiting for its answer (#2201): it checked the key or organization that
    this lookup may change.
    """
    from .setup_checks import discard_waiting

    require_work_order()
    discard_open(attempt, actor_id=actor_id)
    discard_waiting(attempt, actor_id=actor_id)
    return SetupParishSoftLookup.objects.create(
        id=uuid4(),
        attempt=attempt,
        credential_id=credential.pk,
        credential_version=credential.version,
        fingerprint=credential.fingerprint,
        actor_id=actor_id,
    )


def current_lookup(attempt_id):
    """The attempt's open lookup as the page shows it, or None.

    The caller has admitted the attempt (a passive read takes no lock).
    """
    row = (
        SetupParishSoftLookup.objects.filter(
            attempt_id=attempt_id, state__in=OPEN_STATES
        )
        .order_by("-created_at")
        .first()
    )
    return None if row is None else _status(row)


def chosen_name(attempt_id, organization_id):
    """The name the attempt's lookup read for its chosen organization, or None.

    Held with the latest chosen lookup until the final load records the
    name in its snapshot (data spec "ParishSoft organization name"); a
    later choice clears the names earlier ones kept.
    """
    return (
        SetupParishSoftLookup.objects.filter(
            attempt_id=attempt_id, state="chosen", chosen_id=organization_id
        )
        .order_by("-created_at")
        .values_list("chosen_name", flat=True)
        .first()
    )


def _admitted(request, service, attempt_id, expected_version, lookup_id):
    """Lock the attempt for its owner and return it with its open lookup.

    Raises ``StaleRecordError`` when the page is out of date: another
    version, or a lookup that is no longer the open one.
    """
    from .sessions import database_now
    from .setup_drafts import _owned
    from .setup_staging import _expiry

    actor, attempt = _owned(request, service, attempt_id)
    check_version(attempt, expected_version)
    if (
        attempt.state != SetupState.COLLECTING
        or _expiry(attempt, database_now()) is not None
    ):
        raise PermissionError("Setup cannot accept this now.")
    row = (
        SetupParishSoftLookup.objects.select_for_update()
        .filter(pk=lookup_id, attempt=attempt, state__in=OPEN_STATES)
        .first()
    )
    if row is None:
        raise StaleRecordError("Reload this setup page.")
    return actor, attempt, row


def _staged_key(attempt, row):
    """The staged ParishSoft key ``row`` looked up, unchanged since, or raise."""
    credential = (
        SetupSealedCredential.objects.defer("ciphertext")
        .filter(pk=row.credential_id, attempt=attempt, scrubbed_at=None)
        .first()
    )
    if (
        credential is None
        or credential.version != row.credential_version
        or credential.fingerprint != row.fingerprint
    ):
        raise StaleRecordError("Reload this setup page.")
    return credential


def _advance(attempt, actor_id):
    """Advance the attempt's version, so open pages of it reload."""
    from .setup_models import SetupAttempt

    changed = SetupAttempt.objects.filter(
        pk=attempt.pk, version=attempt.version
    ).update(
        actor_id=actor_id,
        correlation_id=current_correlation(),
        version=F("version") + 1,
    )
    if changed != 1:
        raise StorageInvariantError("Setup changed during the organization choice.")


def choose_organization(
    request, service, attempt_id, *, lookup_id, organization_id, expected_version
):
    """Use ``organization_id`` from the open lookup's list for the staged key.

    It must be listed and not known to hold no Families; anything else is a
    ``ValueError`` the page shows. The staged key's settings become
    ``{"organization_id": N}``. Choosing the organization the key already
    has (the saved key pasted again) writes nothing to the key, so a load
    bound to it is kept; choosing another discards a finished load first
    (audit reason ``organization_changed``), as the spec's discard rules
    say. The lookup's list is scrubbed and the choice audited (counts
    only) by its SQL guard, and any earlier choice's organization name is
    cleared.
    """
    from .sessions import authenticated_admin, database_now
    from .setup_staging import _status as draft_status

    if type(organization_id) is not int:
        raise ValueError("Choose one of the listed organizations.")
    with work_transaction():
        actor, attempt, row = _admitted(
            request, service, attempt_id, expected_version, lookup_id
        )
        credential = _staged_key(attempt, row)
        listed = {item["id"]: item for item in row.organizations or ()}
        choice = listed.get(organization_id)
        if row.state != "ready" or choice is None or choice["families"] is False:
            raise ValueError("Choose one of the listed organizations.")
        SetupParishSoftLookup.objects.filter(pk=row.pk, version=row.version).update(
            **_context(
                state="chosen",
                chosen_id=organization_id,
                chosen_name=choice["name"],
                organizations=None,
                scrubbed_at=database_now(),
                actor_id=actor.identity,
            )
        )
        # An earlier choice (the key pasted again, or replaced) keeps no
        # name of an organization that may no longer be the one in use.
        SetupParishSoftLookup.objects.filter(attempt=attempt, state="chosen").exclude(
            pk=row.pk
        ).exclude(chosen_name=None).update(
            **_context(chosen_name=None, actor_id=actor.identity)
        )
        settings = {"organization_id": organization_id}
        if dict(credential.settings) != settings:
            SetupSealedCredential.objects.filter(
                pk=credential.pk, version=credential.version
            ).update(
                settings=settings,
                actor_id=actor.identity,
                correlation_id=current_correlation(),
                version=F("version") + 1,
            )
        _advance(attempt, actor.identity)
        authenticated_admin(request, store=service.store, activity=True)
        attempt.refresh_from_db()
        return draft_status(attempt)


def discard_key(request, service, attempt_id, *, lookup_id, expected_version):
    """No or Cancel: discard the open lookup and its staged key (#1554).

    A key with no organization chosen yet is scrubbed, so the page shows
    the key field again, empty, as on a first visit. The saved key pasted
    again keeps its earlier choice: only the lookup goes.
    """
    from .sessions import authenticated_admin, database_now
    from .setup_staging import _status as draft_status

    with work_transaction():
        actor, attempt, row = _admitted(
            request, service, attempt_id, expected_version, lookup_id
        )
        discard_open(attempt, actor_id=actor.identity)
        SetupSealedCredential.objects.filter(
            pk=row.credential_id, scrubbed_at=None, settings={}
        ).update(
            ciphertext=None,
            scrubbed_at=database_now(),
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            version=F("version") + 1,
        )
        _advance(attempt, actor.identity)
        authenticated_admin(request, store=service.store, activity=True)
        attempt.refresh_from_db()
        return draft_status(attempt)


def retry_lookup(request, service, attempt_id, *, lookup_id, expected_version):
    """Check again or Try again: repeat the whole lookup with the staged key.

    Offered for a finished lookup (ready with unknown results, or failed
    because ParishSoft could not be reached); the key is not pasted again.
    """
    from .sessions import authenticated_admin
    from .setup_staging import _status as draft_status

    with work_transaction():
        actor, attempt, row = _admitted(
            request, service, attempt_id, expected_version, lookup_id
        )
        if row.state not in {"ready", "failed"}:
            raise StaleRecordError("Reload this setup page.")
        queue_lookup(attempt, _staged_key(attempt, row), actor_id=actor.identity)
        _advance(attempt, actor.identity)
        authenticated_admin(request, store=service.store, activity=True)
        attempt.refresh_from_db()
        return draft_status(attempt)


# -- The isolated ParishSoft installer's half ----------------------------------


def has_pending():
    """Whether a lookup is queued or running: a lock-free read (#639)."""
    return SetupParishSoftLookup.objects.filter(
        state__in=["queued", "running"]
    ).exists()


def recover_pending():
    """Fail every claimed lookup whose deadline passed, as unavailable.

    Its helper was stopped or its installer restarted; Try again repeats it.
    Returns how many were failed.
    """
    from .sessions import database_now

    require_work_order()
    return SetupParishSoftLookup.objects.filter(
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
    """Claim the oldest queued lookup and return it with the sealed key.

    Commits the claim before any ParishSoft request, so recovery can fail
    it if this installer stops. Returns None when nothing is queued.
    """
    from .credential_database import admit_installer_database
    from .sessions import database_now

    if connection.in_atomic_block:
        raise StorageInvariantError("An organization lookup needs its own commit.")
    admit_installer_database("parishsoft")
    with work_transaction():
        recover_pending()
        row = (
            SetupParishSoftLookup.objects.select_for_update()
            .filter(state="queued")
            .order_by("created_at", "id")
            .first()
        )
        if row is None:
            return None
        credential = (
            SetupSealedCredential.objects.only("id", "ciphertext", "version")
            .filter(
                pk=row.credential_id,
                scrubbed_at=None,
                version=row.credential_version,
                fingerprint=row.fingerprint,
            )
            .first()
        )
        if credential is None:
            # The web discards a lookup whenever it changes the key, so a
            # stale queued lookup is a defect, never a choice to make.
            raise StorageInvariantError("An organization lookup's key changed.")
        now = database_now()
        SetupParishSoftLookup.objects.filter(pk=row.pk, version=row.version).update(
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
    """Whether this installer still owns its claimed lookup, else raise.

    Run while the helper works: the lease check, then the row still
    running for this worker.
    """
    from .credential_database import admit_installer_database

    try:
        check()
        admit_installer_database("parishsoft")
        with work_transaction():
            current = SetupParishSoftLookup.objects.filter(pk=row.pk).first()
            if (
                current is None
                or current.state != "running"
                or current.worker_id != row.worker_id
            ):
                raise PermissionError("Organization lookup ownership has ended.")
    finally:
        connections.close_all()


def outcome(answer):
    """The lookup's result from the helper's answer: (error code, list).

    Exactly one is set. A key whose organizations all hold no Families (one
    or several) fails with ``parishsoft_no_family_organizations``.
    """
    errors = {
        "refused": "parishsoft_key_refused",
        "unavailable": "parishsoft_unavailable",
        "unreadable": "parishsoft_organizations_unreadable",
    }
    if answer["outcome"] in errors:
        return errors[answer["outcome"]], None
    organizations = answer["organizations"]
    if not organizations:
        return "parishsoft_no_organizations", None
    if all(item["families"] is False for item in organizations):
        return "parishsoft_no_family_organizations", None
    return None, ordered(organizations)


def _record_stop(answer):
    """Log a lookup whose Family checks the time limit stopped (#293)."""
    from parishkit.stewardship.audit.timeouts import record_timeout
    from parishkit.stewardship.observability import Event
    from parishkit.stewardship.provider_checks import LOOKUP_SECONDS

    if answer.get("unchecked"):
        record_timeout(
            Event.WORK_BUDGET_REACHED,
            what="provider_check",
            helper="provider_check_worker",
            limit_seconds=LOOKUP_SECONDS,
            elapsed_seconds=answer.get("elapsed"),
            count=answer["unchecked"],
            level="WARNING",
        )


def run_pending(private, *, check, profile, base_url=None):
    """Answer one queued lookup as the isolated ParishSoft installer.

    Claims it, opens the staged key, runs the helper
    (``provider_checks.lookup_organizations``) outside any transaction,
    and records the list or the error code while the claim is still its
    own. Returns whether there was a lookup to answer.
    """
    from parishkit.stewardship.provider_checks import lookup_organizations

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
        # Never sent: a key that cannot be opened is retried with Try again.
        pass
    else:
        connections.close_all()
        answer = lookup_organizations(
            value,
            check=lambda: _check(row, check),
            profile=profile,
            base_url=base_url,
        )
        _record_stop(answer)
    error, organizations = outcome(answer)
    check()
    admit_installer_database("parishsoft")
    with work_transaction():
        SetupParishSoftLookup.objects.filter(
            pk=row.pk, state="running", worker_id=row.worker_id
        ).update(
            **_context(
                state="failed" if error else "ready",
                error_code=error,
                organizations=organizations,
                finished_at=database_now(),
                actor_id=None,
            )
        )
    return True
