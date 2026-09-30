"""Hosted files (#346): Admin page, public route, placeholders and in-use guards."""

import json
import re
from html import unescape
from uuid import uuid4

import psycopg
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import hosted_file_storage as storage
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.hosted_file_content import pin_references
from parishkit.stewardship.accounts.hosted_file_models import HostedFile
from parishkit.stewardship.accounts.hosted_file_uses import file_uses
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.page_content import render_pages

from .. import hosted_file_samples as samples
from ..content_factory import content
from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import (
    add_draft,
    bind_operations,
    campaign_clock,
    change,
    policy_operation_id,
)
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import apply, post
from .test_content_views_postgresql import values
from .test_family_mail_dispatch_postgresql import prepare as prepare_mail
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_parish_views_postgresql import token

pytestmark = pytest.mark.django_db(transaction=True)
ORIGIN = "https://parish.example.org"
LIBRARY = "/admin/files/"
UPLOAD = "/admin/files/upload"


@pytest.fixture
def media(tmp_path, settings):
    """Private media and a public origin, as the web runtime configures them."""
    root = tmp_path / "media"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_MEDIA_ROOT = root
    settings.STEWARDSHIP_PUBLIC_ORIGIN = ORIGIN
    return root


def upload(browser, name, data, slug=""):
    """Post one file through the real form, with the real CSRF cookie."""
    return post(
        browser,
        UPLOAD,
        {"file": SimpleUploadedFile(name, data), "slug": slug},
    )


def uploaded(browser, name, data, slug=""):
    """Upload successfully and return the stored row."""
    response = upload(browser, name, data, slug)
    assert response.status_code == 302, response.content
    identifier = response["Location"].rsplit("=", 1)[-1]
    return HostedFile.objects.get(pk=identifier)


def draft(store):
    """A real applied current draft campaign and its content catalog path."""
    result, _, _ = add_draft(store, store.active(), uuid4())
    assert result.state == "applied"
    campaign = Campaign.objects.get()
    return campaign, f"/admin/campaign/{campaign.pk}/content"


def save_page(store, browser, catalog, html, slot="welcome"):
    """Preview and confirm page content through the editor; return the preview."""
    path = f"{catalog}/page/{slot}"
    preview = post(browser, path, values(store, html=html))
    if preview.status_code != 200:
        return preview, None
    accepted = post(browser, path, {"action": "confirm", "preview": token(preview)})
    apply(store, accepted)
    return preview, accepted


def add_content(store, campaign, html):
    """Install content directly through a configuration request."""
    record = content(str(campaign.pk), slot="login_help", html=html, text="x")
    return change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "content", **record}],
    )


def test_upload_list_serve_and_audit_with_web_grants(auth_service, google, media):
    """A PDF uploads as the web role, is listed, served as a download and audited."""
    browser, _ = signed_in()
    data = samples.pdf()
    with task_login(ServiceRole.WEB):
        assert browser.get(LIBRARY).status_code == 200
        row = uploaded(browser, "Ministry Guide.pdf", data)
        page = browser.get(f"{LIBRARY}?uploaded={row.pk}")
    assert (row.slug, row.kind, row.size, row.original_name) == (
        "ministry-guide",
        "pdf",
        len(data),
        "Ministry Guide.pdf",
    )
    text = unescape(page.content.decode())
    assert "Uploaded Ministry Guide.pdf as" in text
    assert "{{ file.ministry-guide }}" in text and "Not used" in text
    assert page["Cache-Control"] == "no-store"
    stored = media / storage.DIRECTORY / row.pk.hex
    assert stored.read_bytes() == data and oct(stored.stat().st_mode)[-3:] == "600"
    event = AuditEvent.objects.get(event_type="hosted_file_uploaded")
    assert event.subject_id == row.pk
    assert AuditContext.objects.get(event=event).context == {
        "file_slug": "ministry-guide",
        "file_kind": "pdf",
        "file_size": len(data),
        "file_fingerprint": row.sha256,
    }
    public = Client()
    response = public.get(f"/files/{row.token}?utm_source=mail")
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == data
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == (
        'attachment; filename="Ministry Guide.pdf"; '
        "filename*=UTF-8''Ministry%20Guide.pdf"
    )
    assert response["Content-Security-Policy"] == (
        "default-src 'none'; sandbox; frame-ancestors 'none'"
    )
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "no-referrer"
    assert response["Cache-Control"] == "no-cache"
    assert response["ETag"] == f'"{row.sha256}"'
    head = public.head(f"/files/{row.token}")
    assert head.status_code == 200 and head["Content-Length"] == str(len(data))
    cached = public.get(f"/files/{row.token}", HTTP_IF_NONE_MATCH=f'"{row.sha256}"')
    assert cached.status_code == 304 and not cached.content


def test_images_are_reencoded_and_served_inline(auth_service, google, media):
    """A JPEG with GPS EXIF is stored without it and shown inline."""
    browser, _ = signed_in()
    data = samples.image("JPEG", (300, 100), exif=samples.gps_exif())
    row = uploaded(browser, "picnic photo.jpeg", data, slug="picnic")
    assert (row.kind, row.width, row.height) == ("jpeg", 100, 300)
    response = Client().get(f"/files/{row.token}")
    body = b"".join(response.streaming_content)
    assert response["Content-Type"] == "image/jpeg"
    assert response["Content-Disposition"].startswith(
        'inline; filename="picnic photo.jpg"'
    )
    assert b"Exif" not in body and body != data
    page = unescape(browser.get(LIBRARY).content.decode())
    assert '<img src="{{ file.picnic }}" alt="" width="100">' in page


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("guide.pdf", samples.HTML_AS_PDF, "file type isn't accepted"),
        ("guide.pdf", samples.SVG, "file type isn't accepted"),
        ("minutes.doc", samples.OLE2, "Older Office files"),
        ("budget.xlsm", samples.office("xlsm"), "with macros"),
        ("letter.docx", samples.office("docm"), "with macros"),
        ("slides.pptm", samples.office("pptm"), "with macros"),
        ("bomb.png", samples.bomb_png(), "16 million pixels"),
        ("spinner.gif", samples.image("GIF", frames=3), "Animated images"),
        ("huge.pdf", samples.pdf(10 * 1024 * 1024 + 1), "no larger than 10 MB"),
    ],
    ids=[
        "html-as-pdf",
        "svg",
        "legacy-office",
        "xlsm",
        "docm-named-docx",
        "pptm",
        "decompression-bomb",
        "animated-gif",
        "oversized",
    ],
)
def test_hostile_uploads_are_refused_and_leave_nothing(
    auth_service, google, media, name, data, message
):
    """Each refusal explains itself; no row, file or audit event remains."""
    browser, _ = signed_in()
    response = upload(browser, name, data)
    assert response.status_code == 400
    assert message in unescape(response.content.decode())
    assert not HostedFile.objects.exists()
    assert not AuditEvent.objects.filter(event_type="hosted_file_uploaded").exists()
    directory = media / storage.DIRECTORY
    assert not directory.exists() or {p.name for p in directory.iterdir()} <= {".lock"}


def test_a_polyglot_upload_is_stored_as_a_clean_image(auth_service, google, media):
    """A GIF with a ZIP appended is re-encoded; the ZIP never reaches storage."""
    browser, _ = signed_in()
    row = uploaded(browser, "innocent.gif", samples.gifar())
    stored = (media / storage.DIRECTORY / row.pk.hex).read_bytes()
    assert row.kind == "png" and b"PK\x03\x04" not in stored


def test_slug_rules_on_upload(auth_service, google, media):
    """A typed slug is validated and never changed; a taken one is refused."""
    browser, _ = signed_in()
    uploaded(browser, "a.pdf", samples.pdf(), slug="guide")
    for slug, message in (
        ("guide", "already uses this placeholder name"),
        ("Bad Slug", "lowercase letters, digits and single hyphens"),
    ):
        response = upload(browser, "b.pdf", samples.pdf(), slug)
        assert response.status_code == 400
        assert message in unescape(response.content.decode())
    assert uploaded(browser, "guide.pdf", samples.pdf()).slug == "guide-2"


def test_unknown_deleted_and_missing_files_share_one_friendly_page(
    auth_service, google, media
):
    """No hint whether a link ever worked; nothing is cached."""
    browser, _ = signed_in()
    row = uploaded(browser, "a.pdf", samples.pdf())
    (media / storage.DIRECTORY / row.pk.hex).unlink()
    public = Client()
    pages = [
        public.get(path)
        for path in (
            f"/files/{row.token}",
            "/files/" + "x" * 43,
            "/files/unavailable",
        )
    ]
    for response in pages:
        assert response.status_code == 404
        assert b"This file is no longer available" in response.content
        assert response["Cache-Control"] == "no-store"
        assert response["Referrer-Policy"] == "no-referrer"
    assert pages[0].content == pages[1].content
    assert "Missing" in browser.get(LIBRARY).content.decode()


def test_files_are_served_while_the_family_portal_is_closed(
    auth_service, google, media
):
    """A hosted file is parish material, not a Family page."""
    from parishkit.stewardship.accounts import family_maintenance

    browser, _ = signed_in()
    row = uploaded(browser, "a.pdf", samples.pdf())
    admin = type("Actor", (), {"identity": PortalUser.objects.get().pk})()
    family_maintenance.set_closed(admin, closed=True)
    family_maintenance._cache.update(at=None)
    try:
        assert Client().get("/").status_code == 503
        assert Client().get(f"/files/{row.token}").status_code == 200
    finally:
        # Reopen and forget the cached state, so no later test in this
        # process sees a closed Family portal (a 503) for its 3 seconds.
        family_maintenance.set_closed(admin, closed=False)
        family_maintenance._cache.update(at=None)
    assert not family_maintenance.current_state().closed


def test_placeholders_render_on_pages_and_guard_rename_and_delete(
    auth_service, google, media
):
    """Content names files at save; while it does, rename and delete are refused."""
    store = auth_service.store
    campaign, catalog = draft(store)
    browser, _ = signed_in()
    guide = uploaded(browser, "guide.pdf", samples.pdf(), slug="guide")
    picnic = uploaded(browser, "picnic.png", samples.image("PNG"), slug="picnic")
    # Unknown slugs, images naming documents, and images without alt text.
    for html, message in (
        ('<p><a href="{{ file.nope }}">x</a></p>', "{{ file.nope }} does not match"),
        ('<p><img src="{{ file.guide }}" alt="x"></p>', "is a document, not an image"),
        ('<p><img src="{{ file.picnic }}"></p>', "Describe each image with alt text"),
    ):
        refused, _ = save_page(store, browser, catalog, html)
        assert refused.status_code == 400
        assert message in unescape(refused.content.decode())
    html = (
        '<p><a href="{{ file.guide }}">Ministry guide (PDF)</a>'
        '<img src="{{ file.picnic }}" alt="The parish picnic"></p>'
    )
    preview, _ = save_page(store, browser, catalog, html)
    # The preview shows the real links, as Families will see them.
    shown = unescape(preview.content.decode())
    assert f'href="{ORIGIN}/files/{guide.token}"' in shown
    assert f'<img src="{ORIGIN}/files/{picnic.token}" alt="The parish picnic">' in shown
    campaign.refresh_from_db()
    pages = render_pages(
        campaign.active_configuration.configuration_id,
        campaign.active_configuration,
        {"welcome"},
        dict.fromkeys(["parish_name"], "Parish"),
    )
    assert f'href="{ORIGIN}/files/{guide.token}"' in pages["welcome"]
    assert f'src="{ORIGIN}/files/{picnic.token}"' in pages["welcome"]
    # The library says where each file is used.
    uses = file_uses([guide.pk, picnic.pk])
    assert [use.description for use in uses[guide.pk]] == [
        f"{campaign.active_configuration.values['name']} › Family welcome (page)"
    ]
    library = unescape(browser.get(LIBRARY).content.decode())
    assert "Family welcome (page)" in library and "In use" in library
    # Deletion and renaming are refused while the content uses the files.
    delete = post(
        browser,
        "/admin/files/delete",
        {"action": "confirm", "file_id": [str(guide.pk), str(picnic.pk)]},
    )
    result = unescape(delete.content.decode())
    assert result.count("Not deleted — in use:") == 2
    assert "Family welcome (page)" in result
    rename = post(browser, f"/admin/files/{guide.pk}/name", {"slug": "handbook"})
    assert rename.status_code == 400 and "This file is in use" in unescape(
        rename.content.decode()
    )
    assert HostedFile.objects.count() == 2
    assert HostedFile.objects.get(pk=guide.pk).slug == "guide"
    # The database refuses too, whatever the caller.
    with (
        pytest.raises(IntegrityError, match="Hosted file is in use"),
        transaction.atomic(),
    ):
        HostedFile.objects.filter(pk=guide.pk).delete()
    with (
        pytest.raises(IntegrityError, match="Hosted file is in use"),
        transaction.atomic(),
    ):
        HostedFile.objects.filter(pk=guide.pk).update(slug="x", version=2)
    # Once the content no longer names them, both go for real.
    save_page(store, browser, catalog, "<p>Welcome</p>")
    assert not file_uses([guide.pk, picnic.pk])
    renamed = post(browser, f"/admin/files/{guide.pk}/name", {"slug": "handbook"})
    assert renamed.status_code == 302
    guide.refresh_from_db()
    assert guide.slug == "handbook" and guide.version == 2
    changed = AuditEvent.objects.get(event_type="hosted_file_slug_changed")
    assert AuditContext.objects.get(event=changed).context["previous_file_slug"] == (
        "guide"
    )
    preview = post(
        browser,
        "/admin/files/delete",
        {"action": "preview", "file_id": [str(guide.pk), str(picnic.pk)]},
    )
    assert "This cannot be undone" in unescape(preview.content.decode())
    deleted = post(
        browser,
        "/admin/files/delete",
        {"action": "confirm", "file_id": [str(guide.pk), str(picnic.pk)]},
    )
    assert unescape(deleted.content.decode()).count("Deleted") == 2
    assert not HostedFile.objects.exists()
    assert not (media / storage.DIRECTORY / guide.pk.hex).exists()
    assert AuditEvent.objects.filter(event_type="hosted_file_deleted").count() == 2
    again = post(
        browser,
        "/admin/files/delete",
        {"action": "confirm", "file_id": [str(guide.pk)]},
    )
    assert "Already deleted" in unescape(again.content.decode())
    assert Client().get(f"/files/{guide.token}").status_code == 404


def pending(store, record):
    """Record, but do not install, one content request."""
    actor, key = uuid4(), uuid4()
    patch = [{"operation": "add", "section": "content", **record}]
    bind_operations(patch, policy_operation_id(actor, key))
    return record_request(
        base_digest=store.active().digest,
        patch=patch,
        actor_id=actor,
        request_key=key,
        correlation_id=uuid4(),
    )


def test_a_pending_change_holds_its_files(auth_service, google, media):
    """A staged configuration request that names a file keeps it in use."""
    store = auth_service.store
    campaign, _ = draft(store)
    browser, _ = signed_in()
    row = uploaded(browser, "guide.pdf", samples.pdf(), slug="guide")
    record = content(
        str(campaign.pk), slot="login_help", html="<p>{{ file.guide }}</p>", text="g"
    )
    pending(store, record)
    uses = file_uses([row.pk])[row.pk]
    assert [use.source for use in uses] == ["pending_change"]
    with (
        pytest.raises(IntegrityError, match="Hosted file is in use"),
        transaction.atomic(),
    ):
        HostedFile.objects.filter(pk=row.pk).delete()
    # A request naming a file that does not exist is refused outright.
    missing = content(str(campaign.pk), slot="login_help", html="<p>{{ file.x }}</p>")
    with pytest.raises(ConfigError, match="hosted file that does not exist"):
        pending(store, missing)


def test_a_request_pins_its_files_against_a_concurrent_deletion(
    auth_service, google, media
):
    """FOR KEY SHARE makes a racing deletion wait for the request to commit."""
    browser, _ = signed_in()
    row = uploaded(browser, "guide.pdf", samples.pdf(), slug="guide")
    settings = connection.settings_dict
    with transaction.atomic():
        pin_references([{"values": {"html": "<p>{{ file.guide }}</p>"}}])
        with psycopg.connect(
            host=settings["HOST"],
            port=settings["PORT"],
            dbname=settings["NAME"],
            user=settings["USER"],
            password=settings["PASSWORD"],
            autocommit=True,
        ) as other:
            other.execute("SET lock_timeout = '200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute(
                    "DELETE FROM public.stewardship_hosted_file WHERE id = %s",
                    [row.pk],
                )


def test_archived_content_no_longer_holds_files(auth_service, google, media):
    """Only campaigns that can still render count as current content."""
    store = auth_service.store
    campaign, _ = draft(store)
    browser, _ = signed_in()
    row = uploaded(browser, "guide.pdf", samples.pdf(), slug="guide")
    assert add_content(store, campaign, "<p>{{ file.guide }}</p>").state == "applied"
    assert file_uses([row.pk])
    with connection.cursor() as cursor:
        # Simulate an archived campaign that is no longer the current pointer.
        cursor.execute("ALTER TABLE public.stewardship_campaign DISABLE TRIGGER ALL")
        cursor.execute("UPDATE public.stewardship_campaign SET state='archived'")
        cursor.execute("ALTER TABLE public.stewardship_campaign ENABLE TRIGGER ALL")
        cursor.execute(
            "ALTER TABLE public.stewardship_system_configuration DISABLE TRIGGER ALL"
        )
        cursor.execute(
            "UPDATE public.stewardship_system_configuration "
            "SET current_campaign_id=NULL"
        )
        cursor.execute(
            "ALTER TABLE public.stewardship_system_configuration ENABLE TRIGGER ALL"
        )
    assert not file_uses([row.pk])


def test_the_database_caps_the_library():
    """No more than 100 files or 200 MB, even for direct inserts."""
    actor = uuid4()

    def insert(index, size):
        HostedFile.objects.create(
            slug=f"file-{index}",
            original_name="x.pdf",
            kind="pdf",
            size=size,
            sha256="0" * 64,
            token=f"{index:043d}",
            uploaded_by_id=actor,
        )

    for index in range(20):
        insert(index, 10 * 1024 * 1024)
    with pytest.raises(IntegrityError, match="library is full"), transaction.atomic():
        insert(20, 1)
    HostedFile.objects.all().delete()
    for index in range(100):
        insert(index, 1)
    with pytest.raises(IntegrityError, match="library is full"), transaction.atomic():
        insert(100, 1)


def test_only_the_slug_can_change():
    """Receipts are immutable; every update advances the version."""
    row = HostedFile.objects.create(
        slug="guide",
        original_name="x.pdf",
        kind="pdf",
        size=1,
        sha256="0" * 64,
        token="t" * 43,
        uploaded_by_id=uuid4(),
    )
    for changes in ({"kind": "docx", "version": 2}, {"token": "u" * 43, "version": 2}):
        with pytest.raises(IntegrityError, match="immutable"), transaction.atomic():
            HostedFile.objects.filter(pk=row.pk).update(**changes)
    with (
        pytest.raises(IntegrityError, match="advance the record version"),
        transaction.atomic(),
    ):
        HostedFile.objects.filter(pk=row.pk).update(slug="new")
    HostedFile.objects.filter(pk=row.pk).update(slug="new", version=2)
    with pytest.raises(IntegrityError), transaction.atomic():
        HostedFile.objects.filter(pk=row.pk).update(slug="Bad", version=3)


def test_staff_cannot_use_the_library(auth_service, google, media):
    """Administrators only: Staff are denied every Admin route."""
    store = auth_service.store
    browser, _ = signed_in()
    row = uploaded(browser, "a.pdf", samples.pdf())
    staff = address("staff@example.org", roles=("staff",))
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **staff}],
        ).state
        == "applied"
    )
    google[0]["email"] = "staff@example.org"
    browser, _ = signed_in()
    assert browser.get(LIBRARY).status_code == 403
    assert upload(browser, "b.pdf", samples.pdf()).status_code == 403
    assert (
        post(
            browser, "/admin/files/delete", {"action": "confirm", "file_id": row.pk}
        ).status_code
        == 403
    )
    assert browser.get(f"/admin/files/{row.pk}/name").status_code == 403
    assert HostedFile.objects.filter(pk=row.pk).exists()


def test_the_library_page_uses_the_shared_table(auth_service, google, media):
    """Selection, bulk delete and the row navigator come from the common table."""
    browser, _ = signed_in()
    uploaded(browser, "a.pdf", samples.pdf())
    page = browser.get(LIBRARY).content.decode()
    assert "data-select-table" in page and "data-select-row" in page
    assert re.search(r'<button type="submit"[^>]*data-bulk-action>', page)
    assert 'data-copy="placeholder-' in page


def test_the_library_sorts_every_column_on_the_server(auth_service, google, media):
    """A heading's sort orders the whole library before paging; raw column
    names are refused and the navigator shows the page count."""
    browser, _ = signed_in()
    uploaded(browser, "alpha.pdf", samples.pdf(), "zulu")
    uploaded(browser, "Bravo.pdf", samples.pdf(), "yankee")
    page = browser.get(LIBRARY, {"sort": "-name", "size": "25"}).content.decode()
    assert page.index("Bravo.pdf") < page.index("alpha.pdf")
    assert 'aria-sort="descending"' in page and "Page 1 of 1" in page
    default = browser.get(LIBRARY).content.decode()
    assert default.index("Bravo.pdf") < default.index("alpha.pdf")
    by_name = browser.get(LIBRARY, {"sort": "name"}).content.decode()
    assert by_name.index("alpha.pdf") < by_name.index("Bravo.pdf")
    assert browser.get(LIBRARY, {"sort": "original_name"}).status_code == 400


def other_connection():
    """A second session to the test database, as a concurrent request would use."""
    settings = connection.settings_dict
    return psycopg.connect(
        host=settings["HOST"],
        port=settings["PORT"],
        dbname=settings["NAME"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        autocommit=True,
    )


def file_row(index, *, size=1, slug=None):
    """One library row inserted directly, for guard and race tests."""
    return HostedFile.objects.create(
        slug=slug or f"file-{index}",
        original_name="x.pdf",
        kind="pdf",
        size=size,
        sha256="0" * 64,
        token=f"{index:043d}",
        uploaded_by_id=uuid4(),
    )


INSERT = (
    "INSERT INTO public.stewardship_hosted_file (id, correlation_id, version, "
    "slug, original_name, kind, size, sha256, token, uploaded_by_id) VALUES "
    "(%s, %s, 1, %s, 'x.pdf', 'pdf', %s, %s, %s, %s)"
)


def insert_values(slug, token, size=1):
    """Parameters for ``INSERT`` in a separate session."""
    return [uuid4(), uuid4(), slug, size, "0" * 64, token, uuid4()]


def test_racing_uploads_cannot_pass_the_caps_or_share_a_slug():
    """Inserts serialize: the second waits, then sees the first's row."""
    for index in range(99):
        file_row(index)
    with other_connection() as first, other_connection() as second:
        first.execute("BEGIN")
        first.execute(INSERT, insert_values("last", "L" * 43))
        second.execute("SET lock_timeout = '200ms'")
        # The second upload waits on the library lock while the first is open.
        with pytest.raises(psycopg.errors.LockNotAvailable):
            second.execute(INSERT, insert_values("other", "O" * 43))
        first.execute("COMMIT")
        # Once the first commits, the library is full for the second.
        with pytest.raises(psycopg.errors.CheckViolation, match="library is full"):
            second.execute(INSERT, insert_values("other", "O" * 43))
    HostedFile.objects.filter(slug__in=["last", "file-0"]).delete()
    with other_connection() as first, other_connection() as second:
        first.execute("BEGIN")
        first.execute(INSERT, insert_values("same", "S" * 43))
        second.execute("SET lock_timeout = '200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            second.execute(INSERT, insert_values("same", "T" * 43))
        first.execute("COMMIT")
        with pytest.raises(psycopg.errors.UniqueViolation):
            second.execute(INSERT, insert_values("same", "T" * 43))
    assert HostedFile.objects.filter(slug="same").count() == 1


def test_a_request_pin_blocks_a_racing_rename_and_counts_raw_links():
    """A pending request holds files it names by placeholder or by raw link."""
    guide = file_row(1, slug="guide")
    linked = file_row(2, slug="linked")
    with transaction.atomic():
        pin_references(
            [
                {
                    "values": {
                        "html": '<p>{{ file.guide }} <a href="https://parish.'
                        f'example.org/files/{linked.token}">x</a></p>'
                    }
                }
            ]
        )
        with other_connection() as other:
            other.execute("SET lock_timeout = '200ms'")
            for row in (guide, linked):
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    other.execute(
                        "UPDATE public.stewardship_hosted_file "
                        "SET slug = 'renamed', version = version + 1 WHERE id = %s",
                        [row.pk],
                    )
    # A raw link to a file that is gone does not refuse the request.
    with transaction.atomic():
        pin_references([{"values": {"html": f"/files/{'Z' * 43}"}}])


def test_a_pending_change_counts_a_raw_link(auth_service, google, media):
    """Pasting a file's link, not its placeholder, still keeps it in use."""
    store = auth_service.store
    campaign, _ = draft(store)
    browser, _ = signed_in()
    row = uploaded(browser, "guide.pdf", samples.pdf(), slug="guide")
    link = f"{ORIGIN}/files/{row.token}"
    record = content(
        str(campaign.pk),
        slot="login_help",
        html=f'<p><a href="{link}" rel="noopener noreferrer">g</a></p>',
    )
    record["values"]["text"] = f"g: {link}"
    pending(store, record)
    assert [use.source for use in file_uses([row.pk])[row.pk]] == ["pending_change"]
    with pytest.raises(IntegrityError, match="in use"), transaction.atomic():
        HostedFile.objects.filter(pk=row.pk).delete()


def without_triggers(table, statement, parameters):
    """Change a row directly, bypassing its lifecycle guards (test setup only).

    Replica mode skips ordinary triggers for this session only, leaving the
    schema itself untouched for the tests that follow.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET session_replication_role = replica")
        try:
            cursor.execute(statement, parameters)
        finally:
            cursor.execute("SET session_replication_role = origin")


def test_unsent_campaign_mail_tests_hold_their_files():
    """A queued campaign email test naming a file's link keeps it in use."""
    row = file_row(1)
    test_id = uuid4()
    without_triggers(
        "stewardship_campaign_mail_test",
        "INSERT INTO public.stewardship_campaign_mail_test (id, correlation_id, "
        "version, requested_by_id, request_key, fingerprint, mail, state, "
        "campaign_id, configuration_id, task_id, template_id) VALUES "
        "(%s, %s, 1, %s, %s, %s, %s, 'queued', %s, %s, %s, %s)",
        [
            test_id,
            uuid4(),
            uuid4(),
            uuid4(),
            "0" * 64,
            json.dumps({"html": f'<a href="https://x.example/files/{row.token}">'}),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        ],
    )
    uses = file_uses([row.pk])[row.pk]
    assert [(use.source, use.description) for use in uses] == [
        ("unsent_mail", "1 email(s) waiting to be sent")
    ]
    with pytest.raises(IntegrityError, match="in use"), transaction.atomic():
        HostedFile.objects.filter(pk=row.pk).delete()
    # Submitting still carries the message; every terminal state scrubs it.
    without_triggers(
        "stewardship_campaign_mail_test",
        "UPDATE public.stewardship_campaign_mail_test SET state='submitting', "
        "deadline_at=now(), run_id=%s, submitted_at=now(), task_fence=1, "
        "worker_id=%s WHERE id=%s",
        [uuid4(), uuid4(), test_id],
    )
    assert file_uses([row.pk])
    without_triggers(
        "stewardship_campaign_mail_test",
        "UPDATE public.stewardship_campaign_mail_test SET state='accepted', "
        "finished_at=now(), mail='{}'::jsonb WHERE id=%s",
        [test_id],
    )
    assert not file_uses([row.pk])


def test_unsent_family_mail_holds_its_files(family_mail):  # noqa: F811
    """A prepared outbox message linking a file keeps it in use until sent."""
    harness = family_mail
    row = file_row(1, slug="guide")
    definition = ScheduleDefinition.objects.get()
    template = content(
        str(harness.campaign.pk),
        kind="email",
        slot="initial",
        html='<p>{{ family_code }} {{ family_url }} <a href="{{ file.guide }}" '
        'rel="noopener noreferrer">Guide</a></p>',
        text="{{ family_code }} {{ family_url }} Guide: {{ file.guide }}",
    )
    assert (
        change(
            harness.service.store,
            harness.service.store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **template},
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(definition.pk),
                    "values": {
                        "template_version": template["id"],
                        "subject": template["values"]["subject"],
                    },
                },
            ],
        ).state
        == "applied"
    )
    with campaign_clock(definition.current_revision.due_at):
        message = prepare_mail(harness)
    render = message.render
    assert f"/files/{row.token}" in render.html
    assert f"/files/{row.token}" in render.text

    def sources():
        return sorted(use.source for use in file_uses([row.pk]).get(row.pk, []))

    assert sources() == ["content", "unsent_mail"]
    # Cancelling (a terminal state, which scrubs the sealed values) releases
    # it. The other states need real delivery attempts to reach, so the view's
    # state list is checked against the outbox's own: exactly the non-terminal
    # states count as unsent.
    without_triggers(
        "stewardship_outbox_message",
        "UPDATE public.stewardship_outbox_message SET state='cancelled', "
        "finished_at=now(), sealed_key_id=NULL, sealed_substitutions=NULL "
        "WHERE id=%s",
        [message.pk],
    )
    assert sources() == ["content"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_viewdef('public.stewardship_hosted_file_use'::regclass)"
        )
        view = cursor.fetchone()[0]
    unsent = view.split("stewardship_outbox_message m", 1)[1].split("GROUP BY", 1)[0]
    for state in ("pending", "submitting", "retry_wait", "delivery_unknown"):
        assert f"'{state}'" in unsent
    for state in ("delivered", "permanent_failure", "cancelled"):
        assert f"'{state}'" not in unsent


def test_bulk_delete_reports_a_busy_library_and_continues(auth_service, google, media):
    """A busy storage lock refuses each file with "try again", not an error page."""
    browser, _ = signed_in()
    first = uploaded(browser, "a.pdf", samples.pdf(), slug="a")
    second = uploaded(browser, "b.pdf", samples.pdf(), slug="b")
    with storage.storage_lock(media):
        response = post(
            browser,
            "/admin/files/delete",
            {"action": "confirm", "file_id": [str(first.pk), str(second.pk)]},
        )
    assert response.status_code == 200
    assert unescape(response.content.decode()).count("the library was busy") == 2
    assert HostedFile.objects.count() == 2
    done = post(
        browser,
        "/admin/files/delete",
        {"action": "confirm", "file_id": [str(first.pk), str(second.pk)]},
    )
    assert unescape(done.content.decode()).count("Deleted") == 2


def test_a_large_upload_spills_to_disk_and_is_stored_exactly(
    auth_service, google, media
):
    """Over 5 MB, Django writes the upload to a temporary file first."""
    browser, _ = signed_in()
    data = samples.pdf(7 * 1024 * 1024)
    row = uploaded(browser, "large.pdf", data)
    assert row.size == len(data)
    stored = (media / storage.DIRECTORY / row.pk.hex).read_bytes()
    assert stored == data
    response = Client().get(f"/files/{row.token}")
    assert b"".join(response.streaming_content) == data
