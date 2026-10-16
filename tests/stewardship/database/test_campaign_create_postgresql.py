"""Create the campaign: the first campaign, after system setup (#142).

Real Google sessions, YAML requests, the configuration installer, SQL guards
and a promoted ParishSoft snapshot. Creation is admitted only while the
deployment has never had a campaign; it is not a way to add a second one.
"""

import re
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.campaign_create_views import SALT
from parishkit.stewardship.accounts.campaign_views import SALT as SETTINGS_SALT
from parishkit.stewardship.accounts.content_forms import (
    applicable_slots,
    matches_default,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import Campaign

from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in, unguarded
from .campaign_builders import add_draft, change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_campaign_views_postgresql import apply, fields, post, requested
from .test_parish_views_postgresql import token
from .test_source_families_postgresql import (  # noqa: F401
    publish,
    source_singletons,
)

pytestmark = pytest.mark.django_db(transaction=True)
CREATE = "/admin/campaign/first/"


def new_fields(store):
    """Create the campaign's form posts: Campaign settings' without leader roles.

    A new campaign starts with the default Ministry leader roles (#922); only
    Campaign settings offers them, so this form refuses the field.
    """
    values = fields(store)
    values.pop("ministry_leader_roles")
    return values


def test_first_campaign_is_created_with_default_content(auth_service, google):
    """Review, confirm and apply one request that adds the draft and its text.

    The Review is a confirmation for the review dialog (#1356): one sentence
    and the number of default pages and emails, repeating none of the form's
    values. Apply's answer and the change's status are this page again,
    naming the request, whose region holds the status.
    """
    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    home = browser.get("/admin/").content
    assert f'href="{CREATE}"'.encode() in home
    response = browser.get(CREATE)
    assert response.status_code == 200 and b"Create the campaign" in response.content
    assert flow_steps(response.content) == (STEPS, "Make changes")
    preview = post(browser, CREATE, new_fields(store))
    fragment = preview.content.decode()
    assert "data-review-answer" in fragment and "<html" not in fragment
    assert (
        "This creates the campaign as a draft in Testing mode; no email is sent."
        in fragment
    )
    assert re.search(
        r"It starts with \d+ pages and emails in the default text", fragment
    )
    # None of the form's values is drawn again: they are on the page.
    values = new_fields(store)
    for name in ("name", "start_date", "end_date"):
        assert values[name] not in fragment, name
    assert "Current" not in fragment and "Proposed" not in fragment
    proposal = token(preview)
    assert not Campaign.objects.exists()
    assert not ConfigurationChangeRequest.objects.exists()
    accepted = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    request_id = requested(accepted)
    assert accepted["Location"] == (
        f"{CREATE}?request={request_id}#campaign-create-region"
    )
    assert (
        post(browser, CREATE, {"action": "confirm", "preview": proposal})["Location"]
        == accepted["Location"]
    )
    # Still pending: the region polls Change status, and offers no refresh
    # yet. The offer is drawn inside the live region, so it appears in place.
    pending = browser.get(accepted["Location"]).content.decode()
    assert "data-live-pending" in pending and "data-campaign-refresh" not in pending
    # The poll names no in-place page: once applied, the campaign exists, so
    # nothing refreshes this page (its follow-up is for settings pages only).
    status_url = reverse("admin:configuration_request", args=[request_id])
    assert f'data-live-url="{status_url}"' in pending
    # The region holds the status, not the form, which could only create a
    # second campaign or be refused once this change applies.
    assert 'id="campaign-create-region"' in pending
    assert 'id="settings-form"' not in pending
    apply(store, accepted)
    row = Campaign.objects.get()
    assert row.state == "draft" and not row.structural_locked
    assert SystemConfiguration.objects.get().current_campaign_id == row.pk
    # Once applied, the change's live status offers the campaign's full
    # refresh, posted to Refresh from ParishSoft's own action (#142): on this
    # page's region (a reload of the confirmation's answer, though the
    # campaign now exists), in the poll it reads, and on Change status, which
    # names Create the campaign but never links it (#196).
    for status in (
        browser.get(accepted["Location"]).content,
        browser.get(status_url).content,
    ):
        assert f'href="{CREATE}"'.encode() not in status
        assert b"Load the campaign's ParishSoft data" in status
        region = status.split(b'data-live-status="configuration"', 1)[1]
        assert b"data-campaign-refresh" in region.split(b"</section>", 1)[0]
        assert f'action="{reverse("admin:source_refresh")}"'.encode() in status
        assert b"data-live-follow" not in status
    # A full load of that address once the campaign exists draws no form,
    # which could only be refused now, and points to Campaign settings.
    reloaded = browser.get(accepted["Location"]).content
    assert b'id="settings-form"' not in reloaded
    assert f'href="{reverse("admin:campaign_settings")}"'.encode() in reloaded
    # An identical retry after the campaign exists still returns the receipt.
    late = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    assert late["Location"] == accepted["Location"]
    assert ConfigurationChangeRequest.objects.count() == 1
    document = store.active().document()["sections"]
    records = [
        record
        for record in document["content"]
        if record["values"]["campaign_id"] == str(row.pk)
    ]
    values = row.active_configuration.values
    assert sorted((r["values"]["kind"], r["values"]["slot"]) for r in records) == (
        sorted(applicable_slots(values))
    )
    assert records and all(matches_default(record["values"]) for record in records)
    # Once a campaign exists the page and Home no longer offer creation.
    assert browser.get(CREATE).status_code == 409
    assert f'href="{CREATE}"'.encode() not in browser.get("/admin/").content


def test_a_zone_other_than_the_parish_zone_is_the_campaign_zone(auth_service, google):
    """Any catalog zone may start the campaign, not only the parish's (#1330).

    The Administrator chose America/Kentucky/Louisville with a parish zone of
    America/New_York and was refused. A zone hours away from the parish's
    shows the dates are days in the chosen zone: the campaign opens at local
    midnight there, which is not the parish's midnight. The page offers the
    browser's zone through ``data-browser-default``, on the fresh form only.
    """
    from datetime import date, datetime, time
    from zoneinfo import ZoneInfo

    from ..configuration_factory import PARISH_TIMEZONE

    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    page = browser.get(CREATE).content.decode()
    menu = re.search(r'<select name="timezone"[^>]*>', page)[0]
    assert "data-browser-default" in menu
    zone = "America/Los_Angeles"
    assert zone != PARISH_TIMEZONE
    preview = post(browser, CREATE, new_fields(store) | {"timezone": zone})
    assert preview.status_code == 200, preview.content
    accepted = post(browser, CREATE, {"action": "confirm", "preview": token(preview)})
    apply(store, accepted)
    projection = Campaign.objects.get().active_configuration
    assert projection.timezone == projection.values["timezone"] == zone
    start = date.fromisoformat(projection.values["start_date"])
    assert projection.starts_at == datetime.combine(start, time(), ZoneInfo(zone))
    assert projection.starts_at != datetime.combine(
        start, time(), ZoneInfo(PARISH_TIMEZONE)
    )
    parish = store.active().document()["sections"]["parish"][0]["values"]
    assert parish["timezone"] == PARISH_TIMEZONE


def test_a_refused_form_keeps_the_posted_zone(auth_service, google):
    """A form drawn again keeps the posted zone; the browser does not reset it."""
    publish(source())
    browser, _ = signed_in()
    refused = post(
        browser,
        CREATE,
        new_fields(auth_service.store) | {"timezone": "America/Chicago", "name": ""},
    )
    assert refused.status_code == 400
    menu = re.search(r'<select name="timezone"[^>]*>', refused.content.decode())[0]
    assert "data-browser-default" not in menu
    assert b'<option value="America/Chicago" selected>' in refused.content
    # A name that is not a catalog zone is still refused at the field.
    bogus = post(
        browser, CREATE, new_fields(auth_service.store) | {"timezone": "Mars/Base"}
    )
    assert bogus.status_code == 400 and not Campaign.objects.exists()


def test_creation_is_refused_once_any_campaign_exists(auth_service, google):
    """A preview signed with no campaign cannot add a second one later."""
    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, new_fields(store)))
    add_draft(store, store.active(), uuid4())
    requests = ConfigurationChangeRequest.objects.count()
    # Both are refused with the closed refusal the review dialog shows, and
    # no signed review.
    for refused in (
        post(browser, CREATE, {"action": "confirm", "preview": proposal}),
        post(browser, CREATE, new_fields(store)),
    ):
        assert refused.status_code == 409
        content = refused.content.decode()
        assert "The campaign can't be created right now." in content
        assert 'name="preview"' not in content
    assert ConfigurationChangeRequest.objects.count() == requests
    assert Campaign.objects.count() == 1


def test_a_field_error_is_the_page_again(auth_service, google):
    """A Review refused for the form's values answers the page again (400),
    with the field's error and the summary, and signs nothing, so the review
    dialog stays shut and the errors stay on the form."""
    publish(source())
    browser, _ = signed_in()
    refused = post(browser, CREATE, new_fields(auth_service.store) | {"name": ""})
    assert refused.status_code == 400
    content = refused.content.decode()
    assert 'id="settings-form"' in content and "data-error-summary" in content
    assert 'aria-invalid="true"' in content
    assert 'name="preview"' not in content and "data-review-answer" not in content
    assert not ConfigurationChangeRequest.objects.exists()


def test_creation_needs_loaded_parish_data(auth_service, google):
    """Without a promoted ParishSoft snapshot there is nothing to choose from.
    A person loading the page sees it refused under its own heading, not the
    out-of-date-form heading of its 409 status."""
    browser, _ = signed_in()
    assert browser.get(CREATE).status_code == 409
    page = browser.get(CREATE, HTTP_ACCEPT="text/html", HTTP_SEC_FETCH_MODE="navigate")
    assert page.status_code == 409
    body = page.content.decode()
    assert "<h1>Create the campaign is unavailable</h1>" in body
    assert "This information changed" not in body
    assert "The campaign can&#x27;t be created right now." in body
    assert f'href="{reverse("admin:campaign_settings")}"' in body
    assert f'href="{CREATE}"'.encode() not in browser.get("/admin/").content


def test_creation_and_settings_previews_cannot_be_swapped(auth_service, google):
    """Each page signs with its own salt, so neither accepts the other's review."""
    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, new_fields(store)))
    assert SALT != SETTINGS_SALT
    refused = post(
        browser, "/admin/campaign/settings/", {"action": "confirm", "preview": proposal}
    )
    assert refused.status_code >= 400
    assert not ConfigurationChangeRequest.objects.exists()


def test_an_ordinary_change_offers_no_campaign_refresh(auth_service, google):
    """Only the change that created the current campaign offers its refresh."""
    from parishkit.stewardship.campaigns.models import Campaign as Row

    from .test_campaign_views_postgresql import url

    publish(source())
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Row.objects.get()
    browser, _ = signed_in()
    proposal = token(post(browser, url(row), fields(store, row, name="Renamed")))
    accepted = post(browser, url(row), {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    status_url = reverse("admin:configuration_request", args=[requested(accepted)])
    # The ordinary status renders (a regression check for #786's H1) and
    # offers no campaign refresh: on Change status, and in Campaign settings'
    # own in-place region (#532).
    for response in (browser.get(status_url), browser.get(accepted["Location"])):
        assert response.status_code == 200
        assert b"Applied: your change is saved" in response.content
        assert b"data-campaign-refresh" not in response.content
        assert b"Load the campaign's ParishSoft data" not in response.content
    # Change status answers only safe methods: a POST with a valid CSRF token
    # is refused by the method guard itself.
    assert post(browser, status_url, {}).status_code == 405


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_only_an_administrator_may_create_the_campaign(auth_service, google, role):
    """Other roles can neither open the page nor post to it."""
    publish(source())
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
    requests = ConfigurationChangeRequest.objects.count()
    assert browser.get(CREATE).status_code == 403
    assert post(browser, CREATE, new_fields(store)).status_code == 403
    assert f'href="{CREATE}"'.encode() not in browser.get("/admin/").content
    assert ConfigurationChangeRequest.objects.count() == requests


@pytest.mark.parametrize("lost", ["capability", "identity"])
def test_an_identical_retry_still_needs_current_access(
    auth_service, google, monkeypatch, lost
):
    """confirm_intent checks capability and identity before an existing key.

    An identical retry returns its receipt even when the page's own scope
    would now refuse (#786), but never for an Administrator who has lost the
    capability, or for a session that now belongs to someone else.
    """
    from types import SimpleNamespace

    from parishkit.stewardship.accounts import admin_editing

    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, new_fields(store)))
    accepted = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    # Only confirm_intent's own recheck (a read-only re-read of the session)
    # sees the change; the page's admission before it still passes.
    real_admin, real_allows = admin_editing.authenticated_admin, admin_editing.allows
    rechecked = []

    def recheck(*args, **kwargs):
        """The session re-read; inside admit() it may belong to someone else."""
        actor = real_admin(*args, **kwargs)
        if kwargs.get("read_only"):
            rechecked.append(True)
            if lost == "identity":
                return SimpleNamespace(**vars(actor) | {"identity": uuid4()})
        return actor

    def capability(actor, wanted):
        """The capability, lost by the time admit() rechecks it."""
        return (
            False if lost == "capability" and rechecked else real_allows(actor, wanted)
        )

    monkeypatch.setattr(admin_editing, "authenticated_admin", recheck)
    monkeypatch.setattr(admin_editing, "allows", capability)
    retry = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    assert rechecked, "the retry reached confirm_intent's recheck"
    assert retry.status_code == 403
    assert ConfigurationChangeRequest.objects.count() == 1


@pytest.mark.parametrize("state", ["restore_review", "production"])
def test_creation_is_refused_outside_testing_or_during_restore_review(
    auth_service, google, state
):
    """Production mode refuses the page, its review and its confirmation;
    a restore review closes the Admin portal before the page is reached.
    Either way nothing is recorded, and in Production Home offers no
    creation."""
    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, new_fields(store)))
    with unguarded():
        if state == "production":
            SystemConfiguration.objects.update(mode="production")
        else:
            SystemConfiguration.objects.update(restore_review_required=True)
    responses = (
        browser.get(CREATE),
        post(browser, CREATE, new_fields(store)),
        post(browser, CREATE, {"action": "confirm", "preview": proposal}),
    )
    for response in responses:
        if state == "production":
            # The page, or the JSON answer to a plain request, carries the
            # refusal's explanation.
            assert response.status_code == 409
            assert "Create the campaign is available once" in (
                response.content.decode()
            )
        else:
            # A load is sent to the maintenance page; a post is refused
            # whole (503) before any view runs.
            assert response.status_code in {302, 503}
    # The admission itself refuses too, whatever the portal's own gate does.
    from parishkit.stewardship.campaigns.single_campaign import (
        first_campaign_admitted,
        first_campaign_offer,
    )

    runtime_row = SystemConfiguration.objects.get()
    assert not first_campaign_admitted(runtime_row)
    assert first_campaign_offer(runtime_row) is None
    assert not ConfigurationChangeRequest.objects.exists()
    assert not Campaign.objects.exists()
    if state == "production":
        home = browser.get("/admin/").content
        assert f'href="{CREATE}"'.encode() not in home
        assert b"No campaign yet" not in home


def test_campaign_work_holds_are_read_from_their_rows(auth_service, google):
    """``first_campaign_held`` reads both holds: a running work gate and an
    unfinished Testing cleanup. Each needs a campaign row, which on its own
    already refuses creation; the hold check is defence in depth."""
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )
    from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
    from parishkit.stewardship.campaigns.single_campaign import first_campaign_held

    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    assert not first_campaign_held()
    with unguarded():
        gate = CampaignWorkGate.objects.create(
            campaign_id=row.pk,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="running",
        )
    assert first_campaign_held()
    with unguarded():
        gate.delete()
        CampaignCredentialState.objects.update_or_create(
            campaign_id=row.pk, defaults={"go_live_gate": True}
        )
    assert first_campaign_held()


@pytest.mark.parametrize("hold", ["work_gate", "cleanup"])
def test_a_campaign_work_hold_refuses_creation(auth_service, google, monkeypatch, hold):
    """While campaign work or a Testing cleanup holds edits, the page, its
    review and its confirmation refuse, and Home shows Create the campaign
    unavailable with the reason. Both holds read as the editing state's
    ``held`` item (``campaign_views._state``), forced here because a real
    hold needs a campaign row (see the test above)."""
    from parishkit.stewardship.accounts import campaign_create_views
    from parishkit.stewardship.campaigns import single_campaign

    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, new_fields(store)))
    real_state = campaign_create_views._state

    def held(service):
        """The editing state with its hold set, as either hold sets it."""
        configuration, campaigns, source_row, _, fingerprint = real_state(service)
        return configuration, campaigns, source_row, True, fingerprint + hold

    monkeypatch.setattr(campaign_create_views, "_state", held)
    monkeypatch.setattr(single_campaign, "first_campaign_held", lambda: True)
    assert browser.get(CREATE).status_code == 409
    for refused in (
        post(browser, CREATE, new_fields(store)),
        post(browser, CREATE, {"action": "confirm", "preview": proposal}),
    ):
        assert refused.status_code == 409
    assert not ConfigurationChangeRequest.objects.exists()
    home = browser.get("/admin/").content.decode()
    assert "background campaign work now running finishes" in home
    assert f'href="{CREATE}"' not in home
    assert 'aria-describedby="create-campaign-reason"' in home


def test_home_asks_for_parish_data_before_creation(auth_service, google):
    """With no promoted snapshot, Home says to load ParishSoft data first and
    shows Create the campaign unavailable, not the #145 text."""
    browser, _ = signed_in()
    home = browser.get("/admin/").content.decode()
    assert "Load ParishSoft data before creating the campaign" in home
    assert "single-campaign change (#145)" not in home
    assert f'href="{CREATE}"' not in home
    publish(source())
    assert f'href="{CREATE}"' in browser.get("/admin/").content.decode()


def test_two_creations_confirmed_together_apply_one(auth_service, google):
    """Two reviews confirmed before either applies are both recorded; the
    installer applies one and fails the other, so one campaign exists. The
    failed change's page says a campaign already exists, not that it was
    created."""
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )

    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    first = token(post(browser, CREATE, new_fields(store)))
    second = token(post(browser, CREATE, new_fields(store)))
    accepted = [
        post(browser, CREATE, {"action": "confirm", "preview": proposal})
        for proposal in (first, second)
    ]
    assert ConfigurationChangeRequest.objects.count() == 2
    states = [
        install_request(
            store, request_id=requested(response), correlation_id=uuid4()
        ).state
        for response in accepted
    ]
    assert states == ["applied", "failed"]
    row = Campaign.objects.get()
    assert SystemConfiguration.objects.get().current_campaign_id == row.pk
    applied = browser.get(accepted[0]["Location"]).content.decode()
    failed = browser.get(accepted[1]["Location"]).content.decode()
    assert "The campaign has been created." in applied
    assert "A campaign already exists." in failed
    assert "The campaign has been created." not in failed
    assert 'id="settings-form"' not in failed


def test_a_failed_change_with_no_campaign_links_back_to_the_page(auth_service, google):
    """A reload of a failed creation's address, with no campaign, has a way back.

    The address draws only the change's status, so without a link the failed
    status would be a dead end (review of #1357).
    """
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )

    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, new_fields(store)))
    accepted = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    # Any other configuration change made meanwhile leaves the creation on a
    # stale base, so it fails when it installs and no campaign is created.
    version = store.active()
    parish = version.document()["sections"]["parish"][0]
    change(
        store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Changed Parish"},
            }
        ],
    )
    state = install_request(
        store, request_id=requested(accepted), correlation_id=uuid4()
    ).state
    assert state == "failed" and not Campaign.objects.exists()
    failed = browser.get(accepted["Location"]).content.decode()
    assert "A campaign already exists." not in failed
    assert f'href="{CREATE}"' in failed


def test_local_seeder_creates_the_campaign_through_the_page(auth_service, google):
    """The seeder's campaign step (#142) uses this page's form, review and Apply.

    One request adds the draft with its default text; once a campaign exists
    the seeder is refused, as the page is.
    """
    from datetime import date

    from parishkit.stewardship.accounts.admin_session_store import SessionStore
    from parishkit.stewardship.accounts.authentication import runtime
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )
    from parishkit.stewardship.local import seed_web

    publish(source())
    browser, _ = signed_in()
    request = seed_web.new_request(
        session=SessionStore(session_key=browser.cookies["pk_admin"].value)
    )
    dates = (date(2054, 10, 10), date(2054, 11, 9))
    receipt = seed_web.create_campaign(request, runtime(), dates)
    row = ConfigurationChangeRequest.objects.get()
    assert row.pk == receipt.request_id
    applied = install_request(
        auth_service.store, request_id=row.pk, correlation_id=uuid4()
    )
    assert applied.state == "applied"
    campaign = Campaign.objects.get()
    assert SystemConfiguration.objects.get().current_campaign_id == campaign.pk
    values = campaign.active_configuration.values
    assert values["modules"] == ["census", "financial", "ministry"]
    assert values["start_date"] == "2054-10-10"
    document = auth_service.store.active().document()["sections"]
    slots = {
        (record["values"]["kind"], record["values"]["slot"])
        for record in document["content"]
        if record["values"]["campaign_id"] == str(campaign.pk)
    }
    assert slots == set(applicable_slots(values))
    with pytest.raises(seed_web.SeedWebRefused):
        seed_web.create_campaign(request, runtime(), dates)
