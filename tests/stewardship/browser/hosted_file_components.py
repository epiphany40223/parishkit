"""Hosted files (#346): the library page and a Family page with a hosted image.

The library is an action table (#879). ``/hosted-files`` lists one file in
use and two unused ones; its dialog posts to the real ``DELETE`` address,
which the fixture server answers as the view does (a redirect back), and its
refresh address shows the picnic image gone. ``BULK`` is the same library
whose refresh address shows both unused files gone, for Delete selected, and
``REFUSED`` one whose post is refused with the server's explanation.
``POSTS`` lists those answers, filled in by ``components``.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.hosted_file_uses import Use
from parishkit.stewardship.accounts.hosted_file_views import LIBRARY_SORTING, UploadForm
from parishkit.stewardship.accounts.hosted_files import placeholder
from parishkit.stewardship.web.tables import paginate

# One small image the component server also serves, as the public route would.
IMAGE_TOKEN = "I" * 43
LIBRARY = "/hosted-files"
BULK = "/hosted-files-bulk"
REFUSED = "/hosted-files-refused"
DELETE = reverse("admin:hosted_file_delete")
# The refusal's own explanation, which the dialog shows.
REFUSAL = "A chosen file is now used by a page, an email or unsent mail."
POSTS = {}


def components(context, admin):
    """The library with one used document and one unused image."""
    moment = datetime(2026, 9, 29, 15, 4, tzinfo=UTC)

    def row(slug, kind, *, uses=(), image=False):
        """One library row shaped as ``hosted_file_views._rows`` builds it."""
        file = SimpleNamespace(
            pk=uuid4(),
            slug=slug,
            original_name=f"{slug}.{'png' if image else 'pdf'}",
            kind=kind,
            size=123456,
            width=800 if image else None,
            height=400 if image else None,
            created_at=moment,
        )
        return {
            "file": file,
            "edit_url": f"/hosted-file-name/{slug}",
            "link": f"/files/{IMAGE_TOKEN}" if image else "/files/" + "D" * 43,
            "placeholder": placeholder(slug),
            "image_tag": (
                f'<img src="{placeholder(slug)}" alt="" width="600">' if image else ""
            ),
            "image": image,
            "type": "PNG image" if image else "PDF",
            "uses": list(uses),
            "uploader": "admin@example.org",
            "missing": False,
        }

    rows = [
        row(
            "ministry-guide",
            "pdf",
            uses=[Use("content", "Sample campaign › Family welcome (page)")],
        ),
        row("parish-picnic", "png", image=True),
        row("parish-map", "pdf"),
    ]
    library = (
        context
        | admin
        | {
            "form": UploadForm(),
            "table": paginate(rows, {}, sorting=LIBRARY_SORTING),
            "uploaded": None,
            "uploaded_placeholder": "",
            "example_link": (
                '<a href="{{ file.ministry-guide }}">Ministry guide (PDF)</a>'
            ),
            "example_image": (
                '<img src="{{ file.parish-picnic }}" alt="Picnic" width="600">'
            ),
            "notice": None,
            "count": 3,
            "total": 370368,
            "max_files": 100,
            "max_bytes": 200 * 1024 * 1024,
        }
    )

    def page(shown, refresh_url, action=DELETE):
        """The library listing ``shown``, redrawn from ``refresh_url``.

        Its usage line counts ``shown``, and its upload notice names the
        picnic image while that is listed, as the view's ``?uploaded=`` does.
        """
        picnic = next(
            (r["file"] for r in shown if r["file"].slug == "parish-picnic"), None
        )
        html = render_to_string(
            "stewardship/hosted-files.html",
            library
            | {
                "table": paginate(shown, {}, sorting=LIBRARY_SORTING),
                "refresh_url": refresh_url,
                "count": len(shown),
                "total": sum(r["file"].size for r in shown),
                "uploaded": picnic,
                "uploaded_placeholder": placeholder("parish-picnic") if picnic else "",
            },
        )
        return "text/html", html.replace(f'action="{DELETE}"', f'action="{action}"')

    guide, _picnic, parish_map = rows
    POSTS.update(
        {
            DELETE: (303, LIBRARY, ""),
            f"{DELETE}?refused": (
                409,
                None,
                json.dumps(
                    {
                        "errors": [{"code": "stale"}],
                        "refusal": {
                            "message": REFUSAL,
                            "fix": "Reload the page to see where each file is used.",
                            "link": None,
                        },
                    }
                ),
            ),
        }
    )
    # The page the Name heading leads to, at the exact query string it
    # carries, for the in-place re-sort tests (#478).
    by_name = "/hosted-files?" + library["table"].heading_query("name")
    return {
        LIBRARY: page(rows, f"{LIBRARY}-one-deleted"),
        f"{LIBRARY}-one-deleted": page([guide, parish_map], f"{LIBRARY}-one-deleted"),
        BULK: page(rows, f"{BULK}-deleted"),
        f"{BULK}-deleted": page([guide], f"{BULK}-deleted"),
        REFUSED: page(rows, REFUSED, action=f"{DELETE}?refused"),
        by_name: (
            "text/html",
            render_to_string(
                "stewardship/hosted-files.html",
                library
                | {
                    "table": paginate(rows, {"sort": "name"}, sorting=LIBRARY_SORTING),
                    "refresh_url": by_name,
                },
            ),
        ),
        "/hosted-file-unavailable": (
            "text/html",
            render_to_string(
                "stewardship/hosted-file-unavailable.html",
                {"parish": SimpleNamespace(name="Sample Parish", website="")},
            ),
        ),
        "/hosted-image-page": (
            "text/html",
            render_to_string(
                "stewardship/denied.html",
                context
                | {
                    "retry_path": "/",
                    "kind": "",
                    "public_content": (
                        f'<p><img src="/files/{IMAGE_TOKEN}" alt="Parish picnic"></p>'
                    ),
                },
            ),
        ),
    }
