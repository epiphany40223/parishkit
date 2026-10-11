"""``pk-stewardship admin files list``: the hosted file library (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The command
reads the Hosted files page's library through the page's own row builder,
so its links, placeholders, uses and Missing flag match the page's.
"""

from dataclasses import replace

import pytest

from parishkit.stewardship.accounts import hosted_file_storage as storage
from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.hosted_file_models import (
    MAX_FILES,
    MAX_LIBRARY_BYTES,
)
from parishkit.stewardship.audit.models import AuditEvent

from .. import hosted_file_samples as samples
from ..content_factory import content
from .automation_builders import paired
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_hosted_files_postgresql import (
    ORIGIN,
    draft,
    media,  # noqa: F401
    pending,
    uploaded,
)

pytestmark = pytest.mark.django_db(transaction=True)

FILE_FIELDS = {
    "id",
    "name",
    "slug",
    "placeholder",
    "image_tag",
    "type",
    "width",
    "height",
    "size",
    "link",
    "missing",
    "uses",
}


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def listing(admin, secret):
    """``files list``."""
    return admin("files", "list", secret=secret)


def test_the_library_as_the_page_lists_it(admin, media):  # noqa: F811
    """Any session reads the library sorted by placeholder name, with each
    file's link, image tag, uses and Missing flag; nothing is recorded."""
    store = admin.service.store
    browser, _, _ = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    code, document, _ = listing(admin, reader)
    assert code == 0, document
    assert document["result"] == {
        "count": 0,
        "total_bytes": 0,
        "max_files": MAX_FILES,
        "max_bytes": MAX_LIBRARY_BYTES,
        "files": [],
    }

    # Uploaded out of placeholder order, so the sort is the command's.
    campaign, _ = draft(store)
    data = samples.pdf()
    guide = uploaded(browser, "Ministry Guide.pdf", data, slug="m-guide")
    picnic = uploaded(browser, "picnic.png", samples.image("PNG"), slug="b-picnic")
    old = uploaded(browser, "old.pdf", samples.pdf(), slug="z-old")
    (media / storage.DIRECTORY / old.pk.hex).unlink()
    pending(
        store,
        content(str(campaign.pk), slot="login_help", html="<p>{{ file.m-guide }}</p>"),
    )
    events = AuditEvent.objects.count()

    code, document, _ = listing(admin, reader)
    assert code == 0, document
    result = document["result"]
    files = result["files"]
    assert [row["slug"] for row in files] == ["b-picnic", "m-guide", "z-old"]
    assert result["count"] == 3
    assert result["total_bytes"] == guide.size + picnic.size + old.size
    assert all(set(row) == FILE_FIELDS for row in files)
    image, document_row, gone = files
    assert image == {
        "id": str(picnic.pk),
        "name": "picnic.png",
        "slug": "b-picnic",
        "placeholder": "{{ file.b-picnic }}",
        "image_tag": '<img src="{{ file.b-picnic }}" alt="" width="40">',
        "type": "png",
        "width": 40,
        "height": 20,
        "size": picnic.size,
        "link": f"{ORIGIN}/files/{picnic.token}",
        "missing": False,
        "uses": [],
    }
    assert document_row["name"] == "Ministry Guide.pdf"
    assert document_row["size"] == len(data) and document_row["type"] == "pdf"
    assert document_row["image_tag"] == ""
    assert document_row["width"] is None and document_row["height"] is None
    assert document_row["uses"] == [
        {
            "source": "pending_change",
            "description": "A change that is still being applied",
        }
    ]
    assert gone["missing"] and not image["missing"] and not document_row["missing"]
    # The page's GET records no event, and neither does the command.
    assert AuditEvent.objects.count() == events


def test_a_reader_who_is_not_an_administrator_is_denied(
    admin,
    media,  # noqa: F811
    monkeypatch,
):
    """Staff cannot read the library, as on the page: denied, nothing listed.

    Pairing needs an Administrator, so the admission is given the session's
    principal holding only Staff; the page's ``CONFIGURE`` check refuses it.
    """
    browser, secret, _ = paired(admin.service)
    uploaded(browser, "a.pdf", samples.pdf())
    real = sessions.authenticated_admin

    def as_staff(*arguments, **options):
        """The real admission, its principal holding only Staff."""
        principal = real(*arguments, **options)
        if principal is None:
            return None
        return replace(principal, roles=frozenset({"staff"}))

    monkeypatch.setattr(sessions, "authenticated_admin", as_staff)
    code, document, _ = listing(admin, secret)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document


def test_an_ended_session_reads_nothing(admin, media):  # noqa: F811
    """A revoked session is exit 5, and the library is not printed."""
    browser, secret, _ = paired(admin.service)
    uploaded(browser, "a.pdf", samples.pdf())
    revoke(admin.service, secret)
    code, document, _ = listing(admin, secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert "result" not in document


def test_no_media_root_is_unavailable(admin, settings):
    """Without the deployment's media root the page cannot tell whether a
    stored copy is missing, so the read is exit 3 (``unavailable``)."""
    settings.STEWARDSHIP_MEDIA_ROOT = None
    _, secret, _ = paired(admin.service)
    code, document, _ = listing(admin, secret)
    assert code == 3 and document["error"]["code"] == "unavailable", document
    assert "result" not in document
