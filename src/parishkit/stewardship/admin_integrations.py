"""The Integrations pages' reads (ADM-11 PR 10).

``integration list`` is the Integrations page (``admin:integrations``);
``integration show TARGET`` an integration's settings page as it opens
(``admin:integration_settings``); and ``integration status TARGET`` the
status line that page follows while a key change or a backup folder check
runs (``admin:integration_status``), which ``--watch`` repeats until nothing
is pending. They read through the pages' own request-free helpers in
``accounts.integration_views`` (``integration_list``, ``key_summary``,
``backup_state``), so the pages and the command line cannot drift.

Each admits passively with the pages' ``CONFIGURE`` capability (so
Administrators only, and a read-only session reads), reads in one read
snapshot, rechecks the session before reporting, and records nothing, as the
pages' GETs record nothing. Only the configuration's public, non-secret
settings print, as the page shows them: never a key, a key file or a
fingerprint. Saving, removing, the backup folder check, the refresh schedule
preview, dismissal and every key step are still owed by PR 10.
"""

from dataclasses import dataclass

from .admin_reads import NotAvailable, ReadModel, _admit, _held, _recheck

# ``integration show``'s targets: every settings page but the Backup
# encryption key's, whose proof-of-possession page has its own commands.
SHOW_TARGETS = ("parishsoft", "google_workspace", "email", "slack", "backup")
# ``integration status``'s: the pages with a status line (a key change, or
# the backup folder check), as ``integration_views.integration_status``.
STATUS_TARGETS = ("parishsoft", "google_workspace", "slack", "backup")


@dataclass(frozen=True)
class IntegrationList(ReadModel):
    """The Integrations page: each integration and whether it is set up.

    ``version`` is the applied configuration's digest; ``integrations``
    lists each ``target``, ``name`` (the page's label) and ``configured``,
    in the page's order.
    """

    version: str
    integrations: list


@dataclass(frozen=True)
class IntegrationShow(ReadModel):
    """One integration's settings page, as it opens.

    ``settings`` are the stored public settings (``{}`` for an optional
    integration not set up); ``removable`` is the page's Remove. ``key`` is
    the latest key change line (key targets only), ``source`` the ParishSoft
    page's refresh facts, and ``offsite_backup`` and ``probe`` the
    Off-site backups page's copy status and this Administrator's latest
    Test access; each is None where the page shows none.
    """

    version: str
    target: str
    name: str
    configured: bool
    removable: bool
    settings: dict
    key: dict | None
    source: dict | None
    offsite_backup: dict | None
    probe: dict | None


@dataclass(frozen=True)
class IntegrationStatus(ReadModel):
    """The status line an integration's settings page follows.

    ``pending`` is whether a key change (or the backup folder check) is
    still running; a ``--watch`` stops once it is not.
    """

    target: str
    pending: bool
    key: dict | None
    offsite_backup: dict | None
    probe: dict | None

    @property
    def terminal(self):
        """A watch stops once nothing is pending."""
        return not self.pending


def key_document(summary):
    """The key change line (``CredentialSummary``) as a document, or None."""
    if summary is None:
        return None
    return {
        "state": summary.kind,
        "at": summary.at,
        "message": str(summary.message),
        "request_id": summary.request_id,
    }


def backup_documents(state):
    """``(offsite_backup, probe)`` from ``integration_views.backup_state``."""
    offsite, probe = state["offsite"], state["probe"]
    return (
        {
            "state": offsite.kind,
            "message": str(offsite.message),
            "at": offsite.at,
            "last_copy_at": offsite.last_copy_at,
            "set_name": offsite.set_name,
        },
        None
        if probe is None
        else {
            "state": probe.kind,
            "message": str(probe.message),
            "at": probe.at,
            "folder_url": probe.folder_url,
            "requested_at": probe.requested_at,
            "saved_after": probe.saved_after,
        },
    )


def _read(caller, service, read):
    """Admit, run ``read(configuration, actor)`` in one snapshot, then recheck.

    The configuration is the pages' own ``editable_configuration``, so a
    restore under review or an unfinished setup refuses as the pages do. A
    required integration with no record (Outgoing email addresses or Google
    Workspace mail before setup) is the pages' ``LookupError`` (their 404),
    reported as ``not_available`` only after the session recheck, so an
    ended session is still exit 5, as ``admin_settings._read`` does.
    """
    from .accounts.admin_editing import editable_configuration
    from .accounts.policy import Capability
    from .campaigns.work_locks import read_transaction

    def step():
        """One attempt, repeated by ``_held`` while a change activates."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        missing = False
        with read_transaction():
            configuration = editable_configuration(service)
            try:
                model = read(configuration, actor)
            except LookupError:
                missing = True
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        if missing:
            raise NotAvailable("That integration is not set up.")
        return model

    return _held(step)


def read_integration_list(caller, service):
    """``integration list``: the Integrations page's rows."""
    from .accounts.integration_views import integration_list

    def read(configuration, actor):
        """The page's rows, each label as text."""
        return IntegrationList(
            version=configuration.active_configuration.digest,
            integrations=[
                {
                    "target": row["target"],
                    "name": str(row["label"]),
                    "configured": row["configured"],
                }
                for row in integration_list(configuration)
            ],
        )

    return _read(caller, service, read)


def show_model(configuration, target, *, key, refresh, backup):
    """``integration show``'s document from the page's own parts.

    ``key`` is ``key_summary``'s line, ``refresh`` the ParishSoft page's
    ``full_refresh_status`` and ``backup`` ``backup_state``'s documents, each
    None where the page reads none.
    """
    from .accounts.integration_forms import LABELS
    from .accounts.integration_views import REMOVABLE, _optional
    from .admin_refresh import refresh_fields

    record = _optional(configuration, target)
    offsite, probe = backup or (None, None)
    return IntegrationShow(
        version=configuration.active_configuration.digest,
        target=target,
        name=str(LABELS[target]),
        configured=record is not None,
        removable=record is not None and target in REMOVABLE,
        settings={} if record is None else dict(record["values"]["settings"]),
        key=key_document(key),
        source=None if refresh is None else refresh_fields(refresh),
        offsite_backup=offsite,
        probe=probe,
    )


def read_integration_show(caller, service, target):
    """``integration show TARGET``: the settings page as it opens."""
    from django.utils import timezone

    from .accounts.integration_views import backup_state, key_summary
    from .service_boundaries import ROTATING_TARGETS
    from .source.refresh_status import full_refresh_status, refresh_schedule

    def read(configuration, actor):
        """The page's record, key line, refresh facts and backup status."""
        return show_model(
            configuration,
            target,
            key=(
                key_summary(configuration, target)
                if target in ROTATING_TARGETS
                else None
            ),
            refresh=(
                full_refresh_status(refresh_schedule(configuration), timezone.now())
                if target == "parishsoft"
                else None
            ),
            backup=(
                backup_documents(backup_state(configuration, actor.identity))
                if target == "backup"
                else None
            ),
        )

    return _read(caller, service, read)


def read_integration_status(caller, service, target):
    """``integration status TARGET``: the settings page's status line.

    For the backup, ``pending`` is the page's own: this Administrator's
    folder check is still running.
    """
    from .accounts.integration_views import backup_state, key_summary

    def read(configuration, actor):
        """The status fragment's facts, as ``integration_status`` reads them."""
        if target == "backup":
            state = backup_state(configuration, actor.identity)
            offsite, probe = backup_documents(state)
            return IntegrationStatus(target, state["pending"], None, offsite, probe)
        key = key_summary(configuration, target)
        pending = key is not None and key.kind == "pending"
        return IntegrationStatus(target, pending, key_document(key), None, None)

    return _read(caller, service, read)
