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
fingerprint.

``integration schedule-preview`` is the ParishSoft page's live check of its
refresh schedule editor (``admin:refresh_schedule_check``, #632): the
applied schedule, or a proposed one in the stored ``refresh_rules`` shape,
read by the page's own ``ScheduleEditor`` and previewed by the page's own
``_schedule_context``, so it prints the page's problems, seven days, cost
and freshness. It admits and records nothing like the reads above.
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
    integration not set up); ``removable`` is the page's Remove.
    ``refresh_schedule`` is ParishSoft's refresh schedule as its editor
    shows it, in the stored ``refresh_rules`` shape (an existing schedule
    converted to rules, #632), the document ``integration preview
    --refresh-schedule`` takes; None for any other page. ``key`` is
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
    refresh_schedule: dict | None
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


def _read(caller, service, read, missing="That integration is not set up."):
    """Admit, run ``read(configuration, actor)`` in one snapshot, then recheck.

    The configuration is the pages' own ``editable_configuration``, so a
    restore under review or an unfinished setup refuses as the pages do. A
    required integration with no record (Outgoing email addresses or Google
    Workspace mail before setup) is the pages' ``LookupError`` (their 404),
    reported as ``not_available`` only after the session recheck, so an
    ended session is still exit 5, as ``admin_settings._read`` does.
    ``missing`` is that refusal's message (``integration key status`` names
    a key change instead).
    """
    from .accounts.admin_editing import editable_configuration
    from .accounts.policy import Capability
    from .campaigns.work_locks import read_transaction

    def step():
        """One attempt, repeated by ``_held`` while a change activates."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        absent = False
        with read_transaction():
            configuration = editable_configuration(service)
            try:
                model = read(configuration, actor)
            except LookupError:
                absent = True
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        if absent:
            raise NotAvailable(missing)
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


def show_model(configuration, target, *, key, refresh, backup, organization_name=None):
    """``integration show``'s document from the page's own parts.

    ``key`` is ``key_summary``'s line, ``refresh`` the ParishSoft page's
    ``full_refresh_status`` and ``backup`` ``backup_state``'s documents, each
    None where the page reads none. ``organization_name`` is the ParishSoft
    organization's recorded name (#1554), printed beside its ID.
    """
    from .accounts.integration_forms import LABELS
    from .accounts.integration_views import REMOVABLE, _optional
    from .admin_refresh import refresh_fields
    from .source.refresh_rules import converted_rules

    record = _optional(configuration, target)
    settings = {} if record is None else dict(record["values"]["settings"])
    if target == "parishsoft" and "organization_id" in settings:
        # Read-only, beside the ID (#1554): the name the current snapshot's
        # load recorded, or None until a full load records one.
        settings["organization_name"] = organization_name
    offsite, probe = backup or (None, None)
    return IntegrationShow(
        version=configuration.active_configuration.digest,
        target=target,
        name=str(LABELS[target]),
        configured=record is not None,
        removable=record is not None and target in REMOVABLE,
        settings=settings,
        refresh_schedule=(
            converted_rules(settings)
            if target == "parishsoft" and record is not None
            else None
        ),
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
        from .accounts.integration_views import _optional
        from .source.organization_names import current_organization_name

        record = _optional(configuration, target) if target == "parishsoft" else None
        organization = (record or {}).get("values", {}).get("settings", {})
        return show_model(
            configuration,
            target,
            organization_name=(
                current_organization_name(organization["organization_id"])
                if "organization_id" in organization
                else None
            ),
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


@dataclass(frozen=True)
class SchedulePreview(ReadModel):
    """The ParishSoft page's live check of a refresh schedule (#632).

    ``proposed`` is whether ``--refresh-schedule`` gave the schedule (else
    the applied one, as the page shows it); ``changed`` whether it differs
    from the applied one. ``schedule`` is the checked document in the
    stored ``refresh_rules`` shape, with wall times in ``timezone`` (None
    while a row cannot be read). ``problems`` are every problem the page's
    editor finds, at ``refresh_schedule.rules[N]``, ``.skips[N]`` or
    ``refresh_schedule``, with the page's words; ``blocking`` is whether
    they keep a save of this schedule from going through (only a changed
    schedule's do), and ``status`` the line beside Save. ``preview`` is the
    page's seven days, cost and freshness, None when there is nothing to
    preview (a row cannot be read, or no full refresh).
    """

    version: str
    timezone: str
    proposed: bool
    changed: bool
    schedule: dict | None
    problems: list
    blocking: bool
    status: str
    preview: dict | None


def _line(item):
    """One listed time's words as the page shows them, without the browser time.

    ``item`` is one of the shared schedule preview's lines
    (``occurrence_preview.build``): the wall time, what runs, then a struck
    time's reason or a shifted time's daylight-saving words.
    """
    text = f"{item['wall']} {item['text']}"
    if item["struck"]:
        return f"{text} {item['reason']}"
    return f"{text}: {item['shift_text']}" if item["shift_text"] else text


def preview_document(preview):
    """The page's per-day list and summary (``preview_context``) as a document.

    The seven-day grid's quarter-hour cells draw the same days as the list,
    so they are left out; the list's words are the page's. The keys and
    their shape predate the shared schedule preview (#1129) and are kept:
    ``counts`` is a day's summary, and ``due_at`` is None for a struck time.
    """
    if preview is None:
        return None
    return {
        "days": [
            {
                "day": day["date"],
                "weekday": str(day["weekday"]),
                "today": day["today"],
                "change": day["change"],
                "counts": str(day["summary"]),
                "times": [
                    {
                        "text": _line(entry),
                        "struck": entry["struck"],
                        "kind": entry["kind"],
                        "due_at": None if entry["struck"] else entry["at"],
                    }
                    for entry in day["items"]
                ],
            }
            for day in preview["list"]["days"]
        ],
        "line": str(preview["line"]),
        "summary": str(preview["summary"]),
        "warning": preview["warning"],
        "freshness": str(preview["freshness"]),
        "estimated_windows": preview["estimated_windows"],
    }


def schedule_model(configuration, view, *, proposed):
    """``integration schedule-preview``'s document from the page's editor view.

    ``view`` is the ``EditorView`` the page's live check draws
    (``_schedule_context``'s ``schedule``).
    """
    from .admin_integration_changes import editor_problems

    editor = view.editor
    return SchedulePreview(
        version=configuration.active_configuration.digest,
        timezone=view.timezone,
        proposed=proposed,
        changed=editor.changed,
        schedule=editor.document(),
        # Only where and what: the refusal's closed ``code`` belongs to a
        # refusal (5b), and these problems are answered, not refused.
        problems=[
            {"field": problem["field"], "message": problem["message"]}
            for problem in editor_problems(editor)
        ],
        blocking=editor.blocking,
        status=str(view.status),
        preview=preview_document(view.preview),
    )


def read_schedule_preview(caller, service, refresh_schedule=None):
    """``integration schedule-preview``: the page's live schedule check.

    ``refresh_schedule`` is the proposed document's text, or None for the
    applied schedule. It is parsed as ``integration preview
    --refresh-schedule`` parses it (a malformed document, or a quick rule,
    is ``invalid`` without echoing it) and posted as the page
    editor's rows; the page's own problems are reported, never refused, as
    the live check reports them. ParishSoft not set up is ``not_available``.
    """
    from .accounts.integration_views import _schedule_context, _selected
    from .accounts.refresh_schedule_forms import ScheduleEditor
    from .admin_integration_changes import (
        parse_schedule,
        schedule_rows,
    )

    document = None if refresh_schedule is None else parse_schedule(refresh_schedule)

    def read(configuration, actor):
        """Read the page's editor of the applied or proposed schedule."""
        stored = _selected(configuration, "parishsoft")["values"]["settings"]
        editor = ScheduleEditor(stored=stored)
        if document is not None:
            editor = ScheduleEditor(schedule_rows(document, editor), stored=stored)
        view = _schedule_context(configuration, editor)["schedule"]
        return schedule_model(configuration, view, proposed=document is not None)

    return _read(caller, service, read, missing="ParishSoft is not set up.")
