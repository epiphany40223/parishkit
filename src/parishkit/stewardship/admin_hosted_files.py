"""``files list``: the hosted file library of the Admin command line (ADM-11).

The command reads the Hosted files page (``accounts.hosted_file_views``,
#346) through the page's own pieces: its capability (``CONFIGURE``, so
Administrators only), its read of the library in placeholder-name order and
its row builder (``_rows``: each file's link, placeholder, image tag, uses
and Missing flag), so the page and the command line cannot drift.

It admits passively, so a read-only session reads, and records nothing, as
the page's GET records nothing. Hosted files are public by design (the page
warns never to upload anything private), so each file's public link is
printed as the page shows it.

``files delete`` and ``files rename`` are the page's row actions (#947)
through its own ``hosted_file_uses.delete_many`` and ``rename``, admitted as
the page's POST (``CONFIGURE``, recording activity) and so needing a
full-scope session. The page asks for no recent sign-in, so neither is
fresh-gated; ``files delete`` prompts because a deletion cannot be undone.
Each records the page's events and its own ``admin_cmd_files_<verb>`` in the
page's transaction, through the functions' ``record`` callback, only when
something changed.
"""

from dataclasses import dataclass

from .admin_reads import ReadModel, _held, _recheck


@dataclass(frozen=True)
class HostedFileLibrary(ReadModel):
    """The library as the page lists it, and its usage line.

    ``files`` is sorted by placeholder name (``slug``), the page's default
    sort. Each file has ``id``, ``name`` (the uploaded file name), ``slug``,
    ``placeholder`` (``{{ file.slug }}``), ``image_tag`` (the page's
    copyable tag; "" for a document), ``type`` (the stored kind, such as
    ``pdf`` or ``png``), ``width`` and ``height`` (None for a document),
    ``size`` in bytes, ``link`` (the public link), ``missing`` (the stored
    copy is gone from the server) and ``uses`` (``source`` and the page's
    plain-words ``description``, in the page's order). ``count`` and
    ``total_bytes`` are the library's totals; ``max_files`` and
    ``max_bytes`` its limits.
    """

    count: int
    total_bytes: int
    max_files: int
    max_bytes: int
    files: list


def file_model(row):
    """One page row (``hosted_file_views._rows``) as the command prints it."""
    stored = row["file"]
    return {
        "id": stored.pk,
        "name": stored.original_name,
        "slug": stored.slug,
        "placeholder": row["placeholder"],
        "image_tag": row["image_tag"],
        "type": stored.kind,
        "width": stored.width,
        "height": stored.height,
        "size": stored.size,
        "link": row["link"],
        "missing": row["missing"],
        "uses": [
            {"source": use.source, "description": str(use.description)}
            for use in row["uses"]
        ],
    }


def read_library(caller, service):
    """``files list``: the page's library, Administrators only, any session.

    Admits passively with the page's ``CONFIGURE`` capability, reads the
    library and its uses in one read transaction as the page does, and
    rechecks the session before reporting. A configuration refusal (such as
    no media root) is ``unavailable`` through ``_held``.
    """
    from .accounts.hosted_file_models import MAX_FILES, MAX_LIBRARY_BYTES, HostedFile
    from .accounts.hosted_file_views import _rows
    from .accounts.policy import Capability
    from .admin_reads import _admit
    from .campaigns.work_locks import read_transaction

    def step():
        """Admit, read the library in the page's order, recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        with read_transaction():
            files = list(HostedFile.objects.order_by("slug"))
            rows = _rows(files)
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return HostedFileLibrary(
            count=len(files),
            total_bytes=sum(row.size for row in files),
            max_files=MAX_FILES,
            max_bytes=MAX_LIBRARY_BYTES,
            files=[file_model(row) for row in rows],
        )

    return _held(step)


# ------------------------------------------------------------------ changes


@dataclass(frozen=True)
class FileDeletion(ReadModel):
    """``files delete``'s outcome: the IDs deleted now and those already gone.

    An ID with no file left counts as deleted, as on the page, so repeating
    a deletion whose outcome was unknown is safe.
    """

    deleted: list
    already_deleted: list


@dataclass(frozen=True)
class FileRename(ReadModel):
    """``files rename``'s outcome: whether the name changed, and the file.

    ``file`` is the file as ``files list`` prints it; its link never changes.
    """

    changed: bool
    file: dict


class FileRefused(ValueError):
    """A hosted file change the page refuses (``invalid``, exit 1).

    ``fields`` lists each problem as ``{"field", "code", "message"}`` (see
    ``admin_changes.InvalidChange``): ``field`` is a file ID or ``slug``,
    ``code`` is ``invalid`` and the message is in the page's words.
    """

    def __init__(self, fields):
        """Keep the problems the error document lists."""
        super().__init__("The hosted file change is not valid.")
        self.fields = fields


def _in_use(field, uses):
    """One file's in-use refusal, naming each use in the page's words."""
    from django.utils.translation import gettext as _

    places = "; ".join(str(use.description) for use in uses)
    return {
        "field": field,
        "code": "invalid",
        "message": _("This file is in use, so nothing changed: %(places)s.")
        % {"places": places},
    }


def _admit_change(caller, service):
    """The page's POST admission (``CONFIGURE``), recording activity.

    A session that ended is exit 5 (``session_ended``), not the page's
    refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import Capability, allows
    from .accounts.sessions import authenticated_admin

    principal = authenticated_admin(caller, store=service.store, activity=True)
    if principal is None:
        raise SessionUnusable("session_ended")
    if not allows(principal, Capability.CONFIGURE):
        raise PermissionError("Hosted files need the configure capability.")
    return principal


def _recorder(caller, actor, event, written):
    """The ``record`` callback that writes the command's own audit row.

    The page's function calls it inside its transaction, only when it
    changed something; ``written`` then notes that a commit may follow.
    """
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

    A storage or configuration refusal (the library lock busy, no media
    root) is ``unavailable``: the page's transaction rolled back. A
    database failure once the command's row was written may have struck
    the commit, so ``context["committed"]`` is set (exit 6).
    """
    from django.db import DatabaseError

    from parishkit.config import ConfigError

    from .accounts.policy import Capability
    from .admin_reads import Unavailable
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    written = []
    try:
        result = change(actor, written)
    except ConfigError:
        raise Unavailable("The hosted file library is busy or unavailable.") from None
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except PermissionError:
        # A session ended meanwhile is exit 5, not the page's refusal.
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        raise
    context["committed"] = bool(written)
    return result


def preview_deletion(caller, service, file_ids):
    """Read the chosen files before the prompt, refusing any still in use.

    Admits passively with the page's ``CONFIGURE`` capability. Returns the
    files that still exist, in the order given; a file in use is the
    page's rule (Delete is offered only for unused files) and refuses the
    whole deletion before anyone is asked.
    """
    from .accounts.hosted_file_models import HostedFile
    from .accounts.hosted_file_uses import file_uses
    from .accounts.policy import Capability
    from .admin_reads import _admit

    def step():
        """Admit, read the files and their uses, recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        rows = {row.pk: row for row in HostedFile.objects.filter(pk__in=file_ids)}
        uses = file_uses(list(rows))
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return [rows[value] for value in file_ids if value in rows], uses

    found, uses = _held(step)
    refused = [_in_use(str(row.pk), uses[row.pk]) for row in found if uses.get(row.pk)]
    if refused:
        raise FileRefused(refused)
    return found


def delete_files(caller, service, file_ids, *, context):
    """``files delete``: the page's Delete and Delete selected.

    Through the page's ``delete_many``: all the files or none, under the
    storage lock, with the page's ``hosted_file_deleted`` rows and the
    command's ``admin_cmd_files_delete`` in one transaction (none when
    every file was already gone). A file that became used meanwhile is the
    page's refusal, as ``invalid``.
    """
    from django.utils.translation import gettext as _

    from .accounts.hosted_file_uses import delete_many
    from .accounts.hosted_files import HostedFileError
    from .audit.schemas import Action

    def change(actor, written):
        """Delete the files, or refuse them all."""
        try:
            return delete_many(
                actor,
                file_ids,
                record=_recorder(caller, actor, Action.ADMIN_CMD_FILES_DELETE, written),
            )
        except HostedFileError:
            raise FileRefused(
                [
                    {
                        "field": "file_id",
                        "code": "invalid",
                        "message": _(
                            "A chosen file is now used by a page, an email or "
                            "unsent mail, so nothing was deleted."
                        ),
                    }
                ]
            ) from None

    outcomes = _changing(caller, service, context, change)
    return FileDeletion(
        deleted=[value for value in file_ids if outcomes[value] == "deleted"],
        already_deleted=[
            value for value in file_ids if outcomes[value] == "already_deleted"
        ],
    )


def rename_file(caller, service, file_id, slug, *, context):
    """``files rename``: the page's Edit of a file's placeholder name.

    Through the page's ``rename``, which refuses a file in use, an invalid
    or taken name and a missing file. The page's
    ``hosted_file_slug_changed`` and the command's ``admin_cmd_files_rename``
    share one transaction; the current name changes and records nothing.
    """
    from .accounts.hosted_file_models import HostedFile
    from .accounts.hosted_file_uses import rename
    from .accounts.hosted_file_views import REFUSALS, _rows
    from .accounts.hosted_files import HostedFileError
    from .admin_reads import NotAvailable
    from .audit.schemas import Action

    def change(actor, written):
        """Rename the file, mapping the page's refusals to fields."""
        try:
            return rename(
                actor,
                file_id,
                slug,
                record=_recorder(caller, actor, Action.ADMIN_CMD_FILES_RENAME, written),
            )
        except LookupError:
            raise NotAvailable("The hosted file no longer exists.") from None
        except HostedFileError as refusal:
            if refusal.reason == "in_use":
                raise FileRefused([_in_use("file_id", refusal.uses)]) from None
            message = REFUSALS.get(refusal.reason)
            if message is None:
                raise
            raise FileRefused(
                [{"field": "slug", "code": "invalid", "message": str(message)}]
            ) from None

    row = _changing(caller, service, context, change)
    changed = bool(context["committed"])
    # The file as files list prints it, read again after the commit.
    [listed] = _rows([HostedFile.objects.get(pk=row.pk)])
    return FileRename(changed=changed, file=file_model(listed))
