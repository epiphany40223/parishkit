"""``artwork show``: the Campaign images page's read.

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The images
are chosen through the page itself (upload, review, Apply), so the command
reads exactly what the page applied and draws.
"""

import pytest
from django.db import connection

from parishkit.stewardship.accounts.artwork_views import SLOT_LABELS, UNAVAILABLE
from parishkit.stewardship.accounts.branding_models import BrandingAsset
from parishkit.stewardship.audit.models import AuditEvent

from .auth_builders import signed_in, unguarded
from .automation_builders import paired
from .test_admin_content_cli_postgresql import as_staff
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_artwork_views_postgresql import (
    artwork,
    draft,
    image,
    media,  # noqa: F401
    select,
)
from .test_campaign_views_postgresql import apply

pytestmark = pytest.mark.django_db(transaction=True)
SLOTS = ["banner", "welcome", "member", "financial", "closing"]


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def show(admin, secret):
    """Run ``artwork show`` and return its result, which must succeed."""
    code, document, _ = admin("artwork", "show", secret=secret)
    assert code == 0, document
    return document["result"]


def test_artwork_show_lists_every_slot_and_the_applied_images(admin, media):  # noqa: F811
    """A read-only session reads every slot in the page's order: the images
    the page applied, and the Financial icon unavailable for a census-only
    campaign. Nothing is recorded."""
    store = admin.service.store
    row, path = draft(store)
    _, reader, _ = paired(admin.service, scope="read-only")
    empty = show(admin, reader)
    assert empty["campaign_id"] == str(row.pk)
    assert [item["slot"] for item in empty["images"]] == SLOTS
    assert all(item["image"] is None for item in empty["images"])

    browser, _ = signed_in()
    apply(store, select(browser, admin.service, path, "banner", image(1625, 345)))
    apply(store, select(browser, admin.service, path, "closing", image(600, 600)))
    chosen = artwork(row)["images"]
    events = AuditEvent.objects.count()
    result = show(admin, reader)

    def facts(slot):
        """The stored facts of the slot's applied image, as printed."""
        item = BrandingAsset.objects.get(pk=chosen[slot])
        return {
            "id": str(item.pk),
            "width": item.width,
            "height": item.height,
            "size": item.size,
            "sha256": item.sha256,
        }

    assert result == {
        "version": store.active().digest,
        "campaign_id": str(row.pk),
        "images": [
            {
                "slot": slot,
                "name": str(SLOT_LABELS[slot]),
                "unavailable": str(UNAVAILABLE["financial"])
                if slot == "financial"
                else None,
                "image": facts(slot) if slot in chosen else None,
            }
            for slot in SLOTS
        ],
    }
    assert AuditEvent.objects.count() == events


def test_a_slot_whose_asset_row_is_missing_shows_no_image(admin, media):  # noqa: F811
    """The configuration names assets by plain id: with an applied image's
    row gone, its slot prints ``image`` null, as the page draws it empty,
    and keeps its label and availability."""
    store = admin.service.store
    row, path = draft(store)
    browser, _ = signed_in()
    apply(store, select(browser, admin.service, path, "banner", image(1625, 345)))
    chosen = artwork(row)["images"]
    # Asset rows are immutable to every runtime login; only a superuser
    # could remove one, so the guard triggers are skipped for this delete.
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM stewardship_branding_asset WHERE id = %s",
            [chosen["banner"]],
        )
    assert not BrandingAsset.objects.filter(pk=chosen["banner"]).exists()
    _, secret, _ = paired(admin.service, scope="read-only")
    banner = show(admin, secret)["images"][0]
    assert banner == {
        "slot": "banner",
        "name": str(SLOT_LABELS["banner"]),
        "unavailable": None,
        "image": None,
    }


def test_artwork_show_without_a_campaign_is_not_available(admin):
    """No current campaign: the page has nothing to show, so not available."""
    _, secret, _ = paired(admin.service)
    code, document, _ = admin("artwork", "show", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document


def test_a_reader_who_is_not_an_administrator_is_denied(admin, monkeypatch):
    """Staff cannot read Campaign images: denied, nothing printed."""
    draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    as_staff(monkeypatch)
    code, document, _ = admin("artwork", "show", secret=secret)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document


@pytest.mark.parametrize("campaign", [True, False])
def test_an_ended_session_reads_nothing(admin, campaign):
    """A revoked session is exit 5, with or without a current campaign:
    admission refuses it before anything is read."""
    if campaign:
        draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document, _ = admin("artwork", "show", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert "result" not in document
