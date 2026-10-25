"""``history``: past-campaign archives on the Admin command line (#1018).

The commands are the Past campaigns page's actions (campaign-history spec,
"Command line"), through the same service, ``archive.imports``, so the page
(slice 4) and the command line cannot drift:

- ``history list`` (any session) reads the imported years and their
  tombstones: their columns, checksums, versions and states. It records
  nothing, as the page's GET records nothing.
- ``history preview -`` (full scope) reads one workbook's bytes from
  standard input, checks it in the isolated helper and stores it as a
  pending preview, as the page's Review does; it prints the Review card's
  figures and ``preview.token``. A refused file is ``invalid`` with its
  located problems as the error's fields (a cell's place and rule, never its
  value). ``--no-family-rows`` is the page's unticked Keep Family rows box.
- ``history confirm --token`` (full scope) applies the preview, as Apply
  does; a replacement asks the page's acknowledgement at the prompt
  (``--yes`` answers it).
- ``history remove <year> --expected-version`` (full scope) removes a
  year's import after the confirmation prompt.

Output holds counts, totals, dates, states and checksums only: never a
Family row, a note's text (``notes_flagged`` names only which notes the
Review card flags) or a pledge band, quartile, pledging-Family count or
average, since command output leaves the Admin portal, where the comparison
report's small-count protection applies. Each change records the page's
events (``campaign_archive_*``) and its own ``admin_cmd_history_<verb>`` in
the same transaction.
"""

from dataclasses import dataclass
from datetime import timedelta

from .admin_reads import NotAvailable, ReadModel, Unavailable, _held, _recheck

# The largest workbook a Review takes (archive.package).
from .archive.package import MAX_FILE_BYTES


def archive_summary(archive):
    """One archive as the command prints it: aggregates and identity only."""
    return {
        "id": archive.pk,
        "year": archive.campaign_year,
        "campaign": archive.campaign_name,
        "final": archive.is_final,
        "state": archive.state,
        "version": archive.version,
        "checksum": archive.checksum,
        "day_1": archive.start_date,
        "last_day": archive.last_submission_date,
        "days": archive.days,
        "families_responded": archive.families_responded,
        "annual_pledged": str(archive.annual_pledged),
        "active_families": archive.active_families,
        "family_rows_kept": archive.family_rows_kept,
        "family_rows": getattr(archive, "family_row_count", None),
        "family_id_systems": list(archive.family_id_systems),
        "notes": len(archive.notes),
        "previewed_at": archive.created_at,
        "imported_at": archive.imported_at,
        "removed_at": archive.removed_at,
    }


@dataclass(frozen=True)
class HistoryList(ReadModel):
    """``history list``: imported years and tombstones, newest year first.

    Each entry is ``archive_summary``'s: year, campaign name, final or
    partial, state (``active`` or ``removed``), version (for ``history
    remove --expected-version``), SHA-256, day 1, last day, days, Families
    responded, annual pledges, active Families, whether Family rows were kept
    and how many, the id systems, how many notes, and when it was previewed,
    imported and removed.
    """

    archives: list


@dataclass(frozen=True)
class HistoryPreview(ReadModel):
    """``history preview``: the Review card and the token that applies it.

    ``action`` is ``add``, ``replace`` (``current`` is the year's active
    import, which Apply removes) or ``already`` (``current`` is the import of
    this same file; Apply changes nothing). ``notes_flagged`` names the
    notes the card flags for a look before Apply; their text stays on the
    page. ``preview.token`` is what ``history confirm`` takes, and
    ``preview.expires_at`` when it stops being accepted.
    """

    action: str
    archive: dict
    current: dict | None
    notes_flagged: list
    preview: dict


@dataclass(frozen=True)
class HistoryConfirm(ReadModel):
    """``history confirm``: ``imported``, ``replaced`` or ``already``.

    ``archive`` is the year's active import (for ``already``, the earlier
    import of the same file); ``replaced`` the tombstone of the import a
    replacement removed, else None.
    """

    outcome: str
    archive: dict
    replaced: dict | None


@dataclass(frozen=True)
class HistoryRemove(ReadModel):
    """``history remove``: the removed import's tombstone."""

    removed: dict


def _capabilities():
    """The page's capabilities: Administrators only (giving is in the files)."""
    from .accounts.policy import Capability

    return (Capability.CONFIGURE, Capability.FINANCIAL_DETAIL)


def _admit_page(caller, store, *, activity):
    """Admit as the page does, with both capabilities; refuse otherwise.

    A change (``activity``) records activity, so it needs a full-scope
    session; a session that ended is ``session_ended`` (exit 5).
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import allows
    from .accounts.sessions import authenticated_admin

    principal = authenticated_admin(caller, store=store, activity=activity)
    if principal is None:
        raise SessionUnusable("session_ended")
    if not all(allows(principal, capability) for capability in _capabilities()):
        raise PermissionError("Past campaigns are the Administrator's.")
    return principal


def _recorder(caller, actor, event, written):
    """The callback that records the command's own event in the change."""
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action

    def record():
        """Record the command's event, attributed to the session."""
        record_action(
            event,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        written.append(True)

    return record


def _changing(caller, service, context, change):
    """Run ``change(actor, written)`` as the page's POST, mapping refusals.

    Another Review in progress is ``unavailable`` (retry in a minute). A
    database failure once the command's row was written may have struck
    the commit, so ``context["committed"]`` is set (exit 6).
    """
    from django.db import DatabaseError

    from .archive.imports import ReviewBusy
    from .observability import _guard_refusal

    actor = _admit_page(caller, service.store, activity=True)
    written = []
    try:
        result = change(actor, written)
    except ReviewBusy:
        raise Unavailable("Another archive is being checked.") from None
    except LookupError:
        raise NotAvailable("No import of that year.") from None
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except PermissionError:
        # A session that ended meanwhile is exit 5, not the page's refusal.
        _recheck(caller, service.store, actor)
        raise
    context["committed"] = bool(written)
    return result


def list_archives(caller, service):
    """``history list``: the imported years, Administrators only, any session."""
    from .archive.imports import archives
    from .campaigns.work_locks import read_transaction

    def step():
        """Admit, read the archives, recheck."""
        actor = _admit_page(caller, service.store, activity=False)
        with read_transaction():
            rows = archives()
        _recheck(caller, service.store, actor)
        return HistoryList(archives=[archive_summary(row) for row in rows])

    return _held(step)


def read_workbook(stream):
    """A workbook's bytes from standard input (after the preamble).

    More than the 20 MB limit is refused before anything else reads it.
    """
    data = stream.read(MAX_FILE_BYTES + 1)
    if not data:
        raise ValueError("No workbook was given on standard input.")
    if len(data) > MAX_FILE_BYTES:
        from .archive.imports import refusal

        raise refusal("file_size", "the file is larger than 20 MB")
    return data


def preview_archive(caller, service, data, *, keep_family_rows, context):
    """``history preview``: the page's Review of one workbook."""
    from .archive.imports import PREVIEW_SECONDS, review
    from .audit.schemas import Action

    def change(actor, written):
        """Check the workbook and store its preview."""
        return review(
            service.store,
            actor.identity,
            data,
            keep_family_rows=keep_family_rows,
            record=_recorder(caller, actor, Action.ADMIN_CMD_HISTORY_PREVIEW, written),
        )

    outcome = _changing(caller, service, context, change)
    archive = outcome.archive
    return HistoryPreview(
        action=outcome.action,
        archive=archive_summary(archive),
        current=archive_summary(outcome.current) if outcome.current else None,
        notes_flagged=list(outcome.flagged_notes),
        preview={
            "token": str(archive.pk),
            "expires_at": archive.created_at + timedelta(seconds=PREVIEW_SECONDS),
        },
    )


def pending_replacement(caller, service, token):
    """The year a preview would replace (with its version), or None.

    Read before the prompt, so the question names exactly what Apply
    removes; Apply checks it again in its own transaction. An unknown,
    applied or someone else's preview is ``stale_version``.
    """
    from .reports.archive_models import CampaignArchive
    from .storage import StaleRecordError

    actor = _admit_page(caller, service.store, activity=False)
    pending = CampaignArchive.objects.filter(
        pk=token, state="pending", actor_id=actor.identity
    ).first()
    if pending is None:
        raise StaleRecordError("The preview expired or was applied.")
    current = (
        CampaignArchive.objects.filter(
            state="active", campaign_year=pending.campaign_year
        )
        .exclude(checksum=pending.checksum)
        .first()
    )
    return pending, current


def confirm_archive(caller, service, token, *, replace_acknowledged, context):
    """``history confirm``: the page's Apply of one preview."""
    from .archive.imports import apply
    from .audit.schemas import Action

    def change(actor, written):
        """Apply the preview, or report it already imported."""
        return apply(
            service.store,
            actor.identity,
            token,
            replace_acknowledged=replace_acknowledged,
            record=_recorder(caller, actor, Action.ADMIN_CMD_HISTORY_CONFIRM, written),
        )

    outcome = _changing(caller, service, context, change)
    return HistoryConfirm(
        outcome=outcome.outcome,
        archive=archive_summary(outcome.archive),
        replaced=archive_summary(outcome.replaced) if outcome.replaced else None,
    )


def remove_archive(caller, service, year, *, expected_version, context):
    """``history remove``: the page's Remove of one year's import."""
    from .archive.imports import remove
    from .audit.schemas import Action

    def change(actor, written):
        """Remove the year's import, leaving its tombstone."""
        return remove(
            service.store,
            actor.identity,
            year,
            expected_version=expected_version,
            record=_recorder(caller, actor, Action.ADMIN_CMD_HISTORY_REMOVE, written),
        )

    return HistoryRemove(
        removed=archive_summary(_changing(caller, service, context, change))
    )
