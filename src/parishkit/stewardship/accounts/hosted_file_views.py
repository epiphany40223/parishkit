"""Hosted files (#346): the Administrator library page.

The page lists, uploads, renames and deletes files; every action needs a
current Administrator and a CSRF token, like content edits (no fresh
sign-in). Its table is an action table (#879): each unused file's Edit opens
the placeholder-name page and its Delete, like Delete selected, confirms in
the shared dialog, which posts here and redraws the table in place. See
docs/specs/stewardship/hosted-files/spec.md.
"""

from urllib.parse import urlencode
from uuid import UUID

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.hosted_file_types import FileRefused
from parishkit.stewardship.web.refusals import UserFacingStale, unexpected_fields
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from . import hosted_file_storage as storage
from . import hosted_file_uses, hosted_files
from .admin_editing import error_response, principal
from .authentication import runtime
from .hosted_file_models import (
    IMAGE_KINDS,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_LIBRARY_BYTES,
    MAX_SLUG,
    HostedFile,
)
from .limiting import LimiterUnavailable
from .policy_models import PortalUser

# The one request body larger than the 6 MB default: a 10 MB file plus form
# overhead. Caddy admits the same bound on this route only.
MAX_UPLOAD_REQUEST = 11 * 1024 * 1024
# The widest image the email column shows; the copied image tag uses it.
EMAIL_WIDTH = 600
ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    LookupError,
    PermissionError,
    StaleRecordError,
    ValueError,
    signing.BadSignature,
)
ACCEPT = ".pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.gif"
KIND_LABELS = {
    "pdf": gettext_lazy("PDF"),
    "docx": gettext_lazy("Word document"),
    "xlsx": gettext_lazy("Excel workbook"),
    "pptx": gettext_lazy("PowerPoint presentation"),
    "png": gettext_lazy("PNG image"),
    "jpeg": gettext_lazy("JPEG image"),
}
REFUSALS = {
    "empty": gettext_lazy("Choose a file to upload."),
    "too_large": gettext_lazy("Choose a file no larger than 10 MB."),
    "type": gettext_lazy(
        "This file type isn't accepted. Upload a PDF, Word (.docx), Excel "
        "(.xlsx), PowerPoint (.pptx), PNG, JPEG or GIF file."
    ),
    "legacy_office": gettext_lazy(
        "Older Office files (.doc, .xls, .ppt) and password-protected Office "
        "files can't be hosted. Open the file in Office and save it as .docx, "
        ".xlsx or .pptx without a password."
    ),
    "macro": gettext_lazy(
        "Office files with macros, embedded objects or links to outside "
        "templates can't be hosted. Save it as .docx, .xlsx or .pptx without "
        "macros or embedded objects, or save it as a PDF."
    ),
    "animated": gettext_lazy("Animated images can't be hosted; upload a still image."),
    "image": gettext_lazy(
        "This image can't be read, or is larger than 16 million pixels."
    ),
    "slug_invalid": gettext_lazy(
        "Use lowercase letters, digits and single hyphens, up to 64 characters."
    ),
    "slug_taken": gettext_lazy("Another file already uses this placeholder name."),
    "full": gettext_lazy(
        "The library is full (100 files or 200 MB). Delete unused files first."
    ),
}


class UploadForm(forms.Form):
    """The file and an optional placeholder name; everything else is derived."""

    file = forms.FileField(
        label=gettext_lazy("File"),
        max_length=255,
        widget=forms.ClearableFileInput(attrs={"accept": ACCEPT, "data-slug-from": ""}),
        help_text=gettext_lazy(
            "PDF, Word, Excel, PowerPoint, PNG, JPEG or GIF, up to 10 MB."
        ),
    )
    slug = forms.CharField(
        label=gettext_lazy("Placeholder name"),
        max_length=MAX_SLUG,
        required=False,
        widget=forms.TextInput(
            attrs={"data-slug-to": "", "autocomplete": "off", "spellcheck": "false"}
        ),
        help_text=gettext_lazy(
            "Lowercase letters, digits and hyphens. Leave blank to use the file name."
        ),
    )


class RenameForm(forms.Form):
    """A new placeholder name for an unused file."""

    slug = forms.CharField(
        label=gettext_lazy("Placeholder name"),
        max_length=MAX_SLUG,
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
        help_text=gettext_lazy("Lowercase letters, digits and hyphens."),
    )


# Every data column sorts on the server over the whole library (at most
# MAX_FILES rows, read in placeholder order, so equal values keep that
# order). Preview is a thumbnail of the Type column and Actions holds
# controls, so neither is a sort key; Used in sorts by how many uses.
LIBRARY_SORTING = Sorting.by_column(
    {
        "name": lambda row: row["file"].original_name.casefold(),
        "placeholder": lambda row: row["file"].slug,
        "type": lambda row: str(row["type"]).casefold(),
        "size": lambda row: row["file"].size,
        "uploaded": lambda row: row["file"].created_at,
        "uses": lambda row: len(row["uses"]),
    },
    default="placeholder",
    descending_first={"size", "uploaded", "uses"},
)


def _no_store(response):
    """Admin pages carry configuration; browsers keep no copy."""
    response["Cache-Control"] = "no-store"
    return response


def _rows(files):
    """Library rows with their uses, presentation strings and state."""
    origin = hosted_files.public_origin()
    root = hosted_files.media_root()
    uses = hosted_file_uses.file_uses([row.pk for row in files])
    uploaders = dict(
        PortalUser.objects.filter(
            pk__in={row.uploaded_by_id for row in files}
        ).values_list("pk", "email")
    )
    return [
        {
            "file": row,
            "link": hosted_files.link(origin, row.token),
            "placeholder": hosted_files.placeholder(row.slug),
            "image_tag": (
                f'<img src="{hosted_files.placeholder(row.slug)}" alt="" '
                f'width="{min(row.width, EMAIL_WIDTH)}">'
                if row.kind in IMAGE_KINDS
                else ""
            ),
            "image": row.kind in IMAGE_KINDS,
            "type": KIND_LABELS[row.kind],
            "uses": uses.get(row.pk, []),
            "edit_url": reverse("admin:hosted_file_rename", args=[row.pk]),
            "uploader": uploaders.get(row.uploaded_by_id, ""),
            "missing": not storage.exists(root, row.pk, row.size),
        }
        for row in files
    ]


def _library(request, *, form=None, status=200, notice=None):
    """Render the library page with its upload form."""
    selected = filters(request.GET, allowed=table_parameters() | {"uploaded"})
    files = list(HostedFile.objects.order_by("slug"))
    uploaded = None
    if selected.get("uploaded"):
        uploaded = next(
            (row for row in files if str(row.pk) == selected["uploaded"]), None
        )
    table = paginate(_rows(files), selected, sorting=LIBRARY_SORTING)
    # The table region redraws from this address after a deletion: the page
    # as shown (sort, page and size), without the one-off upload notice.
    shown = urlencode([pair for pair in selected.items() if pair[0] != "uploaded"])
    refresh_url = reverse("admin:hosted_files") + (f"?{shown}" if shown else "")
    response = render(
        request,
        "stewardship/hosted-files.html",
        {
            "form": form or UploadForm(),
            "table": table,
            "refresh_url": refresh_url,
            "uploaded": uploaded,
            "uploaded_placeholder": (
                hosted_files.placeholder(uploaded.slug) if uploaded else ""
            ),
            "example_link": '<a href="{{ file.ministry-guide }}">'
            + _("Ministry guide (PDF)")
            + "</a>",
            "example_image": '<img src="{{ file.parish-picnic }}" alt="'
            + _("Families at the parish picnic")
            + '" width="600">',
            "notice": notice,
            "count": len(files),
            "total": sum(row.size for row in files),
            "max_files": MAX_FILES,
            "max_bytes": MAX_LIBRARY_BYTES,
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return _no_store(response)


@require_http_methods(["GET", "HEAD"])
def library(request):
    """List the hosted files, where each is used, and the upload form."""
    try:
        principal(request, runtime())
        return _library(request)
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["POST"])
def upload(request):
    """Store one validated upload, then return to the library."""
    try:
        length = request.META.get("CONTENT_LENGTH") or "0"
        if not length.isdecimal() or int(length) > MAX_UPLOAD_REQUEST:
            raise ValueError("The upload is too large.")
        actor = principal(request, runtime())
        filters(request.GET, allowed=set())
        if (
            set(request.POST) - {"slug", "csrfmiddlewaretoken"}
            or set(request.FILES) - {"file"}
            or any(len(values) != 1 for _, values in request.POST.lists())
            or any(len(values) != 1 for _, values in request.FILES.lists())
        ):
            raise ValueError("Invalid upload fields.")
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            value = form.cleaned_data["file"]
            try:
                if value.size > MAX_FILE_BYTES:
                    raise FileRefused("too_large")
                row = hosted_files.upload(
                    actor,
                    value,
                    name=value.name,
                    slug=form.cleaned_data["slug"].strip(),
                )
            except (FileRefused, hosted_files.HostedFileError) as refusal:
                field = "slug" if refusal.reason.startswith("slug") else "file"
                form.add_error(field, str(REFUSALS[refusal.reason]))
            else:
                return _no_store(
                    HttpResponseRedirect(
                        reverse("admin:hosted_files") + f"?uploaded={row.pk}"
                    )
                )
        return _library(request, form=form, status=400)
    except ERRORS as error:
        return error_response(error)


def _selected(request):
    """The distinct file IDs the dialog posted, validated as UUIDs."""
    values = request.POST.getlist("file_id")
    if not values or len(values) > MAX_FILES or len(set(values)) != len(values):
        raise ValueError("Select at least one file.")
    return [UUID(value) for value in values]


# The dialog's refusals (#879), by what went wrong. They name no file: the
# reader reloads the page, whose Used in column then says where each file is
# used.
DELETE_REFUSALS = {
    "in_use": (
        gettext_lazy(
            "A chosen file is now used by a page, an email or unsent mail, so "
            "nothing was deleted."
        ),
        gettext_lazy("Reload the page to see where each file is used."),
    ),
    "busy": (
        gettext_lazy("The library was busy, so nothing was deleted."),
        gettext_lazy("Try again in a moment."),
    ),
    "partly": (
        gettext_lazy(
            "Some of the chosen files were deleted, but not all: one came into "
            "use or the library was busy."
        ),
        gettext_lazy("Reload the page to see which files remain, then try again."),
    ),
}


def _delete_refusal(reason):
    """A fresh 409 refusal for the dialog, from ``DELETE_REFUSALS``."""
    message, fix = DELETE_REFUSALS[reason]
    return UserFacingStale(message, fix=fix)


@require_http_methods(["POST"])
def delete(request):
    """Delete the files the confirmation dialog names, then return to the library.

    The dialog (admin-portal spec, "Row actions and confirmation") posts the
    chosen ``file_id`` values. The whole request is refused, and nothing is
    deleted, when any chosen file is in use. Otherwise each file is deleted
    on its own (``hosted_file_uses.delete`` locks it and rechecks its uses;
    one already gone counts as deleted). Should one fail after others went
    (it came into use meanwhile, or the storage lock was busy), the answer
    says so, so the reader reloads. Success redirects to the library, which
    the dialog then redraws in place.
    """
    try:
        actor = principal(request, runtime())
        filters(request.GET, allowed=set())
        if request.FILES or set(request.POST) - {"file_id", "csrfmiddlewaretoken"}:
            raise unexpected_fields()
        ids = _selected(request)
        if hosted_file_uses.file_uses(ids):
            raise _delete_refusal("in_use")
        for done, value in enumerate(ids):
            try:
                hosted_file_uses.delete(actor, value)
            except (hosted_files.HostedFileError, ConfigError, DatabaseError) as error:
                # In use since the check, or busy storage or a brief database
                # problem: this file is untouched, and so are the rest.
                if done:
                    reason = "partly"
                elif isinstance(error, hosted_files.HostedFileError):
                    reason = "in_use"
                else:
                    reason = "busy"
                raise _delete_refusal(reason) from None
        return _no_store(HttpResponseRedirect(reverse("admin:hosted_files")))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD", "POST"])
def rename(request, file_id):
    """Change an unused file's placeholder name; its link never changes."""
    try:
        actor = principal(request, runtime())
        filters(request.GET, allowed=set())
        row = HostedFile.objects.filter(pk=file_id).first()
        if row is None:
            raise LookupError("The hosted file no longer exists.")
        uses = hosted_file_uses.file_uses([row.pk]).get(row.pk, [])
        status = 200
        if request.method == "POST":
            if set(request.POST) - {"slug", "csrfmiddlewaretoken"}:
                raise ValueError("Invalid rename fields.")
            form = RenameForm(request.POST)
            if form.is_valid():
                try:
                    hosted_file_uses.rename(
                        actor, row.pk, form.cleaned_data["slug"].strip()
                    )
                except hosted_files.HostedFileError as refusal:
                    uses = list(refusal.uses) or uses
                    if refusal.reason != "in_use":
                        form.add_error("slug", str(REFUSALS[refusal.reason]))
                else:
                    return _no_store(
                        HttpResponseRedirect(reverse("admin:hosted_files"))
                    )
            status = 400
        else:
            form = RenameForm(initial={"slug": row.slug})
        response = render(
            request,
            "stewardship/hosted-file-rename.html",
            {"file": row, "form": form, "uses": uses},
            status=status,
        )
        if status != 200:
            response.stewardship_safe_error = True
        return _no_store(response)
    except ERRORS as error:
        return error_response(error)
