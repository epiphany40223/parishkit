"""Hosted files (#346): the public route that serves a file by its token.

Anyone with a file's link can open it: no sign-in, no listing, and the query
string is ignored so mail programs' link rewriting still works. Documents
download (``attachment``) and images show inline, all under a sandbox policy
the security middleware keeps. Any unknown, deleted or missing file gets the
same friendly "no longer available" page. See
docs/specs/stewardship/hosted-files/spec.md.
"""

import re
from urllib.parse import quote

from django.db import DatabaseError
from django.http import FileResponse, HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.web.hosted_file_types import CONTENT_TYPES

from . import hosted_file_storage as storage
from . import hosted_files
from .hosted_file_models import IMAGE_KINDS, HostedFile

TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def _disposition(row):
    """``attachment`` for documents, ``inline`` for images, with a safe name."""
    name = hosted_files.download_name(row.original_name, row.kind)
    fallback = "".join(
        char if 32 <= ord(char) < 127 and char not in '"\\;' else "_" for char in name
    )
    kind = "inline" if row.kind in IMAGE_KINDS else "attachment"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def unavailable():
    """The one page for an unknown, deleted or missing file (HTTP 404)."""
    parish = None
    try:
        from .configuration_models import Parish
        from .runtime_models import SystemConfiguration

        active = SystemConfiguration.objects.values_list(
            "active_configuration_id", flat=True
        ).first()
        parish = (
            Parish.objects.filter(configuration_id=active).first() if active else None
        )
    except DatabaseError:
        parish = None
    response = HttpResponse(
        render_to_string(
            "stewardship/hosted-file-unavailable.html", {"parish": parish}
        ),
        status=404,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


@require_http_methods(["GET", "HEAD"])
def public_file(request, token):
    """Serve one hosted file by its token to anyone; no sign-in, no listing.

    The query string is ignored so mail programs' link rewriting still works.
    The security middleware keeps this response's own sandbox policy.
    """
    try:
        row = (
            HostedFile.objects.filter(token=token).first()
            if TOKEN.fullmatch(token)
            else None
        )
        root = hosted_files.media_root()
    except (DatabaseError, ConfigError):
        row = None
    if row is None:
        return unavailable()
    etag = f'"{row.sha256}"'
    headers = {
        "Content-Type": CONTENT_TYPES[row.kind],
        "Content-Disposition": _disposition(row),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-cache",
        "ETag": etag,
        "Accept-Ranges": "none",
    }
    if etag in request.headers.get("If-None-Match", ""):
        response = HttpResponse(status=304)
    else:
        stream = storage.open_file(root, row.pk, row.size)
        if stream is None:
            return unavailable()
        if request.method == "HEAD":
            stream.close()
            response = HttpResponse()
            headers["Content-Length"] = str(row.size)
        else:
            response = FileResponse(stream)
    for name, value in headers.items():
        response[name] = value
    if response.status_code == 304:
        del response["Content-Type"]
    response.stewardship_own_policy = True
    return response
