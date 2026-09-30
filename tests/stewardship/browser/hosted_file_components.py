"""Hosted files (#346): the library page and a Family page with a hosted image."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.hosted_file_uses import Use
from parishkit.stewardship.accounts.hosted_file_views import LIBRARY_SORTING, UploadForm
from parishkit.stewardship.accounts.hosted_files import placeholder
from parishkit.stewardship.web.tables import paginate

# One small image the component server also serves, as the public route would.
IMAGE_TOKEN = "I" * 43


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
            "count": 2,
            "total": 246912,
            "max_files": 100,
            "max_bytes": 200 * 1024 * 1024,
        }
    )
    return {
        "/hosted-files": (
            "text/html",
            render_to_string("stewardship/hosted-files.html", library),
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
