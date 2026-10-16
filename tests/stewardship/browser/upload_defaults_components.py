"""Hosted file Upload and setup content's defaults at their real addresses.

#519 PR 7c: Upload posts its multipart body to the upload route and Fill in
/ Reset post to the content list; each server answers with a
Post/Redirect/Get redirect back to its page, which a route cannot fulfil on
every engine, so the fixture server answers those (``posts``). A refusal is
answered by a route in the test with one of the pages here (the view's 400
re-render). Every page sits at its view's real path, since an in-place
answer from another path is shown by a full load instead.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.hosted_file_models import MAX_LIBRARY_BYTES
from parishkit.stewardship.accounts.hosted_file_views import (
    LIBRARY_SORTING,
    REFUSALS,
    UploadForm,
    _upload_limits,
)
from parishkit.stewardship.accounts.hosted_files import placeholder
from parishkit.stewardship.web.tables import paginate

from .step_up_components import render as step_up_page

LIBRARY = reverse("admin:hosted_files")
UPLOAD = reverse("admin:hosted_file_upload")
# The id the upload's redirect names (``?uploaded=``).
UPLOADED_ID = UUID(int=519)
UPLOADED = f"{LIBRARY}?uploaded={UPLOADED_ID}"
# The same library whose Upload answers as an ended session does (the
# fixture server's /redirect-to-login), and one with 2,000 bytes of room.
ENDED = f"{LIBRARY}?case=ended"
LIMITS = f"{LIBRARY}?case=limits"
# A library full by size, whose redraw after a deletion (its refresh
# address) has room again (#1252).
FULL_BY_SIZE = f"{LIBRARY}?case=full-by-size"
ROOM_AGAIN = f"{LIBRARY}?case=room-again"
# Routed answers to UPLOAD, fetched by GET from these addresses.
REFUSED_TYPE = "/hosted-upload-refused-type"
REFUSED_SIZE = "/hosted-upload-refused-size"
STEP_UP = "/hosted-upload-step-up"


def _row(slug, size=123456):
    """One unused PDF row shaped as ``hosted_file_views._rows`` builds it."""
    file = SimpleNamespace(
        pk=UUID(int=519) if slug == "parish-flyer" else uuid4(),
        slug=slug,
        original_name=f"{slug}.pdf",
        kind="pdf",
        size=size,
        width=None,
        height=None,
    )
    return {
        "file": file,
        "edit_url": f"/hosted-file-name/{slug}",
        "link": "/files/" + "D" * 43,
        "placeholder": placeholder(slug),
        "image_tag": "",
        "image": False,
        "type": "PDF",
        "uses": [],
        "missing": False,
    }


# The library's twenty files, enough that the page scrolls.
ROWS = [_row(f"file-{index:02d}") for index in range(1, 21)]
# The address the library's Name heading leads to, for the re-sort test.
BY_NAME = f"{LIBRARY}?" + paginate(ROWS, {}, sorting=LIBRARY_SORTING).heading_query(
    "name"
)


def _refused(reason):
    """An upload form the view refused for ``reason`` at its file field,
    with the refusal's words and its reason as the code, as the view does."""
    form = UploadForm(
        {"slug": ""},
        {"file": SimpleUploadedFile("guide.pdf", b"<html>", "application/pdf")},
    )
    form.is_valid()
    form.add_error("file", forms.ValidationError(str(REFUSALS[reason]), code=reason))
    return form


def _library(context, admin, rows, *, form=None, uploaded=None, sort=None, **values):
    """The library page listing ``rows`` (sorted by ``sort``), as the view
    renders it. Each row list is long enough that the page scrolls."""
    selected = {"sort": sort} if sort else {}
    form = form or UploadForm()
    html = render_to_string(
        "stewardship/hosted-files.html",
        context
        | {
            "admin_chrome": admin,
            "form": form,
            "library_full": False,
            "table": paginate(rows, selected, sorting=LIBRARY_SORTING),
            "refresh_url": LIBRARY,
            "uploaded": uploaded,
            "uploaded_placeholder": placeholder(uploaded.slug) if uploaded else "",
            "example_link": '<a href="{{ file.guide }}">Guide (PDF)</a>',
            "example_image": '<img src="{{ file.picnic }}" alt="Picnic" width="600">',
            "notice": None,
            "errors": summary_errors(form) if form.is_bound else [],
            "count": len(rows),
            "total": sum(row["file"].size for row in rows),
            "max_files": 100,
            "max_bytes": 200 * 1024 * 1024,
        }
        | values,
    )
    return html


def _sized(context, admin, rows):
    """The library listing ``rows`` with the upload limits the view gives it
    (``_upload_limits``), redrawn from ``ROOM_AGAIN``."""
    form = UploadForm()
    full = _upload_limits(form, [row["file"] for row in rows])
    return _library(
        context, admin, rows, form=form, library_full=full, refresh_url=ROOM_AGAIN
    )


def _limited():
    """An upload form whose file may be at most 2,000 bytes (#563)."""
    form = UploadForm()
    form.fields["file"].widget.attrs |= {
        "data-max-bytes": 2000,
        "data-max-bytes-message": "Choose a file no larger than 2 KB.",
    }
    return form


def components(context, admin):
    """Each page as first shown, the pages the redirects lead to, and the
    refusals a test routes to."""
    rows = ROWS
    flyer = _row("parish-flyer")
    # The file that fills the library to its byte limit.
    archive = _row(
        "parish-archive", MAX_LIBRARY_BYTES - sum(row["file"].size for row in rows)
    )
    pages = {
        LIBRARY: _library(context, admin, rows),
        UPLOADED: _library(context, admin, [flyer, *rows], uploaded=flyer["file"]),
        ENDED: _library(context, admin, rows).replace(
            f'action="{UPLOAD}#', 'action="/redirect-to-login#'
        ),
        LIMITS: _library(context, admin, rows, form=_limited()),
        FULL_BY_SIZE: _sized(context, admin, [archive, *rows]),
        ROOM_AGAIN: _sized(context, admin, rows),
        BY_NAME: _library(context, admin, rows, sort="name"),
        REFUSED_TYPE: _library(context, admin, rows, form=_refused("type")),
        REFUSED_SIZE: _library(context, admin, rows, form=_refused("too_large")),
        STEP_UP: step_up_page("error", context, admin, next=LIBRARY),
    }
    return {path: ("text/html", body) for path, body in pages.items()}


def posts():
    """The fixture server's answers: Upload redirects to the library with its
    notice, Fill in and Reset back to the list with what they did."""
    return {
        UPLOAD: (303, UPLOADED, ""),
    }
