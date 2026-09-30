"""Hosted files for parishioners (#346): one row per uploaded file.

The bytes live on disk under the media root (``hosted_file_storage``); this
row is their receipt and their public identity. Only ``slug`` can change, and
PostgreSQL refuses a slug change or a deletion while current content still
uses the file (``stewardship_hosted_file_uses_v1``). See
docs/specs/stewardship/hosted-files/spec.md.
"""

from django.db import models

from parishkit.stewardship.storage import MutableRecord

# Every type a stored file can have. Uploaded GIFs are re-encoded to PNG.
DOCUMENT_KINDS = ("pdf", "docx", "xlsx", "pptx")
IMAGE_KINDS = ("png", "jpeg")
KINDS = DOCUMENT_KINDS + IMAGE_KINDS
SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
MAX_SLUG = 64
MAX_NAME = 200
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 2048
# Library caps, also enforced by the insert trigger.
MAX_FILES = 100
MAX_LIBRARY_BYTES = 200 * 1024 * 1024


class HostedFile(MutableRecord):
    """A file's receipt: its detected type, digest, size and public token."""

    immutable_fields = MutableRecord.immutable_fields + (
        "original_name",
        "kind",
        "size",
        "sha256",
        "width",
        "height",
        "token",
        "uploaded_by_id",
    )
    slug = models.CharField(max_length=MAX_SLUG)
    original_name = models.CharField(max_length=MAX_NAME)
    kind = models.CharField(max_length=8)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    width = models.PositiveIntegerField(null=True)
    height = models.PositiveIntegerField(null=True)
    token = models.CharField(max_length=43)
    uploaded_by_id = models.UUIDField()

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_hosted_file"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(fields=["slug"], name="hosted_file_slug_unique"),
            models.UniqueConstraint(fields=["token"], name="hosted_file_token_unique"),
            models.CheckConstraint(
                condition=models.Q(slug__regex=SLUG_PATTERN),
                name="hosted_file_slug",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    original_name__regex=r"^[^\x01-\x1f\x7f/\\]{1,200}$"
                ),
                name="hosted_file_name",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=KINDS), name="hosted_file_kind"
            ),
            models.CheckConstraint(
                condition=models.Q(size__gte=1, size__lte=MAX_FILE_BYTES),
                name="hosted_file_size",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="hosted_file_digest",
            ),
            models.CheckConstraint(
                condition=models.Q(token__regex=r"^[A-Za-z0-9_-]{43}$"),
                name="hosted_file_token",
            ),
            # Images carry their pixel size; documents have none.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind__in=IMAGE_KINDS,
                        width__gte=1,
                        width__lte=MAX_IMAGE_SIDE,
                        height__gte=1,
                        height__lte=MAX_IMAGE_SIDE,
                    )
                    | models.Q(
                        kind__in=DOCUMENT_KINDS,
                        width__isnull=True,
                        height__isnull=True,
                    )
                ),
                name="hosted_file_dimensions",
            ),
        ]
