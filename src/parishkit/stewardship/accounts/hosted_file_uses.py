"""Where hosted files are used (#346), and the renames and deletions it guards.

A file is in use while current content, a pending configuration change or
unsent email names it (the ``stewardship_hosted_file_use`` view; see
"Current content" in the spec). A slug change or a deletion is refused while
it is, here in plain words first and by the database guard in any case.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import IntegrityError, connection, transaction
from django.utils.translation import gettext as _

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action

from . import hosted_file_storage as storage
from .hosted_file_models import HostedFile
from .hosted_files import HostedFileError, audit, media_root, valid_slug


@dataclass(frozen=True)
class Use:
    """One place a file is used, already described in plain words."""

    source: str
    description: str


def _in_use(error):
    """Whether a database refusal is the in-use guard's."""
    return "Hosted file is in use" in str(error)


def rename(actor, file_id, slug):
    """Change an unused file's slug; the link (token) never changes."""
    if not valid_slug(slug):
        raise HostedFileError("slug_invalid")
    with transaction.atomic():
        row = HostedFile.objects.select_for_update().filter(pk=file_id).first()
        if row is None:
            raise LookupError("The hosted file no longer exists.")
        if row.slug == slug:
            return row
        uses = file_uses([row.pk]).get(row.pk, [])
        if uses:
            raise HostedFileError("in_use", uses)
        if HostedFile.objects.filter(slug=slug).exists():
            raise HostedFileError("slug_taken")
        previous = row.slug
        try:
            with transaction.atomic():
                HostedFile.objects.filter(pk=row.pk, version=row.version).update(
                    slug=slug, version=row.version + 1, actor_id=actor.identity
                )
        except IntegrityError as error:
            if _in_use(error):
                raise HostedFileError(
                    "in_use", file_uses([row.pk]).get(row.pk, [])
                ) from None
            raise HostedFileError("slug_taken") from None
        row.refresh_from_db()
        audit(Action.HOSTED_FILE_SLUG_CHANGED, actor, row, previous_file_slug=previous)
    return row


def delete(actor, file_id):
    """Delete one unused file for real; return "deleted" or "already_deleted".

    A file still in use raises ``HostedFileError("in_use", uses)``. The bytes
    are removed only after the row's deletion commits.
    """
    root = media_root()
    with storage.storage_lock(root):
        with transaction.atomic():
            row = HostedFile.objects.select_for_update().filter(pk=file_id).first()
            if row is None:
                return "already_deleted"
            uses = file_uses([row.pk]).get(row.pk, [])
            if uses:
                raise HostedFileError("in_use", uses)
            try:
                with transaction.atomic():
                    HostedFile.objects.filter(pk=row.pk).delete()
            except IntegrityError as error:
                if not _in_use(error):
                    raise
                raise HostedFileError(
                    "in_use", file_uses([row.pk]).get(row.pk, [])
                ) from None
            audit(Action.HOSTED_FILE_DELETED, actor, row)
        # The row is gone, so the file is deleted whatever happens next: a
        # file left on disk is never served and the next sweep removes it.
        try:
            storage.remove(root, row.pk)
            storage.sweep(root, HostedFile.objects.values_list("pk", flat=True))
        except ConfigError:
            pass
    return "deleted"


def file_uses(file_ids):
    """Every current use of each file, as ``{file_id: [Use, ...]}``."""
    ids = [value for value in file_ids if isinstance(value, UUID)]
    if not ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT file_id, source, campaign_id, kind, slot, count "
            "FROM public.stewardship_hosted_file_use WHERE file_id = ANY(%s) "
            "ORDER BY source, campaign_id, kind, slot",
            [ids],
        )
        rows = cursor.fetchall()
    names = _campaign_names({row[2] for row in rows if row[2] is not None})
    result = {}
    for file_id, source, campaign_id, kind, slot, count in rows:
        result.setdefault(file_id, []).append(
            Use(source, _describe(source, names.get(campaign_id), kind, slot, count))
        )
    return result


def _campaign_names(campaign_ids):
    """Campaign names from the active configuration, by campaign ID."""
    if not campaign_ids:
        return {}
    from .runtime_models import SystemConfiguration

    configuration = (
        SystemConfiguration.objects.select_related("active_configuration")
        .values_list("active_configuration__canonical_document", flat=True)
        .first()
    )
    campaigns = (configuration or {}).get("sections", {}).get("campaigns", [])
    wanted = {str(value) for value in campaign_ids}
    return {
        UUID(row["id"]): row["values"].get("name", "")
        for row in campaigns
        if row["id"] in wanted
    }


def _describe(source, campaign, kind, slot, count):
    """One use in plain words, for the Admin page and refusals."""
    from .content_forms import HISTORY_EMAIL_LABELS, PAGE_LABELS

    if source == "pending_change":
        return _("A change that is still being applied")
    if source == "unsent_mail":
        return _("%(count)s email(s) waiting to be sent") % {"count": count}
    labels = PAGE_LABELS if kind == "page" else HISTORY_EMAIL_LABELS
    place = str(labels.get(slot, slot))
    what = _("page") if kind == "page" else _("email")
    return f"{campaign or _('Campaign')} › {place} ({what})"
