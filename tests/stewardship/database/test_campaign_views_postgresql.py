"""Draft editor uses real Google sessions, YAML requests, SQL guards and source."""

from uuid import uuid4

import pytest
from django.test import Client

from parishkit.stewardship.accounts.campaign_forms import initial_fields
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..campaign_factory import campaign, financial
from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in
from .campaign_builders import add_draft, change, command
from .test_background_grants_postgresql import task_login
from .test_current_chair_postgresql import publish
from .test_parish_views_postgresql import token
from .test_source_families_postgresql import source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
NEW = "/admin/campaign/new"


def url(row):
    """Each retained campaign has its own authorized structural editor."""
    return f"/admin/campaign/{row.pk}/settings"


def fields(store, row=None, **changes):
    """Convert HTML checkbox/multiselect behavior rather than posting Python values."""
    values = (
        initial_fields(
            row.active_configuration.values if row else campaign()["values"],
            digest=store.active().digest,
        )
        | changes
    )
    return {
        name: ("on" if value is True else value)
        for name, value in values.items()
        if value is not False and value is not None
    } | {"action": "preview"}


def post(browser, path, values):
    """Every mutation includes the current CSRF cookie from a genuine login."""
    return browser.post(
        path, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def apply(store, response):
    """Installation is a separate process boundary from accepting the web request."""
    assert response.status_code == 302, response.content
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    receipt = install_request(store, request_id=row.pk, correlation_id=uuid4())
    assert receipt.state == "applied"


def test_new_draft_requires_confirmation_and_applied_receipt(auth_service, google):
    """No source, Ministry or financial data is required for a census-only draft."""
    browser, _ = signed_in()
    store = auth_service.store
    response = browser.get(NEW)
    assert response.status_code == 200 and b"Create campaign draft" in response.content
    proposal = token(post(browser, NEW, fields(store)))
    assert (
        not Campaign.objects.exists()
        and not ConfigurationChangeRequest.objects.exists()
    )
    accepted = post(browser, NEW, {"action": "confirm", "preview": proposal})
    assert not Campaign.objects.exists()
    apply(store, accepted)
    row = Campaign.objects.get()
    assert row.state == "draft" and not row.structural_locked
    assert SystemConfiguration.objects.get().current_campaign_id == row.pk
    assert (
        post(browser, NEW, {"action": "confirm", "preview": proposal})["Location"]
        == accepted["Location"]
    )
    assert ConfigurationChangeRequest.objects.count() == 1
    assert browser.get(NEW).status_code == 409
    assert b"Campaign settings" in browser.get(url(row)).content


def test_disabling_financial_preview_warns_before_discarding_custom_sharing(
    auth_service, google
):
    """The required empty disabled-module value must not silently erase labels."""
    from parishkit.stewardship.accounts.share_forms import default_share_options

    store = auth_service.store
    record = campaign(modules=["census", "financial"], financial=financial())
    record["values"]["share_options"] = default_share_options()
    record["values"]["share_options"][0]["label"] = "Our custom sharing label"
    add_draft(store, store.active(), uuid4(), record)
    row = Campaign.objects.get()
    browser, _ = signed_in()
    data = fields(store, row, financial_enabled=False)
    for name in (
        "financial_start",
        "financial_end",
        "comparison_start",
        "comparison_end",
        "fund_duids",
        "comparison_fund_duids",
        "overlap_confirmed",
    ):
        data.pop(name, None)
    response = post(browser, url(row), data)
    assert response.status_code == 200, response.content
    assert b"Re-enabling it starts with the default options" in response.content
    assert (
        row.active_configuration.values["share_options"][0]["label"]
        == "Our custom sharing label"
    )


def test_draft_can_change_timezone_without_changing_parish_default(
    auth_service, google
):
    """A draft's explicit timezone is independent after its initial creation."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    response = post(
        browser, url(row), fields(store, row, timezone="America/Los_Angeles")
    )
    assert response.status_code == 302
    assert browser.get(response["Location"]).status_code == 200
    from .test_schedule_views_postgresql import fields as schedule_fields

    data, _ = schedule_fields(store, row)
    data["window-timezone"] = "America/Los_Angeles"
    path = response["Location"].split("?", 1)[0]
    proposal = token(post(browser, path, data))
    apply(store, post(browser, path, {"action": "confirm", "preview": proposal}))
    row.refresh_from_db()
    assert row.active_configuration.timezone == "America/Los_Angeles"
    assert (
        store.active().document()["sections"]["parish"][0]["values"]["timezone"]
        == "America/New_York"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"end_date": "2026-09-01"},
        {"state": "active"},
        {"timezone": "America/Los_Angeles"},
        {"ministry_duids": ["4"]},
        {"fund_duids": ["6"]},
        {"mode": "production"},
        {"name": ["First", "Second"]},
    ],
)
def test_invalid_hidden_and_new_timezone_values_do_not_create_intent(
    auth_service, google, changes
):
    """A typed form and closed request parser reject controls that were not offered."""
    browser, _ = signed_in()
    response = post(browser, NEW, fields(auth_service.store, **changes))
    assert response.status_code == 400
    assert not ConfigurationChangeRequest.objects.exists()


def test_current_campaign_blocks_second_creation_and_old_preview(auth_service, google):
    """Concurrent draft creation invalidates the exact preview, not just its form."""
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, NEW, fields(store)))
    add_draft(store, store.active(), uuid4())
    assert post(browser, NEW, fields(store)).status_code == 409
    assert (
        post(browser, NEW, {"action": "confirm", "preview": proposal}).status_code
        == 409
    )
    assert Campaign.objects.count() == 1


def test_live_lock_invalidates_preview_without_a_yaml_change(auth_service, google):
    """The source/config digest alone cannot authorize an edit after going live."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    proposal = token(
        post(browser, url(row), fields(store, row, name="Changed campaign name"))
    )
    prior = store.active().digest
    command(row, uuid4(), Action.ACTIVATE)
    assert store.active().digest == prior
    assert (
        post(browser, url(row), {"action": "confirm", "preview": proposal}).status_code
        == 409
    )
    assert post(browser, url(row), fields(store, row, name="Locked")).status_code == 409
    response = browser.get(url(row))
    assert response.status_code == 200 and b"read-only" in response.content
    assert b"Preview changes" not in response.content


def test_source_replacement_requires_fresh_preview(auth_service, google):
    """Catalog changes cannot silently change a reviewed Ministry/fund selection."""
    publish(source())
    store = auth_service.store
    browser, _ = signed_in()
    values = fields(store, ministry="on", ministry_duids=["4"])
    proposal = token(post(browser, NEW, values))
    publish(source())
    assert (
        post(browser, NEW, {"action": "confirm", "preview": proposal}).status_code
        == 409
    )
    assert not ConfigurationChangeRequest.objects.exists()


def test_financial_and_ministry_catalogs_work_under_real_web_grants(
    auth_service, google
):
    """Runtime SELECT access is limited to public catalog payloads, not giving rows."""
    publish(source())
    store = auth_service.store
    browser, _ = signed_in()
    row = campaign(
        modules=["financial", "ministry"],
        ministry_duids=[4],
        financial=financial(fund_duids=[9], comparison_fund_duids=[9]),
    )
    values = initial_fields(row["values"], digest=store.active().digest)
    values = {
        name: ("on" if value is True else value)
        for name, value in values.items()
        if value is not False and value is not None
    } | {"action": "preview"}
    with task_login(ServiceRole.WEB):
        assert browser.get(NEW).status_code == 200
        proposal = token(post(browser, NEW, values))
        response = post(browser, NEW, {"action": "confirm", "preview": proposal})
        assert response.status_code == 302, response.content
        assert b"Applying" in browser.get(response["Location"]).content
    apply(store, response)
    assert Campaign.objects.get().active_configuration.values["financial"][
        "fund_duids"
    ] == [9]


def test_inactive_selected_funds_do_not_block_unrelated_draft_edits(
    auth_service, google
):
    """Both pledge and comparison scope retain an explicitly labeled inactive fund."""
    store = auth_service.store
    data = source()
    data.funds[9]["active"] = False
    publish(data)
    row = campaign(
        modules=["financial"],
        financial=financial(fund_duids=[9], comparison_fund_duids=[9]),
    )
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "campaigns", **row}],
    )
    current = Campaign.objects.get()
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        page = browser.get(url(current))
        assert b"Offertory (inactive; retained selection)" in page.content
        assert (
            post(
                browser, url(current), fields(store, current, name="Updated name")
            ).status_code
            == 200
        )


@pytest.mark.parametrize("with_source", [False, True])
def test_missing_catalog_selections_survive_unrelated_edit(
    auth_service, google, with_source
):
    """Missing corpus rows retain labeled saved IDs, never unrelated tenant names."""
    store = auth_service.store
    if with_source:
        publish(source())
    row = campaign(
        modules=["financial", "ministry"],
        ministry_duids=[999],
        financial=financial(fund_duids=[998], comparison_fund_duids=[998]),
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "campaigns", **row}],
        ).state
        == "applied"
    )
    current = Campaign.objects.get()
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        page = browser.get(url(current))
        assert page.status_code == 200
        assert b"Ministry 999 (unavailable; retained selection)" in page.content
        assert b"Fund 998 (unavailable; retained selection)" in page.content
        response = post(
            browser, url(current), fields(store, current, name="Updated name")
        )
        assert response.status_code == 200, response.content


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_non_admin_cannot_read_or_write_campaign_settings(auth_service, google, role):
    """Campaign settings are not a reporting capability, even through direct URLs."""
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    assert browser.get(NEW).status_code == 403
    assert post(browser, NEW, fields(store)).status_code == 403
    assert browser.get(f"/admin/campaign/{uuid4()}/settings").status_code == 403


def test_noop_bad_signature_missing_target_and_query_are_closed(auth_service, google):
    """Malformed navigation cannot expose data or turn no-op input into a save."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    response = post(browser, url(row), fields(store, row))
    assert (
        response.status_code == 400 and b"No settings have changed" in response.content
    )
    assert (
        post(browser, url(row), {"action": "confirm", "preview": "forged"}).status_code
        == 400
    )
    assert browser.get(f"/admin/campaign/{uuid4()}/settings").status_code == 404
    assert browser.get(url(row) + "?extra=value").status_code == 400
    assert Client().get(url(row)).status_code == 403


def test_campaign_edit_cannot_strand_existing_initial_mail(auth_service, google):
    """A proposed window transfers to reconciliation without changing any data."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    response = post(browser, url(row), fields(store, row, start_date="2026-10-02"))
    assert response.status_code == 302
    assert "/schedules?" in response["Location"]
    assert "start_date=2026-10-02" in response["Location"]
    row.refresh_from_db()
    assert row.active_configuration.start_date.isoformat() == "2026-10-01"
