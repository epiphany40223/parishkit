"""``branding preview`` and ``branding confirm`` (#1865).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
stage logos through the page's own form and staging, review them through the
page's own review, and apply them through the page's own confirmation; the
installer then applies the request, and ``branding show`` reads the result.
"""

import io
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing
from PIL import Image

from parishkit.stewardship.accounts import parish_views
from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
from parishkit.stewardship.accounts.branding_models import BrandingAsset, BrandingBundle
from parishkit.stewardship.accounts.branding_slots import SLOTS
from parishkit.stewardship.accounts.branding_views import SALT
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from .automation_builders import as_staff, paired
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_artwork_views_postgresql import change_parish
from .test_background_grants_postgresql import task_login
from .test_branding_views_postgresql import carried, media, stage  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
PREVIEW = "admin_cmd_branding_preview"
CONFIRM = "admin_cmd_branding_confirm"


@pytest.fixture
def admin(auth_service, google, media, monkeypatch):  # noqa: F811
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def image(width=150, height=80, color="blue"):
    """A real PNG's bytes."""
    stream = io.BytesIO()
    Image.new("RGB", (width, height), color).save(stream, format="PNG")
    return stream.getvalue()


def preview(admin, secret, data=None, *, size=None, version=None):
    """Run ``branding preview -`` with ``data`` on standard input."""
    version = version or admin.service.store.active().digest
    options = ["--size", size] if size else []
    return admin(
        "branding",
        "preview",
        "-",
        *options,
        "--expected-version",
        version,
        secret=secret,
        answer=image() if data is None else data,
    )


def confirm(admin, secret, bundle, token):
    """Run ``branding confirm`` for ``bundle`` with ``token``."""
    return admin(
        "branding", "confirm", "--bundle", str(bundle), "--token", token, secret=secret
    )


def reviewed(admin, secret, **options):
    """A successful preview's result."""
    code, document, _ = preview(admin, secret, **options)
    assert code == 0, document
    return document["result"]


def refused(document, code):
    """The error document's code is ``code``, with no result."""
    assert document["error"]["code"] == code, document
    assert "result" not in document


def events(event):
    """How many ``event`` audit events are recorded."""
    return AuditEvent.objects.filter(event_type=event).count()


def install(admin, confirmed):
    """Install the request a successful ``branding confirm`` recorded."""
    assert confirmed["result"]["created"], confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    with task_login(ServiceRole.CONFIG_INSTALLER):
        receipt = install_request(
            admin.service.store, request_id=request_id, correlation_id=uuid4()
        )
    assert receipt.state == "applied"


def shown(admin, secret):
    """``branding show``'s logos, by slot."""
    code, document, _ = admin("branding", "show", secret=secret)
    assert code == 0, document
    return {logo["slot"]: logo for logo in document["result"]["logos"]}


def apply(admin, secret, **options):
    """Preview, confirm and install new logos; returns the preview's result."""
    result = reviewed(admin, secret, **options)
    code, confirmed, _ = confirm(
        admin, secret, result["bundle"], result["preview"]["token"]
    )
    assert code == 0, confirmed
    install(admin, confirmed)
    return result


def test_preview_and_confirm_as_the_page(admin):
    """The preview stages all four sizes under the automation session and
    records its event, with nothing requested; the confirm, a later run of
    the same session, records the request and its event; once installed
    ``branding show`` reads the new logos; a repeat returns the same
    request."""
    _, secret, row = paired(admin.service)
    version = admin.service.store.active().digest
    requests = ConfigurationChangeRequest.objects.count()
    result = reviewed(admin, secret)
    signed = result.pop("preview")["token"]
    logos = result.pop("logos")
    bundle = UUID(result["bundle"])
    assert result == {"version": version, "bundle": str(bundle), "mode": "basic"}
    assets = {
        asset.label: asset for asset in BrandingAsset.objects.filter(bundle_id=bundle)
    }
    assert logos == [
        {
            "slot": slot,
            "name": str(SLOTS[slot].title),
            "id": str(assets[slot].pk),
            "width": assets[slot].width,
            "height": assets[slot].height,
            "size": assets[slot].size,
            "sha256": assets[slot].sha256,
            "changed": True,
        }
        for slot in SLOTS
    ]
    staged = BrandingBundle.objects.get(pk=bundle)
    assert staged.state == "ready" and staged.mode == "basic"
    # Owned by the automation session, not the preview's command session.
    assert staged.session_id == row.pk
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events(PREVIEW) == 1 and events(CONFIRM) == 0

    code, confirmed, _ = confirm(admin, secret, bundle, signed)
    assert code == 0 and confirmed["result"]["created"], confirmed
    request_id = confirmed["result"]["request"]["request_id"]
    assert events(CONFIRM) == 1
    install(admin, confirmed)
    current = shown(admin, secret)
    assert {slot: logo["id"] for slot, logo in current.items()} == {
        slot: str(assets[slot].pk) for slot in SLOTS
    }
    code, again, _ = confirm(admin, secret, bundle, signed)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events(CONFIRM) == 1 and events(PREVIEW) == 1
    assert ConfigurationChangeRequest.objects.count() == requests + 1


def test_the_upload_outlives_the_previews_command_session(admin):
    """The maintenance pass deletes the preview's ended command session and
    its link, but the logos are the automation session's, so a later
    ``branding confirm`` still applies them."""
    from parishkit.stewardship.accounts.automation_models import AutomationLogin
    from parishkit.stewardship.accounts.automation_sessions import (
        delete_orphaned_logins,
    )
    from parishkit.stewardship.accounts.models import PortalSession
    from parishkit.stewardship.accounts.sessions import cleanup_admin_sessions

    _, secret, row = paired(admin.service)
    result = reviewed(admin, secret)
    cleanup_admin_sessions()
    delete_orphaned_logins()
    assert not AutomationLogin.objects.filter(automation_session=row).exists()
    assert not PortalSession.objects.filter(revoked_at__isnull=False).exists()
    code, confirmed, _ = confirm(
        admin, secret, result["bundle"], result["preview"]["token"]
    )
    assert code == 0, confirmed
    install(admin, confirmed)
    # The printed sizes run smallest first (#2288); the large logo is last.
    assert result["logos"][-1]["slot"] == "large"
    assert shown(admin, secret)["large"]["id"] == result["logos"][-1]["id"]


def test_one_size_replaces_only_that_size(admin):
    """``--size favicon`` is the page's Advanced upload of one size: the
    other three keep their current images, and the review says so."""
    _, secret, _ = paired(admin.service)
    apply(admin, secret)
    before = shown(admin, secret)
    result = apply(admin, secret, data=image(48, 48, "red"), size="favicon")
    assert result["mode"] == "advanced"
    changed = {logo["slot"]: logo["changed"] for logo in result["logos"]}
    assert changed == {"large": False, "menu": False, "icon": False, "favicon": True}
    after = shown(admin, secret)
    for slot in ("large", "menu", "icon"):
        assert after[slot]["sha256"] == before[slot]["sha256"]
    assert after["favicon"]["sha256"] != before["favicon"]["sha256"]
    assert BrandingBundle.objects.get(pk=result["bundle"]).mode == "advanced"


def test_preview_refusals_stage_nothing(admin):
    """Another version is stale; a file that is not an image, or a size
    outside its rules, is invalid at its field in the page's words; a size
    without current logos to keep is invalid at the missing size. Nothing
    is staged, recorded or requested."""
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    code, document, _ = preview(admin, secret, version="0" * 64)
    assert code == 1
    refused(document, "stale_version")
    code, document, _ = preview(admin, secret, b"not an image")
    assert code == 1
    refused(document, "invalid")
    assert [problem["field"] for problem in document["error"]["fields"]] == [
        "branding.logo"
    ]
    code, document, _ = preview(admin, secret, image(400, 120), size="favicon")
    assert code == 1
    refused(document, "invalid")
    [problem] = document["error"]["fields"]
    assert problem["field"] == "branding.favicon"
    assert "a favicon must be square" in problem["message"]
    # No logos yet: an Advanced upload of one size has nothing to keep.
    code, document, _ = preview(admin, secret, image(48, 48), size="favicon")
    assert code == 1
    refused(document, "invalid")
    assert all(
        problem["field"].startswith("branding.")
        for problem in document["error"]["fields"]
    )
    code, document, _ = preview(admin, secret, b"")
    assert code == 1
    refused(document, "invalid")
    assert not BrandingBundle.objects.exists()
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events(PREVIEW) == 0 and events(CONFIRM) == 0


def test_only_the_same_sessions_upload_is_applied(admin):
    """Logos staged on the page, or by another automation session of the
    same Administrator, are not this session's to apply."""
    browser, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    with task_login(ServiceRole.WEB):
        page = carried(stage(browser, admin.service))
    code, document, _ = confirm(admin, secret, page["bundle"], page["preview"])
    assert code == 1
    refused(document, "not_available")
    _, other, _ = paired(admin.service)
    result = reviewed(admin, other)
    code, document, _ = confirm(
        admin, secret, result["bundle"], result["preview"]["token"]
    )
    assert code == 1
    refused(document, "not_available")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events(CONFIRM) == 0


def test_confirm_refuses_foreign_and_stale_tokens(admin):
    """An altered token, another bundle's or another page's is invalid;
    another Administrator's is denied; one reviewed before the settings
    changed is stale; nothing is requested."""
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    first, second = reviewed(admin, secret), reviewed(admin, secret)
    bundle, signed = first["bundle"], first["preview"]["token"]
    code, document, _ = confirm(admin, secret, bundle, signed[:-2] + "xx")
    assert code == 1
    refused(document, "invalid")
    code, document, _ = confirm(admin, secret, second["bundle"], signed)
    assert code == 1
    refused(document, "invalid")
    payload = signing.loads(signed, salt=SALT + bundle)
    code, document, _ = confirm(
        admin, secret, bundle, signing.dumps(payload, salt=parish_views.SALT)
    )
    assert code == 1
    refused(document, "invalid")
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=SALT + bundle,
    )
    code, document, _ = confirm(admin, secret, bundle, other)
    assert code == 1
    refused(document, "denied")
    change_parish(admin.service.store)
    requests += 1
    code, document, _ = confirm(admin, secret, bundle, signed)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events(CONFIRM) == 0


def test_refused_sessions(admin, monkeypatch):
    """A read-only session and Staff are denied; an ended session is exit 5;
    nothing is staged by them."""
    _, secret, _ = paired(admin.service)
    result = reviewed(admin, secret)
    bundle, signed = result["bundle"], result["preview"]["token"]
    staged = BrandingBundle.objects.count()
    requests = ConfigurationChangeRequest.objects.count()
    _, reader, _ = paired(admin.service, scope="read-only")
    for code, document, _ in (
        preview(admin, reader),
        confirm(admin, reader, bundle, signed),
    ):
        assert code == 1
        refused(document, "denied")
    revoke(admin.service, secret)
    for code, document, _ in (
        preview(admin, secret),
        confirm(admin, secret, bundle, signed),
    ):
        assert code == 5
        refused(document, "session_ended")
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        preview(admin, staff),
        confirm(admin, staff, bundle, signed),
    ):
        assert code == 1
        refused(document, "denied")
    assert BrandingBundle.objects.count() == staged
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events(PREVIEW) == 1 and events(CONFIRM) == 0
