"""Families the form cannot open: the Administrator's list (#774)."""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.source.family_names import family_display_name

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import response_source
from .test_report_workspace_postgresql import read as get
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

LIST = "/admin/system/source-form/"
HEALTH = "/admin/system/health/"


def test_the_list_follows_the_snapshot_and_never_shows_a_value(
    response_service, google
):
    """A clean source lists nothing; a promoted malformed Member is listed by
    DUID and field only, on the list and as a count on System health; the
    view is audited with a count."""
    harness = response_service
    family = (
        FamilyCampaign.objects.filter(campaign=harness.campaign, portal_eligible=True)
        .values_list("family_duid", flat=True)
        .get()
    )
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, LIST)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert b"Every Family that can use the portal can open the form." in body
    _, body = get(browser, HEALTH)
    assert b"Every Family that can use the portal can open the Family form." in body
    assert LIST.encode() in body
    data = response_source()
    data.members[3].update(firstName="x" * 101, lastName="private\tvalue")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    # A new snapshot is a new cache key: the list follows it at once.
    response, body = get(browser, LIST)
    assert response.status_code == 200
    assert b"1 Family cannot open the Family form" in body
    assert f"<td>{family}</td><td>3</td><td>First name</td>".encode() in body
    # The surname alone, never "Surname, heads": a head's name is a Member
    # value and may be the refused one (here, Member 3's first name).
    family_name = family_display_name(data.families[1], "Unavailable Family")
    assert f'<th scope="row">{family_name}</th>'.encode() in body
    assert b"<td>Last name</td>" in body
    assert b"private" not in body and b"x" * 101 not in body
    _, body = get(browser, HEALTH)
    assert b"1 Family cannot open the Family form" in body
    assert get(browser, LIST + "?sort=value")[0].status_code == 400
    assert get(browser, LIST + "?other=1")[0].status_code == 400
    contexts = [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(event_type="source_form_viewed")
    ]
    assert {"outcome": "succeeded", "count": 1} in contexts
    assert all(set(context) == {"outcome", "count"} for context in contexts)


def test_staff_cannot_open_the_list(response_service, google):
    """Administrators only, as System health is."""
    store = response_service.service.store
    rule = address("staff@example.org", roles=("staff",))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "staff@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    assert get(browser, LIST)[0].status_code in {403, 404}
    assert not AuditEvent.objects.filter(event_type="source_form_viewed").exists()


def test_the_page_and_the_command_agree(response_service):
    """The page's rows are the command's findings, on the same data."""
    from parishkit.stewardship import source_form_check
    from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.reports import source_form

    from .test_background_grants_postgresql import task_login

    harness = response_service
    data = response_source()
    data.members[3].update(firstName="x" * 101, lastName="private\tvalue")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    source_form._cache.clear()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        command = source_form_check.scan()["findings"]
        with CampaignReadGuard(
            [harness.campaign.pk], authorize=lambda guard: None, abort=lambda: None
        ):
            page = source_form.blocked_families(harness.campaign.pk)["rows"]
    assert command
    assert [(row["family_duid"], row["member_duid"]) for row in page] == [
        (item["family_duid"], item["member_duid"]) for item in command
    ]
    assert [row["field"] for row in page] == [
        source_form.field_label(item["field"], item["kind"]) for item in command
    ]


def test_the_poll_never_scans_and_a_failed_check_says_so(
    response_service, google, monkeypatch
):
    """System health's 10-second status read never runs the scan; a page load
    whose check cannot run says it could not check, and the page still loads."""
    from parishkit.stewardship.accounts import source_form_views
    from parishkit.stewardship.campaigns.read_guards import ReadUnavailable

    browser, login = signed_in()
    assert login.status_code == 302
    calls = []

    def unavailable(campaign_id):
        """The scan cannot run now."""
        calls.append(campaign_id)
        raise ReadUnavailable("The source is being refreshed.")

    monkeypatch.setattr(source_form_views, "blocked_families", unavailable)
    response, _ = get(browser, HEALTH + "status")
    assert response.status_code == 200 and calls == []
    response, body = get(browser, HEALTH)
    assert response.status_code == 200 and len(calls) == 1
    assert b"could not be checked right now" in body
