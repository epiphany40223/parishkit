"""``artwork remove-preview`` and ``artwork remove`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The images
are chosen through the page itself (upload, review, Apply), and the
commands remove them through the page's own removal review and
confirmation, so a token from either channel confirms on the other.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import parish_views
from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
from parishkit.stewardship.accounts.artwork_views import (
    SLOT_LABELS,
    UNAVAILABLE,
    _salt,
)
from parishkit.stewardship.accounts.branding_models import BrandingAsset
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in
from .automation_builders import as_staff, paired
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_artwork_views_postgresql import (
    artwork,
    carried,
    change_parish,
    draft,
    image,
    media,  # noqa: F401
    removal,
    select,
)
from .test_background_grants_postgresql import task_login
from .test_branding_views_postgresql import post
from .test_campaign_views_postgresql import apply

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_artwork_remove"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


@pytest.fixture
def banner(admin, media):  # noqa: F811
    """A current draft whose banner the page applied; returns the campaign."""
    store = admin.service.store
    row, path = draft(store)
    browser, _ = signed_in()
    apply(store, select(browser, admin.service, path, "banner", image(1625, 345)))
    assert "banner" in artwork(row)["images"]
    return row


def preview(admin, secret, slot, version=None):
    """Run ``artwork remove-preview`` for ``slot``."""
    version = version or admin.service.store.active().digest
    return admin(
        "artwork",
        "remove-preview",
        slot,
        "--expected-version",
        version,
        secret=secret,
    )


def remove(admin, secret, slot, token):
    """Run ``artwork remove`` for ``slot`` with ``token``."""
    return admin("artwork", "remove", slot, "--token", token, secret=secret)


def token(admin, secret, slot="banner"):
    """A successful preview's token."""
    code, document, _ = preview(admin, secret, slot)
    assert code == 0, document
    return document["result"]["preview"]["token"]


def refused(document, code):
    """The error document's code is ``code``, with no result."""
    assert document["error"]["code"] == code, document
    assert "result" not in document


def events():
    """How many ``artwork remove`` events are recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def install(admin, confirmed):
    """Install the request a successful ``artwork remove`` recorded."""
    assert confirmed["result"]["created"], confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"


def set_icon(store, campaign_id, slot):
    """Select a ready icon for ``slot`` through the actual installer.

    The page refuses an upload for a slot whose Family page the campaign
    does not show (#563), so this sets one the way an earlier configuration
    (before the module was turned off) would have: a ready one-image bundle
    named by a configuration change. Returns the asset's id.
    """
    from datetime import timedelta

    from django.db.models import F

    from parishkit.stewardship.accounts.branding_models import BrandingBundle
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedConfigurationVersion,
    )
    from parishkit.stewardship.accounts.sessions import database_now
    from parishkit.stewardship.campaigns.models import Campaign

    from .campaign_builders import change

    actor = uuid4()
    version = store.active()
    bundle = BrandingBundle.objects.create(
        owner_id=actor,
        actor_id=actor,
        session_id=uuid4(),
        base=AppliedConfigurationVersion.objects.get(digest=version.digest),
        expires_at=database_now() + timedelta(hours=1),
        correlation_id=uuid4(),
    )
    asset = BrandingAsset.objects.create(
        id=uuid4(),
        bundle=bundle,
        label="section",
        width=64,
        height=64,
        size=1,
        sha256="0" * 64,
        actor_id=actor,
        correlation_id=uuid4(),
    )
    BrandingBundle.objects.filter(pk=bundle.pk).update(
        state="ready", version=F("version") + 1, actor_id=actor
    )
    current = artwork(Campaign.objects.get(pk=campaign_id))
    images = {**current.get("images", {}), slot: str(asset.pk)}
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign_id),
            "values": {"artwork": {**current, "images": images}},
        }
    ]
    assert change(store, version, actor, patch).state == "applied"
    return asset.pk


def test_preview_and_remove_as_the_page(admin, banner):
    """The review names the image being removed; nothing changes until the
    removal is confirmed, which records the request and the command's event;
    once installed the slot is empty; a repeat returns the same request."""
    _, secret, _ = paired(admin.service)
    version = admin.service.store.active().digest
    asset = BrandingAsset.objects.get(pk=artwork(banner)["images"]["banner"])
    requests = ConfigurationChangeRequest.objects.count()
    code, document, _ = preview(admin, secret, "banner")
    assert code == 0, document
    result = document["result"]
    signed = result.pop("preview")["token"]
    assert result == {
        "version": version,
        "campaign_id": str(banner.pk),
        "slot": "banner",
        "name": str(SLOT_LABELS["banner"]),
        "image": {
            "id": str(asset.pk),
            "width": asset.width,
            "height": asset.height,
            "size": asset.size,
            "sha256": asset.sha256,
        },
    }
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0

    code, confirmed, _ = remove(admin, secret, "banner", signed)
    assert code == 0 and confirmed["result"]["created"], confirmed
    request_id = confirmed["result"]["request"]["request_id"]
    assert events() == 1
    receipt = install_request(
        admin.service.store, request_id=UUID(request_id), correlation_id=uuid4()
    )
    assert receipt.state == "applied"
    assert "banner" not in artwork(banner).get("images", {})
    # The image itself stays: the activated configurations' history names it.
    assert BrandingAsset.objects.filter(pk=asset.pk).exists()
    code, again, _ = remove(admin, secret, "banner", signed)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events() == 1
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # Nothing is left to remove now.
    code, document, _ = preview(admin, secret, "banner")
    assert code == 1
    refused(document, "not_available")


def test_a_command_preview_confirms_on_the_page(admin, banner):
    """Tokens are bound to the Administrator, not the channel."""
    browser, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    # The command runs under its own login, so it runs before the page's.
    signed = token(admin, secret)
    with task_login(ServiceRole.WEB):
        response = post(
            browser,
            "/admin/campaign/images/",
            {"action": "confirm", "preview": signed, "slot": "banner"},
        )
    assert response.status_code == 302, response.content
    assert response["Location"].startswith("/admin/changes/")
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    assert events() == 0


def test_a_page_preview_confirms_on_the_command_line(admin, banner):
    """The reverse direction: the page's own review token, confirmed by
    ``artwork remove``, records the request and the command's event."""
    browser, secret, _ = paired(admin.service)
    with task_login(ServiceRole.WEB):
        review = removal(browser, admin.service, "/admin/campaign/images/", "banner")
        signed = carried(review)["preview"]
    code, confirmed, _ = remove(admin, secret, "banner", signed)
    assert code == 0, confirmed
    install(admin, confirmed)
    assert "banner" not in artwork(banner).get("images", {})
    assert events() == 1


def test_an_unavailable_slots_image_can_still_be_removed(admin, media):  # noqa: F811
    """The census-only draft shows no Financial page, so its icon cannot be
    uploaded, but a current one is still reviewed and removed (#563)."""
    store = admin.service.store
    row, _ = draft(store)
    asset = set_icon(store, row.pk, "financial")
    _, secret, _ = paired(admin.service)
    code, shown, _ = admin("artwork", "show", secret=secret)
    financial = {item["slot"]: item for item in shown["result"]["images"]}["financial"]
    assert financial["unavailable"] == str(UNAVAILABLE["financial"])
    assert financial["image"]["id"] == str(asset)
    code, document, _ = preview(admin, secret, "financial")
    assert code == 0, document
    assert document["result"]["image"]["id"] == str(asset)
    code, confirmed, _ = remove(
        admin, secret, "financial", document["result"]["preview"]["token"]
    )
    assert code == 0, confirmed
    install(admin, confirmed)
    assert "financial" not in artwork(row).get("images", {})
    assert events() == 1


def test_preview_refusals(admin, banner):
    """Another version is stale; a slot with no image is not available;
    nothing is requested."""
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    code, document, _ = preview(admin, secret, "banner", version="0" * 64)
    assert code == 1
    refused(document, "stale_version")
    code, document, _ = preview(admin, secret, "welcome")
    assert code == 1
    refused(document, "not_available")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_remove_refuses_foreign_and_stale_tokens(admin, banner):
    """An altered token, another slot's or another page's is invalid;
    another Administrator's is denied; one reviewed before the settings
    changed is stale; nothing is requested by the command."""
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    signed = token(admin, secret)
    code, document, _ = remove(admin, secret, "banner", signed[:-2] + "xx")
    assert code == 1
    refused(document, "invalid")
    code, document, _ = remove(admin, secret, "closing", signed)
    assert code == 1
    refused(document, "invalid")
    # Another page's token (the same payload signed for Parish settings).
    payload = signing.loads(signed, salt=_salt(banner.pk, ("remove", "banner", None)))
    code, document, _ = remove(
        admin, secret, "banner", signing.dumps(payload, salt=parish_views.SALT)
    )
    assert code == 1
    refused(document, "invalid")
    # Another Administrator's review of the same removal.
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=_salt(banner.pk, ("remove", "banner", None)),
    )
    code, document, _ = remove(admin, secret, "banner", other)
    assert code == 1
    refused(document, "denied")
    change_parish(admin.service.store)
    requests += 1
    code, document, _ = remove(admin, secret, "banner", signed)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0


def test_without_a_campaign_nothing_is_available(admin):
    """No current campaign: neither command has an image to remove."""
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, "banner")
    assert code == 1
    refused(document, "not_available")
    code, document, _ = remove(admin, secret, "banner", "x")
    assert code == 1
    refused(document, "not_available")
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0


def test_refused_sessions(admin, banner, monkeypatch):
    """A read-only session and Staff are denied; an ended session is exit 5."""
    requests = ConfigurationChangeRequest.objects.count()
    _, reader, _ = paired(admin.service, scope="read-only")
    code, document, _ = preview(admin, reader, "banner")
    assert code == 1
    refused(document, "denied")
    _, secret, _ = paired(admin.service)
    signed = token(admin, secret)
    revoke(admin.service, secret)
    for code, document, _ in (
        preview(admin, secret, "banner"),
        remove(admin, secret, "banner", signed),
    ):
        assert code == 5
        refused(document, "session_ended")
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        preview(admin, staff, "banner"),
        remove(admin, staff, "banner", signed),
    ):
        assert code == 1
        refused(document, "denied")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0
