"""Hosted files (#346): the Administrator library page.

The page lists, uploads, renames and deletes files; every action needs a
current Administrator and a CSRF token, like content edits (no fresh
sign-in). See docs/specs/stewardship/hosted-files/spec.md.
"""

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
from parishkit.stewardship.web.tables import paginate, table_parameters

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
    table = paginate(_rows(files), selected)
    response = render(
        request,
        "stewardship/hosted-files.html",
        {
            "form": form or UploadForm(),
            "table": table,
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
    """The distinct file IDs a bulk form submitted, validated as UUIDs."""
    values = request.POST.getlist("file_id")
    if not values or len(values) > MAX_FILES or len(set(values)) != len(values):
        raise ValueError("Select at least one file.")
    return [UUID(value) for value in values]


@require_http_methods(["POST"])
def delete(request):
    """Confirm, then delete each selected file on its own; report every result."""
    try:
        actor = principal(request, runtime())
        filters(request.GET, allowed=set())
        if set(request.POST) - {"file_id", "action", "csrfmiddlewaretoken"}:
            raise ValueError("Invalid deletion fields.")
        action = request.POST.get("action")
        if action not in {"preview", "confirm"}:
            raise ValueError("Unknown deletion action.")
        ids = _selected(request)
        if action == "preview":
            files = {row.pk: row for row in HostedFile.objects.filter(pk__in=ids)}
            uses = hosted_file_uses.file_uses(list(files))
            rows = [
                {"id": value, "file": files.get(value), "uses": uses.get(value, [])}
                for value in ids
            ]
            return _no_store(
                render(
                    request,
                    "stewardship/hosted-file-delete.html",
                    {"rows": rows, "confirming": True},
                )
            )
        names = dict(HostedFile.objects.filter(pk__in=ids).values_list("pk", "slug"))
        results = []
        for value in ids:
            try:
                outcome = hosted_file_uses.delete(actor, value)
                results.append(
                    {"slug": names.get(value), "outcome": outcome, "uses": []}
                )
            except hosted_files.HostedFileError as refusal:
                results.append(
                    {
                        "slug": names.get(value),
                        "outcome": "in_use",
                        "uses": refusal.uses,
                    }
                )
            except (ConfigError, DatabaseError):
                # Busy storage or a brief database problem: this file is
                # untouched; report it and carry on with the others.
                results.append(
                    {"slug": names.get(value), "outcome": "retry", "uses": []}
                )
        return _no_store(
            render(
                request,
                "stewardship/hosted-file-delete.html",
                {"results": results, "confirming": False},
            )
        )
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
