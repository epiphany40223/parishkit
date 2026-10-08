"""Open form's hand-off and Staff-entered responses (#529), over HTTP.

The campaign is ``live_response_service``: activated in Production, the
corpus Family (DUID 1) signed in with its own live code. An Administrator
opens the Family form for it through the hand-off, which is single-use,
bound to the Admin's browser and session, and never carries the code or the
secret in a URL; a response submitted in that session is marked as entered
by Staff on the timeline, the directory and the submitted list, never with
the Admin's name. Requests run as the restricted web login.
"""

import re
from dataclasses import replace

import pytest
from django.test import Client

from parishkit.stewardship.accounts import assisted_entry
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilySession,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import Submission

from .auth_builders import signed_in, unguarded
from .test_background_grants_postgresql import task_login
from .test_report_workspace_postgresql import read as get
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)

ORIGIN = {"HTTP_ORIGIN": "http://testserver"}
HANDOFF = re.compile(rb'name="handoff" value="([A-Za-z0-9_-]{43})"')


def respond(harness):
    """One real final submission in the harness's Family session.

    The metrics suite's ``respond`` pins the campaign clock to the database
    clock, which falls outside this fixture's campaign dates; the fixture's
    own clock already admits the Family.
    """
    form, answers = form_and_answers(harness)
    assert submit(harness, form, answers).submission is not None


def as_web(callable_, *args, **kwargs):
    """Run one request as the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return callable_(*args, **kwargs)


def open_form(browser, campaign, family):
    """POST Open form as the browser's Admin; the response and its secret."""
    response = as_web(
        browser.post,
        f"/admin/reports/{campaign}/families/{family}/open-form",
        {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
    )
    found = HANDOFF.search(response.content)
    return response, found.group(1).decode() if found else None


def redeem(browser, secret, **headers):
    """POST a hand-off to the Family form from ``browser``."""
    return as_web(
        browser.post, "/family/assisted", {"handoff": secret}, **(ORIGIN | headers)
    )


def test_open_form_marks_the_response_as_entered_by_staff(
    live_response_service, google
):
    """The whole hand-off, its refusals, and the marker where responses show."""
    harness = live_response_service
    campaign = harness.campaign.pk
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, login = signed_in()
    assert login.status_code == 302
    admin_id = PortalSession.objects.get(revoked_at__isnull=True).principal_id

    # The timeline offers Open form as a POST; the code is in no URL.
    timeline = f"/admin/reports/{campaign}/families/{family.pk}/"
    response, body = as_web(get, admin, timeline)
    assert response.status_code == 200
    assert f"/families/{family.pk}/open-form".encode() in body
    assert b"#code=" not in body and harness.code.encode() + b'"' not in body

    # Open form: the hand-off page posts the secret, never the code.
    response, secret = open_form(admin, campaign, family.pk)
    assert response.status_code == 200 and secret
    assert response["Cache-Control"] == "no-store"
    assert harness.code.encode() not in response.content
    cookie = response.cookies[assisted_entry.BINDING_COOKIE]
    assert cookie["path"] == "/family/assisted" and cookie["httponly"]
    assert cookie["samesite"] == "Strict"
    opened = AuditEvent.objects.get(event_type="family_form_opened")
    assert opened.subject_id == family.pk and opened.campaign_reference == campaign
    assert set(opened.auditcontext.context) == {"outcome"}

    # Another browser cannot redeem it (no binding cookie), and that spends it.
    stranger = Client()
    assert redeem(stranger, secret).status_code == 403
    assert redeem(admin, secret).status_code == 403
    # Another site's page cannot post one; that refusal reads nothing, so the
    # Admin's own tab can still use it.
    _, secret = open_form(admin, campaign, family.pk)
    before = set(FamilySession.objects.values_list("pk", flat=True))
    assert redeem(admin, secret, HTTP_ORIGIN="https://evil.example").status_code == 403
    assert set(FamilySession.objects.values_list("pk", flat=True)) == before

    # The real hand-off: signed in as the Family, marked as opened by Staff.
    redeemed = redeem(admin, secret)
    assert redeemed.status_code == 302 and redeemed["Location"] == "/family/"
    session = FamilySession.objects.exclude(pk__in=before).get()
    assert session.family_id == family.pk and session.revoked_at is None
    assisted = AuditEvent.objects.get(event_type="family_assisted_login")
    assert assisted.subject_id == session.pk and assisted.actor_id == admin_id
    # Replaying the same hand-off starts nothing.
    sessions = FamilySession.objects.count()
    assert redeem(admin, secret).status_code == 403
    assert FamilySession.objects.count() == sessions

    # A response submitted in that session records the Admin.
    staff = replace(harness, client=admin, request=redeemed.wsgi_request)
    respond(staff)
    entered = Submission.objects.get(family=family, mode="live")
    assert entered.entered_by_id == admin_id

    # It shows as entered by Staff, never by whom.
    _, body = as_web(get, admin, timeline)
    assert b"Entered by Staff for the Family" in body
    # The hand-off's sign-in reads as Staff's (#795); the Family's own code
    # sign-in, made by the fixture, still reads as a sign-in.
    assert body.count(b"Staff opened the form") == 1
    assert body.count(b"or a mail scanner checked the link") == 1
    _, body = as_web(get, admin, f"/admin/reports/{campaign}/families/")
    assert b"(entered by Staff)" in body
    _, body = as_web(get, admin, f"/admin/reports/{campaign}/responses/submitted/")
    assert b"Entered by Staff" in body

    # The directory's tag follows the current response: once the Family
    # submits its own, it is no longer tagged.
    respond(harness)
    _, body = as_web(get, admin, f"/admin/reports/{campaign}/families/")
    assert b"(entered by Staff)" not in body


def test_the_familys_own_response_is_not_marked(live_response_service):
    """The Family's own code sign-in leaves the marker empty."""
    harness = live_response_service
    respond(harness)
    assert Submission.objects.get(mode="live").entered_by_id is None
    assert not AuditEvent.objects.filter(event_type="family_assisted_login")


def test_a_hand_off_needs_a_live_admin_session(live_response_service, google):
    """Signing out of the Admin portal makes an unused hand-off useless."""
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    _, secret = open_form(admin, harness.campaign.pk, family.pk)
    row = PortalSession.objects.get(revoked_at__isnull=True)
    with unguarded():
        PortalSession.objects.filter(pk=row.pk).update(
            revoked_at=row.last_activity_at, version=row.version + 1
        )
    sessions = FamilySession.objects.count()
    assert redeem(admin, secret).status_code == 403
    assert FamilySession.objects.count() == sessions


def test_open_form_is_refused_outside_its_scope(live_response_service, google):
    """Another campaign or an unknown Family gets the same plain refusal."""
    from uuid import uuid4

    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    for campaign, target in (
        (uuid4(), family.pk),
        (harness.campaign.pk, uuid4()),
    ):
        response, secret = open_form(admin, campaign, target)
        assert response.status_code == 403 and secret is None
        assert b"Open form not available" in response.content
    assert not AuditEvent.objects.filter(event_type="family_form_opened")
    # A GET is not a hand-off.
    response = as_web(
        admin.get,
        f"/admin/reports/{harness.campaign.pk}/families/{family.pk}/open-form",
    )
    assert response.status_code == 405


def test_a_role_without_family_codes_cannot_redeem(
    live_response_service, google, monkeypatch
):
    """A role that loses Open form before redemption cannot use its hand-off.

    No real role has Family timeline access without Family codes, so the
    redemption's role check is narrowed for the redeeming request only; the
    session itself stays live.
    """
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    _, secret = open_form(admin, harness.campaign.pk, family.pk)
    assert secret
    monkeypatch.setattr(assisted_entry, "may_open", lambda principal: False)
    sessions = FamilySession.objects.count()
    assert redeem(admin, secret).status_code == 403
    assert FamilySession.objects.count() == sessions
    assert not AuditEvent.objects.filter(event_type="family_assisted_login")


def test_open_form_is_refused_in_testing(response_service, google):
    """Testing mode accepts only Testing codes, so Open form is refused."""
    harness = response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    response, secret = open_form(admin, harness.campaign.pk, family.pk)
    assert response.status_code == 403 and secret is None
    assert not AuditEvent.objects.filter(event_type="family_form_opened")


def test_a_family_no_longer_admitted_is_not_signed_in(live_response_service, google):
    """A Family that leaves the portal before redemption gets no session."""
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    _, secret = open_form(admin, harness.campaign.pk, family.pk)
    assert secret
    with unguarded():
        FamilyCampaign.objects.filter(pk=family.pk).update(
            portal_eligible=False, version=family.version + 1
        )
    sessions = FamilySession.objects.count()
    assert redeem(admin, secret).status_code == 403
    assert FamilySession.objects.count() == sessions
    assert not AuditEvent.objects.filter(event_type="family_assisted_login")
