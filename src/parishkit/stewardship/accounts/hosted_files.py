"""Hosted files for parishioners (#346): names, slugs and upload.

Administrators upload PDF, Office and image files that page and email content
links with ``{{ file.<slug> }}``. Each file has a random public token; the
placeholder expands to ``<public origin>/files/<token>``, so renaming a slug
never changes a link. Uploads are validated from their bytes alone
(``web.hosted_file_types``), written durably to private storage, then
recorded; PostgreSQL enforces the library caps. The permission check belongs
to the calling view. See docs/specs/stewardship/hosted-files/spec.md.
"""

import hashlib
import re
import secrets
import unicodedata
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.web.hosted_file_types import EXTENSIONS, detect

from . import hosted_file_storage as storage
from .hosted_file_models import (
    MAX_FILE_BYTES,
    MAX_NAME,
    MAX_SLUG,
    SLUG_PATTERN,
    HostedFile,
)

FILES_PATH = "/files/"
_FORBIDDEN_NAME = re.compile(r'[\x00-\x1f\x7f"*/:<>?\\|]')


class HostedFileError(ValueError):
    """A refusal the Admin can fix, with a closed reason code."""

    def __init__(self, reason, uses=()):
        super().__init__(reason)
        self.reason = reason
        self.uses = tuple(uses)


def media_root():
    """The admitted deployment media root, never a posted or default path."""
    value = getattr(settings, "STEWARDSHIP_MEDIA_ROOT", None)
    if not isinstance(value, Path) or not value.is_absolute():
        raise ConfigError("Hosted file storage is unavailable.")
    return value


def public_origin():
    """This deployment's public origin, or "" before one is configured."""
    return getattr(settings, "STEWARDSHIP_PUBLIC_ORIGIN", "") or ""


def clean_name(value):
    """The stored display name: the base name without unsafe characters."""
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _FORBIDDEN_NAME.sub("", unicodedata.normalize("NFC", name))
    name = " ".join(name.split())[:MAX_NAME].strip()
    return name or "file"


def download_name(original_name, kind):
    """The name a download is saved as: the base name with the type's extension."""
    stem = original_name.rsplit(".", 1)[0] if "." in original_name else original_name
    return (stem.strip() or "file")[: MAX_NAME - 5] + EXTENSIONS[kind]


def valid_slug(value):
    """Whether ``value`` is an allowed placeholder name."""
    return (
        type(value) is str
        and 0 < len(value) <= MAX_SLUG
        and re.fullmatch(SLUG_PATTERN, value) is not None
    )


def derive_slug(original_name, taken):
    """A free slug from a file's base name: ASCII, lowercase, hyphenated.

    ``taken`` is the set of slugs in use; a clash gets ``-2``, ``-3`` and so
    on, still within the length limit.
    """
    stem = original_name.rsplit(".", 1)[0] if "." in original_name else original_name
    ascii_text = (
        unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode().lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if len(slug) > MAX_SLUG:
        cut = slug[:MAX_SLUG]
        slug = cut.rsplit("-", 1)[0] if "-" in cut else cut
    slug = slug.strip("-") or "file"
    candidate, number = slug, 1
    while candidate in taken:
        number += 1
        suffix = f"-{number}"
        candidate = slug[: MAX_SLUG - len(suffix)].rstrip("-") + suffix
    return candidate


def placeholder(slug):
    """The canonical placeholder that content uses for a file."""
    return "{{ file." + slug + " }}"


def link(origin, token):
    """A file's absolute public link."""
    return origin + FILES_PATH + token


def audit(action, actor, row, **extra):
    """Record one upload, rename or deletion in the same transaction."""
    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor.identity,
        subject_id=row.pk,
        context={
            "file_slug": row.slug,
            "file_kind": row.kind,
            "file_size": row.size,
            "file_fingerprint": row.sha256,
            **extra,
        },
    )


def upload(actor, stream, *, name, slug=""):
    """Validate, store and record one upload; return the new row.

    ``stream`` is the uploaded file object. An empty ``slug`` is derived from
    the file name; a typed one is validated, never changed.
    """
    data = stream.read(MAX_FILE_BYTES + 1)
    original_name = clean_name(name)
    if slug and not valid_slug(slug):
        raise HostedFileError("slug_invalid")
    detected = detect(data)
    root = media_root()
    with storage.storage_lock(root):
        storage.sweep(root, HostedFile.objects.values_list("pk", flat=True))
        taken = set(HostedFile.objects.values_list("slug", flat=True))
        if slug and slug in taken:
            raise HostedFileError("slug_taken")
        row = HostedFile(
            id=uuid4(),
            slug=slug or derive_slug(original_name, taken),
            original_name=original_name,
            kind=detected.kind,
            size=len(detected.data),
            sha256=hashlib.sha256(detected.data).hexdigest(),
            width=detected.width,
            height=detected.height,
            token=secrets.token_urlsafe(32),
            uploaded_by_id=actor.identity,
            actor_id=actor.identity,
        )
        storage.write(root, row.pk, detected.data)
        try:
            with transaction.atomic():
                row.save(force_insert=True)
                audit(Action.HOSTED_FILE_UPLOADED, actor, row)
        except IntegrityError as error:
            storage.remove(root, row.pk)
            if "library is full" in str(error):
                raise HostedFileError("full") from None
            if "hosted_file_slug_unique" in str(error):
                raise HostedFileError("slug_taken") from None
            raise
        except BaseException:
            storage.remove(root, row.pk)
            raise
    return row
