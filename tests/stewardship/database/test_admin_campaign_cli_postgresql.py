"""``campaign show``, ``campaign preview`` and ``campaign confirm`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
go through Campaign settings' own form, Review and Apply routing, so a token
from either channel confirms on the other, for a draft's settings, a live
campaign's leader roles and end date, and its Ministries.
"""

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import parish_views
from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
from parishkit.stewardship.accounts.campaign_views import SALT
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.models import (
    Campaign,
    CampaignConfigurationIntent,
)
from parishkit.stewardship.deployment import ServiceRole

from .automation_builders import as_staff, paired
from .campaign_builders import add_draft, change
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import (
    fields,
    live_ministry_campaign,
    post,
    requested,
)
from .test_live_end_date_postgresql import digest as page_digest
from .test_live_end_date_postgresql import installed, live
from .test_live_ministries_postgresql import live_post as ministries_post
from .test_ministry_reports_postgresql import setup as ministry_setup
from .test_parish_views_postgresql import token
from .test_schedule_views_postgresql import setup as draft_with_reminder

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_campaign_confirm"
URL = "/admin/campaign/settings/"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


@pytest.fixture
def draft(admin):
    """The current Testing-mode draft campaign."""
    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    return Campaign.objects.get()


def show(admin, secret):
    """Run ``campaign show``."""
    return admin("campaign", "show", secret=secret)


def preview(admin, secret, settings, version=None):
    """Run ``campaign preview`` for the ``settings`` document member."""
    version = version or admin.service.store.active().digest
    return admin(
        "campaign",
        "preview",
        "--changes",
        json.dumps({"campaign": settings}),
        "--expected-version",
        version,
        secret=secret,
    )


def confirm(admin, secret, signed):
    """Run ``campaign confirm`` with ``signed``."""
    return admin("campaign", "confirm", "--token", signed, secret=secret)


def reviewed(admin, secret, settings):
    """A successful preview's result."""
    code, document, _ = preview(admin, secret, settings)
    assert code == 0, document
    return document["result"]


def refused(document, code):
    """The error document's code is ``code``, with no result."""
    assert document["error"]["code"] == code, document
    assert "result" not in document


def events():
    """How many ``campaign confirm`` events are recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def install(admin, confirmed):
    """Install the request a successful ``campaign confirm`` recorded."""
    assert confirmed["result"]["created"], confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"


def values(row):
    """The campaign's applied values."""
    return Campaign.objects.get(pk=row.pk).active_configuration.values


def test_show_reads_a_draft_and_records_nothing(admin, draft):
    """A read-only session reads every setting, all open on a draft."""
    _, reader, _ = paired(admin.service, scope="read-only")
    before = AuditEvent.objects.count()
    code, document, _ = show(admin, reader)
    assert code == 0, document
    assert AuditEvent.objects.count() == before
    result = document["result"]
    assert result["version"] == admin.service.store.active().digest
    assert result["campaign_id"] == str(draft.pk)
    assert result["editable"] and result["state"] == draft.state
    assert sorted(result["open"]) == sorted(result["settings"])
    saved = draft.active_configuration.values
    settings = result["settings"]
    assert settings["name"] == saved["name"]
    assert settings["end_date"] == saved["end_date"]
    assert settings["ministry_duids"] == saved["ministry_duids"]
    assert settings["census"] is ("census" in saved["modules"])
    assert "base_digest" not in settings


def test_preview_and_confirm_a_draft_rename(admin, draft):
    """The review names the stored change; confirming records the request
    and the command's event; installed, the name applies; a repeat returns
    the same request and records nothing new."""
    _, secret, _ = paired(admin.service)
    version = admin.service.store.active().digest
    requests = ConfigurationChangeRequest.objects.count()
    result = reviewed(admin, secret, {"name": "Renamed by the command"})
    signed = result.pop("preview")["token"]
    assert result == {
        "version": version,
        "campaign_id": str(draft.pk),
        "kind": "settings",
        "changes": [
            {
                "field": "name",
                "before": draft.active_configuration.values["name"],
                "after": "Renamed by the command",
            }
        ],
        "replanned": [],
        "ministries": None,
        "notes": [],
    }
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0
    code, confirmed, _ = confirm(admin, secret, signed)
    assert code == 0, confirmed
    request_id = confirmed["result"]["request"]["request_id"]
    assert events() == 1
    install(admin, confirmed)
    assert values(draft)["name"] == "Renamed by the command"
    code, again, _ = confirm(admin, secret, signed)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events() == 1
    assert ConfigurationChangeRequest.objects.count() == requests + 1


def test_tokens_confirm_on_either_channel(admin, draft):
    """A command preview confirms on the page; the page's own review token
    confirms on the command line, which records its event."""
    browser, secret, _ = paired(admin.service)
    signed = reviewed(admin, secret, {"name": "From the command"})["preview"]["token"]
    with task_login(ServiceRole.WEB):
        response = post(browser, URL, {"action": "confirm", "preview": signed})
    request_id = requested(response)
    assert events() == 0
    install_request(admin.service.store, request_id=request_id, correlation_id=uuid4())
    row = Campaign.objects.get()
    with task_login(ServiceRole.WEB):
        review = post(browser, URL, fields(admin.service.store, row, name="From page"))
    assert review.status_code == 200, review.content
    code, confirmed, _ = confirm(admin, secret, token(review))
    assert code == 0, confirmed
    install(admin, confirmed)
    assert values(draft)["name"] == "From page"
    assert events() == 1


def test_preview_refusals(admin, draft):
    """Another version is stale; no change, a field error, a field the page
    has not, a wrong JSON type and a malformed document are invalid;
    nothing is requested."""
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    name = draft.active_configuration.values["name"]
    code, document, _ = preview(admin, secret, {"name": "New"}, version="0" * 64)
    assert code == 1
    refused(document, "stale_version")
    code, document, _ = preview(admin, secret, {"name": name})
    assert code == 1
    refused(document, "invalid")
    assert "No settings have changed." in document["error"]["fields"][0]["message"]
    code, document, _ = preview(admin, secret, {"name": ""})
    assert code == 1
    refused(document, "invalid")
    assert document["error"]["fields"][0]["field"] == "campaign.name"
    code, document, _ = preview(admin, secret, {"colour": "red", "census": "yes"})
    assert code == 1
    refused(document, "invalid")
    assert [item["field"] for item in document["error"]["fields"]] == [
        "campaign.colour",
        "campaign.census",
    ]
    code, document, _ = admin(
        "campaign",
        "preview",
        "--changes",
        "[]",
        "--expected-version",
        admin.service.store.active().digest,
        secret=secret,
    )
    assert code == 1
    refused(document, "invalid")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_confirm_refuses_foreign_and_stale_tokens(admin, draft):
    """An altered token or another page's is invalid; another
    Administrator's is denied; one reviewed before the settings changed is
    stale; nothing is requested by the command."""
    _, secret, _ = paired(admin.service)
    signed = reviewed(admin, secret, {"name": "Reviewed"})["preview"]["token"]
    requests = ConfigurationChangeRequest.objects.count()
    code, document, _ = confirm(admin, secret, signed[:-2] + "xx")
    assert code == 1
    refused(document, "invalid")
    payload = signing.loads(signed, salt=SALT)
    code, document, _ = confirm(
        admin, secret, signing.dumps(payload, salt=parish_views.SALT)
    )
    assert code == 1
    refused(document, "invalid")
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=SALT,
    )
    code, document, _ = confirm(admin, secret, other)
    assert code == 1
    refused(document, "denied")
    store = admin.service.store
    parish = store.active().document()["sections"]["parish"][0]
    patch = [
        {
            "operation": "update",
            "section": "parish",
            "id": parish["id"],
            "values": {"name": "Changed elsewhere"},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    requests += 1
    code, document, _ = confirm(admin, secret, signed)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0


def test_a_live_campaign_changes_only_its_open_settings(admin):
    """A live campaign shows what is open; its leader roles are reviewed
    and confirmed alone; a locked setting is stale with its reason."""
    row = live_ministry_campaign(admin.service.store)
    _, secret, _ = paired(admin.service)
    code, document, _ = show(admin, secret)
    assert code == 0, document
    result = document["result"]
    assert not result["editable"]
    assert {"ministry_leader_roles", "ministry_duids"} <= set(result["open"])
    assert "name" not in result["open"]
    assert result["settings"]["ministry_leader_roles"] == ["Chairperson", "Staff"]
    code, document, _ = preview(admin, secret, {"name": "Locked now"})
    assert code == 1
    refused(document, "stale_version")
    result = reviewed(admin, secret, {"ministry_leader_roles": ["Chairperson"]})
    assert result["kind"] == "settings"
    assert result["changes"] == [
        {
            "field": "ministry_leader_roles",
            "before": ["Chairperson", "Staff"],
            "after": ["Chairperson"],
        }
    ]
    code, confirmed, _ = confirm(admin, secret, result["preview"]["token"])
    assert code == 0, confirmed
    install(admin, confirmed)
    assert values(row)["ministry_leader_roles"] == ["Chairperson"]
    assert events() == 1


def test_a_locked_check_box_is_refused_not_dropped(admin):
    """Unticking a locked module box is the page's lock refusal, alone or
    with an open change, never a review that silently drops it."""
    row = live_ministry_campaign(admin.service.store)
    assert "census" in values(row)["modules"]
    _, secret, _ = paired(admin.service)
    for settings in (
        {"census": False, "ministry_leader_roles": ["Chairperson"]},
        {"census": False},
    ):
        code, document, _ = preview(admin, secret, settings)
        assert code == 1, document
        refused(document, "stale_version")
    # Naming a locked setting at its saved value changes nothing and is no
    # refusal: only the open change is reviewed.
    result = reviewed(
        admin, secret, {"census": True, "ministry_leader_roles": ["Chairperson"]}
    )
    assert [item["field"] for item in result["changes"]] == ["ministry_leader_roles"]
    assert events() == 0


def test_a_drafts_dates_with_schedules_go_to_schedule_preview(admin):
    """With mail schedules, a draft's dates alone are refused naming
    schedule preview; with other settings, the page's own words."""
    draft_with_reminder(admin.service.store)
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    code, document, _ = preview(admin, secret, {"end_date": "2054-11-15"})
    assert code == 1
    refused(document, "invalid")
    [problem] = document["error"]["fields"]
    assert "schedule preview" in problem["message"]
    code, document, _ = preview(
        admin, secret, {"end_date": "2054-11-15", "name": "Renamed too"}
    )
    assert code == 1
    refused(document, "invalid")
    # The refusal is the end date's own error (#1330 item 1).
    [problem] = document["error"]["fields"]
    assert problem["field"] == "campaign.end_date"
    assert "on their own" in problem["message"]
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_page_signed_live_tokens_confirm_on_the_command_line(admin):
    """The page's own end-date review token, confirmed by ``campaign
    confirm``, binds its intent and records the page's event."""
    campaign = live(admin.service.store)
    browser, secret, _ = paired(admin.service)
    with task_login(ServiceRole.WEB):
        page = browser.get(URL)
        review = post(
            browser,
            URL,
            {
                "action": "preview",
                "end_date": "2054-11-15",
                "base_digest": page_digest(page),
            },
        )
    code, confirmed, _ = confirm(admin, secret, token(review))
    assert code == 0, confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    intent = CampaignConfigurationIntent.objects.get(request_id=request_id)
    assert (intent.action, intent.campaign_id) == ("edit_end", campaign.pk)
    assert AuditEvent.objects.filter(
        event_type="campaign_end_date_requested", subject_id=request_id
    ).exists()
    assert events() == 1


def test_a_page_signed_ministries_token_confirms_on_the_command_line(
    admin, response_service
):
    """The page's own Ministries review token, confirmed by ``campaign
    confirm``, records the page's campaign_ministries_requested."""
    ministry_setup(response_service)
    browser, secret, _ = paired(admin.service)
    with task_login(ServiceRole.WEB):
        review = ministries_post(browser, browser.get(URL), ["4"])
    code, confirmed, _ = confirm(admin, secret, token(review))
    assert code == 0, confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    audit = AuditContext.objects.get(event__event_type="campaign_ministries_requested")
    assert audit.event.subject_id == request_id
    assert events() == 1


def test_staff_are_denied_before_any_input_is_read(admin, draft, monkeypatch):
    """The role comes first (#1341): with ``-``, Staff are denied before
    standard input is read."""
    from parishkit.stewardship import admin_cli

    _, secret, _ = paired(admin.service)
    read = []
    real = admin_cli._input
    monkeypatch.setattr(
        admin_cli, "_input", lambda *args: read.append(args) or real(*args)
    )
    as_staff(monkeypatch)
    for argv in (
        ("campaign", "preview", "--changes", "-", "--expected-version", "a" * 64),
        ("campaign", "confirm", "--token", "-"),
    ):
        code, document, _ = admin(*argv, secret=secret, answer=b"{}")
        assert code == 1
        refused(document, "denied")
    assert read == [] and events() == 0


def test_a_live_end_date_confirms_as_the_page(admin):
    """A live end date is reviewed as the page reviews it, with the
    schedules it re-plans (none here); confirming binds its intent and records the
    page's campaign_end_date_requested with the command's event."""
    store = admin.service.store
    campaign = live(store)
    _, secret, _ = paired(admin.service)
    result = reviewed(admin, secret, {"end_date": "2054-11-15"})
    assert result["kind"] == "end_date"
    assert result["changes"] == [
        {"field": "end_date", "before": "2054-10-31", "after": "2054-11-15"}
    ]
    # A later end re-plans no saved mailing here.
    assert result["replanned"] == [] and len(result["notes"]) == 2
    code, confirmed, _ = confirm(admin, secret, result["preview"]["token"])
    assert code == 0, confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    intent = CampaignConfigurationIntent.objects.get(request_id=request_id)
    assert (intent.action, intent.campaign_id) == ("edit_end", campaign.pk)
    assert AuditEvent.objects.filter(
        event_type="campaign_end_date_requested", subject_id=request_id
    ).exists()
    assert events() == 1
    assert installed(store, request_id).state == "applied"
    campaign.refresh_from_db()
    assert campaign.active_configuration.end_date.isoformat() == "2054-11-15"


def test_a_live_ministries_change_records_its_audit(admin, response_service):
    """A live campaign's Ministries are reviewed with their impact and
    confirmed with the page's campaign_ministries_requested; with another
    change they are refused in the page's words."""
    harness = ministry_setup(response_service)
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(
        admin,
        secret,
        {"ministry_duids": [4], "ministry_leader_roles": ["Chairperson"]},
    )
    assert code == 1
    refused(document, "invalid")
    result = reviewed(admin, secret, {"ministry_duids": [4]})
    assert result["kind"] == "ministries"
    assert result["changes"] == [
        {"field": "ministry_duids", "before": [4, 9], "after": [4]}
    ]
    ministries = result["ministries"]
    assert ministries["added"] == []
    [removed] = ministries["removed"]
    assert (removed["duid"], removed["name"]) == (9, "Food pantry")
    code, confirmed, _ = confirm(admin, secret, result["preview"]["token"])
    assert code == 0, confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    audit = AuditContext.objects.get(event__event_type="campaign_ministries_requested")
    assert audit.event.subject_id == request_id
    assert audit.context["removed_ministry_duids"] == [9]
    assert events() == 1
    install(admin, confirmed)
    harness.campaign.refresh_from_db()
    assert harness.campaign.active_configuration.values["ministry_duids"] == [4]


def test_without_a_campaign_nothing_is_available(admin):
    """No current campaign: every command is not_available."""
    _, secret, _ = paired(admin.service)
    for code, document, _ in (
        show(admin, secret),
        preview(admin, secret, {"name": "x"}),
        confirm(admin, secret, "x"),
    ):
        assert code == 1
        refused(document, "not_available")
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0


def test_refused_sessions(admin, draft, monkeypatch):
    """A read-only session cannot change it; Staff are denied; an ended
    session is exit 5."""
    _, reader, _ = paired(admin.service, scope="read-only")
    _, secret, _ = paired(admin.service)
    signed = reviewed(admin, secret, {"name": "Never"})["preview"]["token"]
    requests = ConfigurationChangeRequest.objects.count()
    for code, document, _ in (
        preview(admin, reader, {"name": "Never"}),
        confirm(admin, reader, signed),
    ):
        assert code == 1
        refused(document, "denied")
    revoke(admin.service, secret)
    for code, document, _ in (
        show(admin, secret),
        preview(admin, secret, {"name": "Never"}),
        confirm(admin, secret, signed),
    ):
        assert code == 5
        refused(document, "session_ended")
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        show(admin, staff),
        preview(admin, staff, {"name": "Never"}),
        confirm(admin, staff, signed),
    ):
        assert code == 1
        refused(document, "denied")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0
