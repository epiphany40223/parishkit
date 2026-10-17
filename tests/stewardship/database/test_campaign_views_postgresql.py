"""Draft editor uses real Google sessions, YAML requests, SQL guards and source."""

import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from django.test import Client

from parishkit.stewardship.accounts.campaign_forms import initial_fields
from parishkit.stewardship.accounts.campaign_views import SALT
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.workspace_views import fund_names

from ..campaign_factory import campaign, financial
from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in
from .campaign_builders import add_draft, change, command
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_parish_views_postgresql import digest, token
from .test_source_families_postgresql import (
    publish,
    source_singletons,  # noqa: F401
)

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


def test_new_campaign_address_is_gone_and_records_nothing(auth_service, google):
    """New campaign is retired (decision 11) and its address is 404 (#864).

    A form left open on the old page, even one carrying a valid signed
    creation preview, records nothing.
    """
    from .test_clone_views_postgresql import added_campaign, signed_preview

    browser, _ = signed_in()
    store = auth_service.store
    assert browser.get(NEW).status_code == 404
    proposal = signed_preview(SALT, added_campaign())
    for response in (
        post(browser, NEW, fields(store)),
        post(browser, NEW, {"action": "confirm", "preview": proposal}),
    ):
        assert response.status_code == 404
    assert not ConfigurationChangeRequest.objects.exists()
    assert not Campaign.objects.exists()


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
    # The financial periods and share options are long settings: the full
    # review's table, under the dialog's own title (#1356).
    assert b"<table" in response.content
    assert b"data-review-title" not in response.content
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
    response = browser.get(url(row))
    assert response.status_code == 200
    body = response.content.decode()
    # One editor (#1119): no read-only copy, every setting once, and the one
    # Save (#1356). Only the end date stays open once live (#912); every
    # other setting is disabled where it stands, its reason in its tip.
    assert "read-only" not in body and 'id="end-date"' not in body
    assert body.count(">Save</button>") == 1
    assert body.count('name="name"') == 1
    assert re.search(r'<input type="text" name="name"[^>]*disabled', body)
    assert re.search(r'<input type="date" name="end_date"[^>]*>', body)
    assert not re.search(r'<input type="date" name="end_date"[^>]*disabled', body)
    assert "scheduled or live in Production" in body
    assert flow_steps(response.content) == (STEPS, "Make changes")
    # A locked setting never changes, whatever is posted: a changed value
    # for it is refused with the reason, and nothing is recorded.
    locked = post(
        browser,
        url(row),
        {
            "action": "preview",
            "name": "Locked",
            "end_date": row.active_configuration.values["end_date"],
            "base_digest": digest(response),
        },
    )
    assert locked.status_code == 409
    assert locked.json()["refusal"]["message"] == LOCKED_NOW


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
    # Refused in the review dialog (#1119): the server's words, as JSON.
    assert refused.json()["refusal"]["message"] == "This preview is out of date."
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


def test_a_selection_gone_from_parishsoft_shows_its_last_name(auth_service, google):
    """A saved Ministry or fund an earlier ParishSoft refresh had, but the
    current one lacks, reads by its name in the latest promoted snapshot
    that held it, never its DUID (#1123). Renamed A, B, then A again, it
    reads A, though the version row for A is the older one."""
    store = auth_service.store

    def earlier(ministry, fund):
        """The standard source plus Ministry 999 and fund 998 so named."""
        data = source()
        data.ministry_types[999] = {"id": 999, "name": ministry}
        data.funds[998] = {
            "fundId": 998,
            "name": fund,
            "active": True,
            "fundStartDate": "2026-01-01T00:00:00",
        }
        return data

    publish(earlier("Old outreach", "Old fund"))
    publish(earlier("Renamed outreach", "Renamed fund"))
    publish(earlier("Old outreach", "Old fund"))
    publish(source())
    row = campaign(
        modules=["financial", "ministry"],
        ministry_duids=[999],
        financial=financial(fund_duids=[998], comparison_fund_duids=[998]),
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
    assert page.status_code == 200
    content = page.content.decode()
    assert "Old outreach (no longer in ParishSoft; retained selection)" in content
    assert "Old fund (no longer in ParishSoft; retained selection)" in content
    assert "Renamed" not in content
    assert "Ministry 999" not in content and "Fund 998" not in content


def test_participation_names_each_periods_funds(auth_service):
    """Participation's financial periods name their funds from the
    statistics' own snapshot, never their DUIDs (#1123)."""
    snapshot = publish(source())

    def period(*funds):
        """One financial period holding these fund DUIDs."""
        return SimpleNamespace(funds=funds)

    statistics = SimpleNamespace(
        source_id=snapshot.pk,
        financial=SimpleNamespace(upcoming=period(9), comparison=period(9, 77)),
    )
    with task_login(ServiceRole.WEB):
        names = fund_names(statistics)
    assert names == {
        "upcoming": ["Offertory"],
        "comparison": ["Offertory", "Unavailable fund"],
    }
    assert fund_names(SimpleNamespace(source_id=None, financial=None)) is None


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
        # Never shown by DUID (#1123); no name is borrowed for an ID the
        # tenant's data never had.
        assert b"Unavailable Ministry (retained selection)" in page.content
        assert b"Unavailable fund (retained selection)" in page.content
        assert b"999 (" not in page.content and b"998 (" not in page.content
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


def test_review_and_apply_in_the_review_dialog(auth_service, google):
    """Campaign settings reviews in the shared review dialog (#1119, #750).

    Review answers the dialog's fragment, never a page; Apply records the
    change and answers with its Change status, which the dialog follows.
    """
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    page = browser.get(url(row))
    body = page.content.decode()
    assert flow_steps(page.content) == (STEPS, "Make changes")
    # A Testing draft is in the campaign wizard, whose Save and continue
    # saves directly (#1404 PR 4, test_campaign_direct_save_postgresql);
    # the page's Review and Apply, used outside the wizard, stay as they are.
    assert "data-direct-save" in body and ">Save</button>" not in body
    assert '<dialog id="campaign-review"' in body
    assert "data-in-place data-table-sync" not in body
    requests = ConfigurationChangeRequest.objects.count()
    review = post(browser, url(row), fields(store, row, name="Renamed in dialog"))
    shown = review.content.decode()
    assert review.status_code == 200 and "data-review-answer" in shown
    assert "<html" not in shown and "Renamed in dialog" in shown
    assert 'data-review-done="Campaign settings changed."' in shown
    # One short setting changed: a compact review (#1356), its one line
    # naming the old name and the new, and nothing recorded by the Review.
    assert 'data-review-title="Save this change?"' in shown
    lines = re.findall(r"<li>(.*?)</li>", shown, flags=re.S)
    assert len(lines) == 1
    assert lines[0].startswith('<span class="compact-review-field">Campaign name:')
    assert lines[0].endswith(" Renamed in dialog") and "<table" not in shown
    assert ConfigurationChangeRequest.objects.count() == requests
    response = post(browser, url(row), {"action": "confirm", "preview": token(review)})
    request_id = requested(response)
    assert urlsplit(response["Location"]).path == f"/admin/changes/{request_id}/"
    apply(store, response)
    row.refresh_from_db()
    assert row.active_configuration.name == "Renamed in dialog"
    # The page the dialog redraws from shows the applied value.
    assert 'value="Renamed in dialog"' in browser.get(url(row)).content.decode()


def test_a_refused_review_shows_its_errors_on_the_form(auth_service, google):
    """A Review refused for the values posted is the page again (status 400)
    with each error at its field and the summary, which the page script
    marks on the form; the dialog stays shut (#750)."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    refused = post(browser, url(row), fields(store, row, name=""))
    assert refused.status_code == 400
    body = refused.content.decode()
    assert 'id="settings-form"' in body and "data-error-summary" in body
    assert 'id="id_name_error"' in body


def test_a_refused_apply_keeps_the_reviewed_version(auth_service, google):
    """A confirmation refused because the settings changed elsewhere is shown
    in the dialog and leaves the form at the reviewed version: the next
    Review from it is refused too, until a reload brings the current
    settings, so it can never quietly propose undoing the other change."""
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
    assert refused.json()["refusal"]["message"] == "This preview is out of date."
    again = post(browser, url(row), values)
    assert again.status_code == 409
    assert digest(browser.get(url(row))) == store.active().digest


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


def live_post(browser, row, roles, *, version, end_date=None):
    """A live campaign's Review, as a browser posts it: only the open fields.

    The locked settings are disabled, so a browser never sends them; the end
    date is sent whenever it is open, at the saved date unless changed.
    """
    data = {"action": "preview", "ministry_leader_roles": roles, "base_digest": version}
    if end_date is not None:
        data["end_date"] = end_date
    return post(browser, url(row), data)


def test_a_live_campaign_changes_its_leader_roles_on_the_page(auth_service, google):
    """A live campaign's leader roles stay open (#922) in the one editor
    (#1119), reviewed in the dialog; every other setting stays locked.
    """
    store = auth_service.store
    row = live_ministry_campaign(store)
    browser, _ = signed_in()
    page = browser.get(url(row))
    body = page.content.decode()
    assert "read-only" not in body and 'name="editor"' not in body
    # Review waits for a changed role, as on the other settings forms (#921),
    # and says why while it waits, with the shared hint (#924).
    assert 'data-require-change="base_digest"' in body
    assert "Change a setting to save it." in body
    assert body.count('id="id_ministry_leader_roles_0"') == 1
    assert "sees the reports and follow-up of each Ministry" in body
    for role in ("Chairperson", "Staff"):
        tag = re.search(rf'<input [^>]*value="{role}"[^>]*>', body).group(0)
        assert "checked" in tag and "disabled" not in tag, role
    # The Ministry list is open here too (#1119); the name stays locked.
    assert not re.search(r'<select name="ministry_duids"[^>]*disabled', body)
    assert re.search(r'<input type="text" name="name"[^>]*disabled', body)
    version = digest(page)
    end = row.active_configuration.values["end_date"]
    open_end = 'name="end_date"' in body and not re.search(
        r'<input type="date" name="end_date"[^>]*disabled', body
    )
    saved_end = end if open_end else None
    refused = live_post(browser, row, [], version=version, end_date=saved_end)
    assert refused.status_code == 400 and "Choose at least one role." in (
        refused.content.decode()
    )
    unchanged = live_post(
        browser, row, ["Chairperson", "Staff"], version=version, end_date=saved_end
    )
    assert unchanged.status_code == 400
    assert unchanged.json()["refusal"]["message"] == "No settings have changed."
    invalid = live_post(browser, row, ["Pastor"], version=version, end_date=saved_end)
    assert invalid.status_code == 400 and "Select a valid choice" in (
        invalid.content.decode()
    )
    review = live_post(
        browser, row, ["Chairperson"], version=version, end_date=saved_end
    )
    # A second Review of the same version, refused below once the first is
    # applied.
    later = live_post(browser, row, ["Staff"], version=version, end_date=saved_end)
    shown = review.content.decode()
    assert "data-review-answer" in shown
    assert "Ministry leader roles" in shown and "Chairperson, Staff" in shown
    # A live campaign's leader roles alone: a compact review (#1356).
    assert 'id="campaign-review-summary"' in shown and "<table" not in shown
    response = post(browser, url(row), {"action": "confirm", "preview": token(review)})
    apply(store, response)
    row.refresh_from_db()
    assert row.active_configuration.values["ministry_leader_roles"] == ["Chairperson"]
    stale = post(browser, url(row), {"action": "confirm", "preview": token(later)})
    assert stale.status_code == 409
    assert stale.json()["refusal"]["message"] == "This preview is out of date."


def test_a_draft_changes_its_leader_roles_with_its_other_settings(auth_service, google):
    """A draft edits the roles with its other settings and Ministry stewardship."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    body = browser.get(url(row)).content.decode()
    assert 'id="id_ministry_leader_roles_0"' in body
    review = post(
        browser,
        url(row),
        fields(store, row, ministry=True, ministry_leader_roles=["Staff"]),
    )
    assert "Ministry leader roles" in review.content.decode()
    confirmed = post(browser, url(row), {"action": "confirm", "preview": token(review)})
    apply(store, confirmed)
    row.refresh_from_db()
    assert row.active_configuration.values["ministry_leader_roles"] == ["Staff"]


def test_a_live_campaign_without_ministries_has_no_leader_roles(auth_service, google):
    """No open leader roles, and none accepted, when there are no Ministries."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    assert "ministry" not in row.active_configuration.values["modules"]
    command(row, uuid4(), Action.ACTIVATE)
    browser, _ = signed_in()
    page = browser.get(url(row))
    body = page.content.decode()
    assert not re.search(r'name="ministry_leader_roles"(?![^>]*disabled)[^>]*>', body)
    requests = ConfigurationChangeRequest.objects.count()
    end = row.active_configuration.values["end_date"]
    refused = live_post(browser, row, ["Staff"], version=digest(page), end_date=end)
    assert refused.status_code in (400, 409)
    assert ConfigurationChangeRequest.objects.count() == requests


LOCKED_NOW = "Some settings you changed can no longer change now."


def test_a_draft_locked_after_loading_refuses_its_review(auth_service, google):
    """A draft that goes live between loading the page and its Review is
    refused (409) with the reason, never reviewed with its changes quietly
    dropped (#1119 review); nothing is recorded."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    page = browser.get(url(row))
    data = fields(store, row, name="Changed before going live")
    command(row, uuid4(), Action.ACTIVATE)
    requests = ConfigurationChangeRequest.objects.count()
    refused = post(browser, url(row), data | {"base_digest": digest(page)})
    assert refused.status_code == 409
    refusal = refused.json()["refusal"]
    assert refusal["message"] == LOCKED_NOW
    assert "scheduled or live in Production" in refusal["fix"]
    assert ConfigurationChangeRequest.objects.count() == requests
