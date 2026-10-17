"""``parish show``, ``ministries list``, ``branding show``, ``campaign shares``.

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
read Parish settings, Ministries, Parish logos and Share options through the
pages' own pieces, so their values match the pages'.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.admin_editing import editable_configuration
from parishkit.stewardship.accounts.share_forms import default_share_options
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign

from ..campaign_factory import campaign, financial
from ..test_source_corpus import source
from .automation_builders import paired
from .campaign_builders import add_draft, command
from .test_admin_content_cli_postgresql import as_staff
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_branding_postgresql import asset, bundle
from .test_ministry_views_postgresql import two_ministries
from .test_source_families_postgresql import (
    publish,
    source_singletons,  # noqa: F401
)

pytestmark = pytest.mark.django_db(transaction=True)
READS = [
    ("parish", "show"),
    ("ministries", "list"),
    ("branding", "show"),
    ("campaign", "shares"),
]


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on."""
    return runner(auth_service, monkeypatch)


def shares_draft(store):
    """A real applied current draft with the financial module and options."""
    options = default_share_options()
    result, _, _ = add_draft(
        store,
        store.active(),
        uuid4(),
        campaign(modules=["financial"], financial=financial(), share_options=options),
    )
    assert result.state == "applied"
    return Campaign.objects.get(), options


def read(admin, secret, *argv):
    """Run one read and return its result, which must succeed."""
    code, document, _ = admin(*argv, secret=secret)
    assert code == 0, document
    return document["result"]


def test_parish_show_is_the_editors_profile(admin):
    """A read-only session reads the applied profile; nothing is recorded."""
    store = admin.service.store
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    result = read(admin, reader, "parish", "show")
    parish = editable_configuration(admin.service).active_configuration.parish
    assert result["version"] == store.active().digest
    assert set(result["parish"]) == {
        "name",
        "website",
        "timezone",
        "phone",
        "online_giving_url",
        "date_format",
    }
    for key in ("name", "website", "timezone", "phone"):
        assert result["parish"][key] == getattr(parish, key)
    assert AuditEvent.objects.count() == events


def test_ministries_list_is_the_pages_catalog(admin):
    """Every catalog row, by name, with campaign inclusion and no activity."""
    publish(two_ministries())
    store = admin.service.store
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    result = read(admin, reader, "ministries", "list")
    assert result["version"] == store.active().digest
    assert result["campaign_id"] is None and result["snapshot_id"]
    names = [row["name"] for row in result["ministries"]]
    assert "Lectors" in names and names == sorted(names, key=str.casefold)
    lectors = next(row for row in result["ministries"] if row["name"] == "Lectors")
    assert lectors == {"duid": 5, "name": "Lectors", "included": False}
    assert AuditEvent.objects.count() == events


def test_ministries_list_marks_the_current_drafts_ministries(admin):
    """A Ministry the current draft asks about is ``included``; others not."""
    publish(two_ministries())
    store = admin.service.store
    result, _, _ = add_draft(
        store,
        store.active(),
        uuid4(),
        campaign(modules=["census", "ministry"], ministry_duids=[5]),
    )
    assert result.state == "applied"
    _, secret, _ = paired(admin.service)
    listed = read(admin, secret, "ministries", "list")
    assert listed["campaign_id"] == str(Campaign.objects.get().pk)
    included = {row["duid"]: row["included"] for row in listed["ministries"]}
    assert included[5] is True
    assert not any(value for duid, value in included.items() if duid != 5)


def test_ministries_list_without_a_catalog_is_unavailable(admin):
    """No promoted ParishSoft catalog: the page answers 503, so exit 3."""
    _, secret, _ = paired(admin.service)
    code, document, _ = admin("ministries", "list", secret=secret)
    assert code == 3 and document["error"]["code"] == "unavailable", document
    assert "result" not in document


def test_branding_show_lists_the_current_logos(admin):
    """The logos the page draws, in slot order, each with its stored facts;
    a slot whose asset row is missing is left out, as on the page."""
    store = admin.service.store
    parish = editable_configuration(admin.service).active_configuration.parish
    row = bundle(store.active(), uuid4())
    # Created out of slot order, and without the menu logo's row.
    favicon = asset(row, "favicon", id=parish.favicon_id, width=32, height=32)
    large = asset(row, "large", id=parish.large_logo_id, width=512, height=256)
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    result = read(admin, reader, "branding", "show")
    assert result == {
        "version": store.active().digest,
        "logos": [
            {
                "slot": slot,
                "id": str(item.pk),
                "width": item.width,
                "height": item.height,
                "size": item.size,
                "sha256": item.sha256,
            }
            for slot, item in (("large", large), ("favicon", favicon))
        ],
    }
    assert AuditEvent.objects.count() == events


def test_campaign_shares_of_a_draft_then_a_live_campaign(admin):
    """A draft's options are editable; once activated they still read, with
    ``editable`` false, where the page refuses to open."""
    store = admin.service.store
    row, options = shares_draft(store)
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    result = read(admin, reader, "campaign", "shares")
    assert result == {
        "version": store.active().digest,
        "campaign_id": str(row.pk),
        "editable": True,
        "financial": True,
        "options": [
            {
                "id": item["id"],
                "name": item["label"],
                "short_name": item.get("short_label", ""),
                "free_text": item["free_text"],
            }
            for item in options
        ],
    }
    assert AuditEvent.objects.count() == events

    command(row, uuid4(), Action.ACTIVATE)
    result = read(admin, reader, "campaign", "shares", "--campaign", str(row.pk))
    assert result["campaign_id"] == str(row.pk)
    assert not result["editable"] and result["financial"]
    assert [item["id"] for item in result["options"]] == [
        item["id"] for item in options
    ]


def test_campaign_shares_without_the_financial_module(admin):
    """A census-only draft has no Share options to edit."""
    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    _, secret, _ = paired(admin.service)
    result = read(admin, secret, "campaign", "shares")
    assert not result["editable"] and not result["financial"]


@pytest.mark.parametrize("current", [False, True])
def test_campaign_shares_of_no_campaign_is_not_available(admin, current):
    """No current campaign, or an unknown one, is not available."""
    argv = ["campaign", "shares"]
    if current:
        shares_draft(admin.service.store)
        argv += ["--campaign", str(uuid4())]
    _, secret, _ = paired(admin.service)
    code, document, _ = admin(*argv, secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document


@pytest.mark.parametrize("argv", READS)
def test_a_reader_who_is_not_an_administrator_is_denied(admin, monkeypatch, argv):
    """Staff cannot read the configuration pages: denied, nothing printed."""
    publish(source())
    shares_draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    as_staff(monkeypatch)
    code, document, _ = admin(*argv, secret=secret)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document


@pytest.mark.parametrize("argv", READS)
def test_an_ended_session_reads_nothing(admin, argv):
    """A revoked session is exit 5, and nothing is printed."""
    publish(source())
    shares_draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document, _ = admin(*argv, secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert "result" not in document


def test_an_ended_session_outranks_no_campaign(admin):
    """With no current campaign an ended session is still exit 5: the session
    is rechecked before the refusal is reported."""
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document, _ = admin("campaign", "shares", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
