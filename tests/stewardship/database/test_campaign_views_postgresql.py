"""Draft editor uses real Google sessions, YAML requests, SQL guards and source."""

import re
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from django.test import Client

from parishkit.stewardship.accounts.campaign_forms import initial_fields
from parishkit.stewardship.accounts.campaign_views import SALT
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..campaign_factory import campaign, financial
from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in
from .campaign_builders import add_draft, change, command
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_current_chair_postgresql import publish
from .test_parish_views_postgresql import digest, region, token
from .test_source_families_postgresql import source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
NEW = "/admin/campaign/new"


def url(row):
    """Each retained campaign has its own authorized structural editor."""
    return "/admin/campaign/settings/"


def fields(store, row=None, **changes):
    """Convert HTML checkbox/multiselect behavior rather than posting Python values."""
    values = (
        initial_fields(
            row.active_configuration.values if row else campaign()["values"],
            digest=store.active().digest,
        )
        | changes
    )
    # A browser posts the ticked Ministry leader roles (#922); the default
    # ones are ticked until someone changes them.
    values.setdefault("ministry_leader_roles", ["Chairperson", "Staff"])
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


def requested(response):
    """The change a confirmation's redirect names.

    Most editors lead to Change status (/changes/<id>/); Campaign and Parish
    settings answer with themselves, naming it in ``request`` (#532).
    """
    assert response.status_code == 302, response.content
    location = urlsplit(response["Location"])
    named = parse_qs(location.query).get("request")
    return UUID(named[0] if named else location.path.rstrip("/").rsplit("/", 1)[-1])


def apply(store, response):
    """Installation is a separate process boundary from accepting the web request."""
    row = ConfigurationChangeRequest.objects.get(pk=requested(response))
    receipt = install_request(store, request_id=row.pk, correlation_id=uuid4())
    assert receipt.state == "applied"


def test_new_campaign_address_redirects_and_records_nothing(auth_service, google):
    """New campaign is retired (decision 11): its address only redirects.

    With no current campaign it leads Home, otherwise to the current
    campaign's settings. A form left open on the old page, even one carrying
    a valid signed creation preview, is redirected without recording anything.
    """
    from .test_clone_views_postgresql import added_campaign, signed_preview

    browser, _ = signed_in()
    store = auth_service.store
    response = browser.get(NEW)
    assert response.status_code == 302 and response["Location"] == "/admin/"
    proposal = signed_preview(SALT, added_campaign())
    for response in (
        post(browser, NEW, fields(store)),
        post(browser, NEW, {"action": "confirm", "preview": proposal}),
    ):
        assert response.status_code == 302 and response["Location"] == "/admin/"
        assert response["Cache-Control"] == "no-store"
    assert not ConfigurationChangeRequest.objects.exists()
    assert not Campaign.objects.exists()
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    response = browser.get(NEW)
    assert response.status_code == 302 and response["Location"] == url(row)
    assert b"Campaign settings" in browser.get(url(row)).content


def test_signed_creation_preview_is_refused_by_campaign_settings(auth_service, google):
    """Campaign settings' shared confirmation never adds a campaign (rule 10).

    A creation preview signed before New campaign was retired shares Campaign
    settings' salt; posting it there is refused and records nothing, while an
    edit signed the same way is still accepted.
    """
    from .test_clone_views_postgresql import added_campaign, signed_preview

    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    requests = ConfigurationChangeRequest.objects.count()
    proposal = signed_preview(SALT, added_campaign())
    refused = post(browser, url(row), {"action": "confirm", "preview": proposal})
    assert refused.status_code == 410
    assert refused.json()["refusal"]["message"] == (
        "Creating another campaign is disabled."
    )
    assert ConfigurationChangeRequest.objects.count() == requests
    edit = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(row.pk),
            "values": {"name": "Renamed campaign"},
        }
    ]
    accepted = post(
        browser, url(row), {"action": "confirm", "preview": signed_preview(SALT, edit)}
    )
    apply(store, accepted)
    row.refresh_from_db()
    assert row.active_configuration.name == "Renamed campaign"
    assert Campaign.objects.count() == 1


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
        {"end_date": "2054-09-01"},
        {"state": "active"},
        {"ministry_duids": ["4"]},
        {"fund_duids": ["6"]},
        {"mode": "production"},
        {"name": ["First", "Second"]},
    ],
)
def test_invalid_and_hidden_values_do_not_create_intent(auth_service, google, changes):
    """A typed form and closed request parser reject controls that were not offered."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    requests = ConfigurationChangeRequest.objects.count()
    browser, _ = signed_in()
    response = post(browser, url(row), fields(store, row, **changes))
    assert response.status_code == 400
    assert ConfigurationChangeRequest.objects.count() == requests


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
    assert b"Review changes" not in response.content
    # A read-only page is not a step of any flow (#196).
    assert flow_steps(response.content) is None


def test_source_replacement_requires_fresh_preview(auth_service, google):
    """Catalog changes cannot silently change a reviewed Ministry/fund selection."""
    publish(source())
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    requests = ConfigurationChangeRequest.objects.count()
    browser, _ = signed_in()
    values = fields(store, row, ministry="on", ministry_duids=["4"])
    proposal = token(post(browser, url(row), values))
    publish(source())
    refused = post(browser, url(row), {"action": "confirm", "preview": proposal})
    assert refused.status_code == 409
    # Refused in place (#532): the page again, explained in its review region.
    assert "This preview is out of date." in region(refused.content)
    assert b'id="settings-form"' in refused.content
    assert ConfigurationChangeRequest.objects.count() == requests


def test_financial_and_ministry_catalogs_work_under_real_web_grants(
    auth_service, google
):
    """Runtime SELECT access is limited to public catalog payloads, not giving rows."""
    publish(source())
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    current = Campaign.objects.get()
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
    } | {"action": "preview", "ministry_leader_roles": ["Chairperson", "Staff"]}
    with task_login(ServiceRole.WEB):
        assert browser.get(url(current)).status_code == 200
        proposal = token(post(browser, url(current), values))
        response = post(
            browser, url(current), {"action": "confirm", "preview": proposal}
        )
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
    assert browser.get("/admin/campaign/settings/").status_code == 403


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
    assert browser.get(f"/admin/campaign/{uuid4()}/settings").status_code == 410
    assert browser.get(url(row) + "?extra=value").status_code == 400
    assert Client().get(url(row)).status_code == 403


def test_campaign_edit_cannot_strand_existing_initial_mail(auth_service, google):
    """A proposed window transfers to reconciliation without changing any data."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    response = post(browser, url(row), fields(store, row, start_date="2054-10-02"))
    assert response.status_code == 302
    assert "/admin/campaign/schedules/?" in response["Location"]
    assert "start_date=2054-10-02" in response["Location"]
    row.refresh_from_db()
    assert row.active_configuration.start_date.isoformat() == "2054-10-01"


def test_review_and_apply_stay_on_campaign_settings(auth_service, google):
    """Campaign settings reviews, applies and follows a change in place (#532).

    The review keeps its exact content beside the form, the step indicator
    follows each step, and the confirmed change's status is drawn on the
    page itself, polling Change status's passive read.
    """
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    page = browser.get(url(row))
    assert flow_steps(page.content) == (STEPS, "Make changes")
    assert b"data-in-place data-table-sync" in page.content
    review = post(browser, url(row), fields(store, row, name="Renamed in place"))
    assert flow_steps(review.content) == (STEPS, "Review")
    shown = region(review.content)
    assert "Review your changes" in shown and "Renamed in place" in shown
    response = post(browser, url(row), {"action": "confirm", "preview": token(review)})
    request_id = requested(response)
    assert response["Location"] == (f"{url(row)}?request={request_id}#settings-review")
    # Reading the change's status (the page's own quiet refresh once it is
    # applied) is passive: it never renews idle time, as Change status.
    activity = PortalSession.objects.get().last_activity_at
    status = browser.get(response["Location"])
    assert PortalSession.objects.get().last_activity_at == activity
    assert flow_steps(status.content) == (STEPS, "Apply")
    assert "Applying your change" in region(status.content)
    assert "?in_place=campaign_settings" in region(status.content)
    apply(store, response)
    row.refresh_from_db()
    assert row.active_configuration.name == "Renamed in place"
    assert "Applied: your change is saved" in region(
        browser.get(response["Location"]).content
    )


def test_a_refused_apply_keeps_the_reviewed_version(auth_service, google):
    """A confirmation refused because the settings changed elsewhere keeps the
    form at the reviewed version (#532 review): the next Review is refused
    too, until a reload brings the current settings, so it can never quietly
    propose undoing the other change."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    values = fields(store, row, name="Reviewed name")
    proposal = token(post(browser, url(row), values))
    parish = store.active().document()["sections"]["parish"][0]
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Changed elsewhere"},
            }
        ],
    )
    refused = post(browser, url(row), {"action": "confirm", "preview": proposal})
    assert refused.status_code == 409
    assert "This preview is out of date." in region(refused.content)
    assert digest(refused) == values["base_digest"] != store.active().digest
    again = post(browser, url(row), values | {"base_digest": digest(refused)})
    assert again.status_code == 409
    assert digest(browser.get(url(row))) == store.active().digest


def test_a_pending_campaign_change_keeps_its_reviewed_version(auth_service, google):
    """Until it is applied, Apply's answer draws the form at the version the
    change was reviewed at, not one committed elsewhere (#751 review)."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    values = fields(store, row, name="Reviewed name")
    proposal = token(post(browser, url(row), values))
    response = post(browser, url(row), {"action": "confirm", "preview": proposal})
    parish = store.active().document()["sections"]["parish"][0]
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Changed elsewhere"},
            }
        ],
    )
    page = browser.get(response["Location"], HTTP_X_REQUESTED_WITH="fetch")
    assert digest(page) == values["base_digest"] != store.active().digest
    # A full load uses the current version, so a Review is accepted.
    reloaded = browser.get(response["Location"])
    assert digest(reloaded) == store.active().digest
    again = post(browser, url(row), fields(store, row, name="After reload"))
    assert again.status_code == 200 and "Review your changes" in region(again.content)


def live_ministry_campaign(store):
    """A live campaign that asks about Ministries."""
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(row.pk),
                "values": {"modules": ["census", "ministry"]},
            }
        ],
    )
    assert result.state == "applied"
    command(Campaign.objects.get(), uuid4(), Action.ACTIVATE)
    return Campaign.objects.get()


def leader_post(browser, row, roles, *, version):
    """The live leader-role form's Review, as a browser posts it."""
    return post(
        browser,
        url(row),
        {
            "action": "preview",
            "editor": "leader_roles",
            "ministry_leader_roles": roles,
            "base_digest": version,
        },
    )


def test_a_live_campaign_changes_its_leader_roles_in_place(auth_service, google):
    """A live campaign keeps one editable leader-role form (#922), reviewed and
    applied in place under it; every other setting stays read-only.
    """
    store = auth_service.store
    row = live_ministry_campaign(store)
    browser, _ = signed_in()
    page = browser.get(url(row))
    body = page.content.decode()
    assert "read-only" in body
    assert 'name="editor" value="leader_roles"' in body
    # Review waits for a changed role, as on the other settings forms (#921).
    assert re.search(
        r'<form [^>]*data-require-change="base_digest"[^>]*>(?:\s*<input[^>]*>)*?'
        r'\s*<input type="hidden" name="editor" value="leader_roles">',
        body,
    )
    assert body.count('id="leader_ministry_leader_roles_0"') == 1
    assert "sees the reports and follow-up of each Ministry" in body
    for role in ("Chairperson", "Staff"):
        assert re.search(
            rf'value="{role}"[^>]*id="leader_ministry_leader_roles_\d+"[^>]*checked',
            body,
        ), role
    version = digest(page)
    for roles, message in (
        ([], "Choose at least one role."),
        (["Chairperson", "Staff"], "No settings have changed."),
        (["Pastor"], "Select a valid choice"),
    ):
        refused = leader_post(browser, row, roles, version=version)
        assert refused.status_code == 400 and message in refused.content.decode()
    review = leader_post(browser, row, ["Chairperson"], version=version)
    shown = region(review.content)
    assert "Ministry leader roles" in shown and "Chairperson, Staff" in shown
    response = post(browser, url(row), {"action": "confirm", "preview": token(review)})
    assert response["Location"].endswith("#settings-review")
    apply(store, response)
    row.refresh_from_db()
    assert row.active_configuration.values["ministry_leader_roles"] == ["Chairperson"]
    # The other settings stay locked whatever the browser posts.
    assert post(browser, url(row), fields(store, row, name="Locked")).status_code == 409


def test_a_draft_changes_its_leader_roles_with_its_other_settings(auth_service, google):
    """A draft edits the roles in its own form, with Ministry stewardship (#922)."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    body = browser.get(url(row)).content.decode()
    assert 'id="id_ministry_leader_roles_0"' in body
    assert 'name="editor"' not in body
    review = post(
        browser,
        url(row),
        fields(store, row, ministry=True, ministry_leader_roles=["Staff"]),
    )
    assert "Ministry leader roles" in region(review.content)
    confirmed = post(browser, url(row), {"action": "confirm", "preview": token(review)})
    apply(store, confirmed)
    row.refresh_from_db()
    assert row.active_configuration.values["ministry_leader_roles"] == ["Staff"]
    # A draft cannot use the live form.
    refused = leader_post(browser, row, ["Chairperson"], version=store.active().digest)
    assert refused.status_code == 409


def test_a_live_campaign_without_ministries_has_no_leader_roles(auth_service, google):
    """No leader-role form, and none accepted, when the campaign has no Ministries."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    assert "ministry" not in row.active_configuration.values["modules"]
    command(row, uuid4(), Action.ACTIVATE)
    browser, _ = signed_in()
    page = browser.get(url(row))
    assert 'name="editor"' not in page.content.decode()
    refused = leader_post(browser, row, ["Staff"], version=digest(page))
    assert refused.status_code == 409
