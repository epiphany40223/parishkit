"""Open form's hand-off and Staff-entered responses (#529), over HTTP.

The campaign is ``live_response_service``: activated in Production, the
corpus Family (DUID 1) signed in with its own live code. An Administrator
opens the Family form for it through the hand-off, which is single-use,
bound to the Admin's browser and session, and never carries the code or the
secret in a URL; a response submitted in that session is marked as entered
by Staff on the timeline and the directory, never with the Admin's name.
Requests run as the restricted web login.
"""

import html
import re
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import Client

from parishkit.stewardship.accounts import assisted_entry
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilySession,
)
from parishkit.stewardship.campaigns.engagement_models import FamilyEngagement
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import Submission

from .auth_builders import signed_in, unguarded
from .test_background_grants_postgresql import task_login
from .test_report_workspace_postgresql import read as get
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)

ORIGIN = {"HTTP_ORIGIN": "http://testserver"}
HANDOFF = re.compile(rb'name="handoff" value="([A-Za-z0-9_-]{43})"')
RETURN = re.compile(rb'data-return="([^"]*)"')


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


def open_form(browser, family, next=None):
    """POST Open form as the browser's Admin; the response and its secret.

    The route takes the current campaign (NAV-12), so only the Family is in it.
    ``next`` is the address of the page the button was on, when given.
    """
    fields = {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    if next is not None:
        fields["next"] = next
    response = as_web(
        browser.post, f"/admin/reports/families/{family}/open-form/", fields
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
    timeline = f"/admin/reports/families/{family.pk}/"
    response, body = as_web(get, admin, timeline)
    assert response.status_code == 200
    assert f"/families/{family.pk}/open-form/".encode() in body
    assert b"#code=" not in body and harness.code.encode() + b'"' not in body

    # Open form: the hand-off page posts the secret, never the code.
    response, secret = open_form(admin, family.pk)
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
    _, secret = open_form(admin, family.pk)
    before = set(FamilySession.objects.values_list("pk", flat=True))
    assert redeem(admin, secret, HTTP_ORIGIN="https://evil.example").status_code == 403
    assert set(FamilySession.objects.values_list("pk", flat=True)) == before

    # The real hand-off: signed in as the Family, marked as opened by Staff.
    # Staff signing in is not the Family following its link, so the Family's
    # engagement (first link, last seen) is left exactly as it was.
    engagement = list(FamilyEngagement.objects.filter(family=family).values())
    redeemed = redeem(admin, secret)
    assert redeemed.status_code == 302 and redeemed["Location"] == "/family/"
    session = FamilySession.objects.exclude(pk__in=before).get()
    assert session.family_id == family.pk and session.revoked_at is None
    # SQL records the Admin on Open form's session (#2294); the Family's own
    # sign-in, made by the fixture, records none.
    assert session.assisted_by_id == admin_id
    assert set(
        FamilySession.objects.filter(pk__in=before).values_list(
            "assisted_by_id", flat=True
        )
    ) == {None}
    assisted = AuditEvent.objects.get(event_type="family_assisted_login")
    assert assisted.subject_id == session.pk and assisted.actor_id == admin_id
    assert list(FamilyEngagement.objects.filter(family=family).values()) == engagement
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
    assert b"Entered by Staff" in body
    assert b"by Staff for the Family" not in body
    # The hand-off's sign-in reads as Staff's (#795); the Family's own code
    # sign-in, made by the fixture, still reads as a sign-in.
    assert body.count(b"Staff opened the form") == 1
    assert body.count(b"or a mail scanner checked the link") == 1
    _, body = as_web(get, admin, "/admin/reports/families/")
    assert b"(entered by Staff)" in body

    # The directory's tag follows the current response: once the Family
    # submits its own, it is no longer tagged.
    respond(harness)
    _, body = as_web(get, admin, "/admin/reports/families/")
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
    _, secret = open_form(admin, family.pk)
    row = PortalSession.objects.get(revoked_at__isnull=True)
    with unguarded():
        PortalSession.objects.filter(pk=row.pk).update(
            revoked_at=row.last_activity_at, version=row.version + 1
        )
    sessions = FamilySession.objects.count()
    assert redeem(admin, secret).status_code == 403
    assert FamilySession.objects.count() == sessions


def test_open_form_is_refused_outside_its_scope(live_response_service, google):
    """An unknown Family gets the plain refusal. The route takes the current
    campaign itself (NAV-12), so no other campaign can be named."""
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    unknown = uuid4()
    response, secret = open_form(admin, unknown)
    assert response.status_code == 403 and secret is None
    assert b"Open form not available" in response.content
    # The refusal is audited as denied, naming the Admin and what was asked.
    refused = AuditEvent.objects.get(event_type="family_form_opened")
    assert refused.subject_id == unknown and refused.actor_id is not None
    assert refused.auditcontext.context == {"outcome": "denied"}
    # A GET is not a hand-off.
    response = as_web(
        admin.get,
        f"/admin/reports/families/{family.pk}/open-form/",
    )
    assert response.status_code == 405


def test_a_role_without_family_codes_cannot_redeem(
    live_response_service, google, monkeypatch
):
    """A role that loses Open form before redemption cannot use its hand-off.

    No real role has Timeline access without Family codes, so the
    redemption's role check is narrowed for the redeeming request only; the
    session itself stays live.
    """
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    _, secret = open_form(admin, family.pk)
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
    response, secret = open_form(admin, family.pk)
    assert response.status_code == 403 and secret is None
    refused = AuditEvent.objects.get(event_type="family_form_opened")
    assert refused.subject_id == family.pk
    assert refused.auditcontext.context == {"outcome": "denied"}


def test_a_family_no_longer_admitted_is_not_signed_in(live_response_service, google):
    """A Family that leaves the portal before redemption gets no session."""
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    _, secret = open_form(admin, family.pk)
    assert secret
    with unguarded():
        FamilyCampaign.objects.filter(pk=family.pk).update(
            portal_eligible=False, version=family.version + 1
        )
    sessions = FamilySession.objects.count()
    assert redeem(admin, secret).status_code == 403
    assert FamilySession.objects.count() == sessions
    assert not AuditEvent.objects.filter(event_type="family_assisted_login")


def test_a_family_no_longer_eligible_sees_open_form_unavailable(
    live_response_service, google
):
    """The timeline shows Open form unavailable, with why, for a Family that
    may no longer use the Family form; a posted hand-off is still refused
    (#1331)."""
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    timeline = f"/admin/reports/families/{family.pk}/"

    def page():
        """The timeline's whole body, read through the real read guard."""
        response, content = as_web(get, admin, timeline)
        assert response.status_code == 200
        return content.decode()

    assert "data-open-form" in page()
    with unguarded():
        FamilyCampaign.objects.filter(pk=family.pk).update(
            portal_eligible=False, version=family.version + 1
        )
    body = page()
    assert "data-open-form" not in body
    assert '<button type="button" disabled aria-describedby="open-form-reason">' in body
    assert "this Family is no longer in this campaign" in body
    response, secret = open_form(admin, family.pk)
    assert response.status_code == 403 and secret is None


def test_back_returns_to_the_page_open_form_was_clicked_on(
    live_response_service, google
):
    """``next`` names where Back returns: the directory, with its query, or
    the Family's timeline; nothing else, so it is never an open redirect."""
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    admin, _ = signed_in()
    timeline = f"/admin/reports/families/{family.pk}/"
    directory = "/admin/reports/families/?search=Lee&page=2"
    cases = {
        None: timeline,
        directory: directory,
        timeline: timeline,
        "https://evil.example/admin/reports/families/": timeline,
        "//evil.example/admin/reports/families/": timeline,
        "/\\evil.example/admin/reports/families/": timeline,
        "javascript:alert(1)": timeline,
        "/admin/reports/": timeline,
        f"/admin/reports/families/{uuid4()}/": timeline,
        "/admin/nowhere/": timeline,
    }
    for given, expected in cases.items():
        response, secret = open_form(admin, family.pk, next=given)
        assert response.status_code == 200 and secret, given
        found = RETURN.search(response.content).group(1).decode()
        assert html.unescape(found) == expected, given


MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)


def context_body(name):
    """``stewardship_safe_context_v1``'s whole definition in frozen file ``name``."""
    text = (MIGRATIONS / name).read_text(encoding="utf-8")
    start = text.index("CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1")
    return text[start : text.index("$_$;", start) + 4]


# The token change 0065 makes to the removed response lists' report_sort list.
SORTS = "'problem','-problem')"
STAFF_SORTS = "'problem','-problem','staff_entered','-staff_entered')"


@pytest.mark.parametrize(
    "older",
    [
        # Every database before 0065: 0059's body, the latest replacement.
        lambda: context_body("0059_campaign_archives.sql"),
        # A stale rebuild from 0050's body, which would drop 0059's archive
        # words while adding the staff sort tokens.
        lambda: context_body("0050_directory_response_filters.sql").replace(
            SORTS, STAFF_SORTS
        ),
    ],
    ids=["0059", "0050-plus-staff-sorts"],
)
def test_migration_self_check_refuses_an_older_context_body(older):
    """0065's DO block fails on 0059's context body and on 0050's with only
    the staff sort tokens added; on the installed body it passes.

    Each older body runs inside a rolled-back transaction, so the installed
    function is left as the full migration made it.
    """
    text = (MIGRATIONS / "0065_staff_entered.sql").read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    body = older()
    assert body.count(STAFF_SORTS) + body.count(SORTS) == 1
    with connection.cursor() as cursor:
        # The grants the DO block compares against, as the file records them.
        cursor.execute(
            "SELECT set_config('stewardship.migration_0065_acl', (SELECT"
            " string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';'"
            " ORDER BY p.proname) FROM pg_proc p JOIN pg_namespace n ON"
            " n.oid=p.pronamespace WHERE n.nspname='public' AND"
            " p.proname='stewardship_safe_context_v1'), false)"
        )
        cursor.execute(check)
        with transaction.atomic():
            cursor.execute(body)
            with (
                pytest.raises(DatabaseError, match="Entered by Staff sorts"),
                transaction.atomic(),
            ):
                cursor.execute(check)
            transaction.set_rollback(True)
        cursor.execute(check)


def after_close(harness):
    """Close the harness's campaign through its real boundary; a later clock.

    The Family's own response comes first, as in Production, where the
    campaign's first response is long recorded before it closes. Returns
    the campaign clock three days after the end, for ``with``.
    """
    from datetime import timedelta

    from .campaign_builders import campaign_clock, close_campaign

    respond(harness)
    close_campaign(harness.campaign, uuid4())
    assert harness.campaign.state == "closed"
    return campaign_clock(
        harness.campaign.active_configuration.ends_at + timedelta(days=3)
    )


def test_open_form_enters_a_late_response_after_close(live_response_service, google):
    """#2294: after close, the Family's own code, link and session are still
    refused with the ended page, while Staff's Open form signs in, submits a
    response marked as entered by Staff and Late, and gets its receipt."""
    from parishkit.stewardship.responses.models import SubmissionReceiptOccurrence

    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    with after_close(harness):
        # The Family's own code is refused with the after-close page, and its
        # session from before the close ends at its next request.
        before = set(FamilySession.objects.values_list("pk", flat=True))
        own = as_web(Client().post, "/", {"code": harness.code})
        assert own.status_code == 403
        assert b"Sorry, the campaign is over" in own.content
        assert as_web(harness.client.get, "/family/").status_code == 302
        assert set(FamilySession.objects.values_list("pk", flat=True)) == before

        # Staff's Open form still works, marked with the Admin in SQL.
        admin, _ = signed_in()
        admin_id = PortalSession.objects.get(revoked_at__isnull=True).principal_id
        timeline = f"/admin/reports/families/{family.pk}/"
        _, body = as_web(get, admin, timeline)
        assert b"data-open-form" in body
        assert b"It still works after the campaign closes" in body
        response, secret = open_form(admin, family.pk)
        assert response.status_code == 200 and secret
        redeemed = redeem(admin, secret)
        assert redeemed.status_code == 302 and redeemed["Location"] == "/family/"
        session = FamilySession.objects.exclude(pk__in=before).get()
        assert session.assisted_by_id == admin_id
        assert as_web(admin.get, "/family/").status_code == 200

        # A changed answer, so the late response has review items of its
        # own: the Family's earlier ones keep their own submitted time.
        staff = replace(harness, client=admin, request=redeemed.wsgi_request)
        form, answers = form_and_answers(staff)
        answers["family"]["email_opt_out"] = True
        assert submit(staff, form, answers).submission is not None
        late = Submission.objects.filter(family=family, mode="live").latest(
            "family_version"
        )
        assert late.entered_by_id == admin_id
        assert late.submitted_at >= harness.campaign.active_configuration.ends_at
        assert SubmissionReceiptOccurrence.objects.filter(submission=late).exists()

        # Late shows on the Timeline line, and on the review worklist rows.
        _, body = as_web(get, admin, timeline)
        assert body.count(b"Late: after the campaign closed") == 1
        _, body = as_web(get, admin, "/admin/reports/census/")
        assert b"Late: after the campaign closed" in body


@pytest.fixture
def two_family_service(monkeypatch, request):
    """``live_response_service`` with a second eligible Family, DUID 4.

    DUID 4 is a copy of DUID 1's household with its own Member and address;
    the fixture's own sign-in stays DUID 1's.
    """
    from . import response_builders

    original = response_builders.response_source

    def two_families():
        """The response corpus plus DUID 4 and its Member 5."""
        data = original()
        data.families[4] = data.families[1] | {
            "familyDUID": 4,
            "familyID": 14,
            "lastName": "Second",
            "eMailAddress": "second@example.org",
        }
        data.members[5] = data.members[3] | {
            "memberDUID": 5,
            "familyDUID": 4,
            "lastName": "Second",
            "emailAddress": "second@example.org",
        }
        return data

    monkeypatch.setattr(response_builders, "response_source", two_families)
    return request.getfixturevalue("live_response_service")


def test_open_form_enters_a_familys_first_response_after_close(
    two_family_service, google
):
    """#2294: after close, Open form submits the first response of a Family
    that never responded: its first-response pointer is set, the receipt is
    queued, the response records the Admin, and it is marked Late."""
    from parishkit.stewardship.responses.models import SubmissionReceiptOccurrence

    harness = two_family_service
    second = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=4)
    assert second.portal_eligible and second.first_live_submission_id is None
    with after_close(harness):
        assert not Submission.objects.filter(family=second)
        admin, _ = signed_in()
        admin_id = PortalSession.objects.get(revoked_at__isnull=True).principal_id
        response, secret = open_form(admin, second.pk)
        assert response.status_code == 200 and secret
        redeemed = redeem(admin, secret)
        assert redeemed.status_code == 302 and redeemed["Location"] == "/family/"

        staff = replace(harness, client=admin, request=redeemed.wsgi_request)
        form, answers = form_and_answers(staff)
        answers["family"]["email_opt_out"] = True
        late = submit(staff, form, answers).submission
        assert late is not None and late.family_id == second.pk
        assert late.entered_by_id == admin_id and late.family_version == 1
        assert late.submitted_at >= harness.campaign.active_configuration.ends_at
        second.refresh_from_db()
        assert second.first_live_submission_id == late.pk
        assert second.effective_submission_id == late.pk
        receipt = SubmissionReceiptOccurrence.objects.get(submission=late)
        assert receipt.disposition == "queued" and receipt.outbox_id is not None

        _, body = as_web(get, admin, f"/admin/reports/families/{second.pk}/")
        assert body.count(b"Late: after the campaign closed") == 1
        assert b"Entered by Staff" in body
        assert b"by Staff for the Family" not in body
        _, body = as_web(get, admin, "/admin/reports/census/")
        assert b"Late: after the campaign closed" in body


def test_the_close_keeps_open_forms_session(live_response_service, google):
    """#2294: the close revokes every Family's own session but not one Open
    form started, so Staff mid-entry at the close can still submit."""
    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    own = FamilySession.objects.get(revoked_at__isnull=True)
    admin, _ = signed_in()
    _, secret = open_form(admin, family.pk)
    redeemed = redeem(admin, secret)
    assert redeemed.status_code == 302
    assisted = FamilySession.objects.get(assisted_by_id__isnull=False)
    with after_close(harness):
        own.refresh_from_db()
        assisted.refresh_from_db()
        assert own.revoked_at is not None
        assert assisted.revoked_at is None
        assert as_web(harness.client.get, "/family/").status_code == 302
        staff = replace(harness, client=admin, request=redeemed.wsgi_request)
        form, answers = form_and_answers(staff)
        answers["family"]["email_opt_out"] = True
        late = submit(staff, form, answers).submission
        assert late is not None and late.entered_by_id == assisted.assisted_by_id


def test_a_family_session_cannot_reach_the_late_admission(
    live_response_service, google
):
    """#2294: the after-close admission is bound to Open form's hand-off.

    A Family's own session has no ``assisted_by_id``, and the web login can
    neither set one nor sign in through the assisted function without a
    live Admin session; the Family's own sign-in function still refuses
    after close.
    """
    from parishkit.stewardship.accounts.family_authentication import _mint_session

    from .test_family_login_sql_postgresql import fresh_key

    harness = live_response_service
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    own = FamilySession.objects.get(revoked_at__isnull=True)
    assert own.assisted_by_id is None
    digests = harness.service.mac.lookups(harness.campaign.pk, harness.code)
    admin, _ = signed_in()
    portal = PortalSession.objects.get(revoked_at__isnull=True)

    def mint(**options):
        """One SQL sign-in as the web login, in its own transaction."""
        with transaction.atomic():
            return _mint_session(fresh_key(), family.pk, **options)

    def rebind():
        """Try to mark the Family's own session as Staff's, as the web login."""
        with transaction.atomic():
            FamilySession.objects.filter(pk=own.pk).update(
                assisted_by_id=portal.principal_id, version=own.version + 1
            )

    # The web login, which may update Family sessions, cannot do that.
    with pytest.raises(DatabaseError, match="immutable"):
        as_web(rebind)
    with after_close(harness):
        # The Family's own sign-in refuses after close, whatever it presents.
        assert as_web(mint, digests=digests) is None
        # The assisted one needs a live Admin portal session.
        assert as_web(mint, digests=digests, assisted_session=uuid4()) is None
        assert as_web(mint, digests=digests, assisted_session=own.pk) is None
        with unguarded():
            PortalSession.objects.filter(pk=portal.pk).update(
                revoked_at=portal.last_activity_at, version=portal.version + 1
            )
        assert as_web(mint, digests=digests, assisted_session=portal.pk) is None
        # It never takes a link.
        with pytest.raises(ValueError):
            mint(token="x" * 43, assisted_session=portal.pk)
        assert not FamilySession.objects.filter(assisted_by_id__isnull=False)


def frozen_body(name, function):
    """``function``'s whole CREATE OR REPLACE definition in frozen file ``name``."""
    text = (MIGRATIONS / name).read_text(encoding="utf-8")
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{function}(")
    return text[start : text.index("$$;", start) + 3]


# Each body 0188 replaces, with its #2294 admission undone the way an older
# or partial install would lack it; and the receipt guard without its rights.
STALE_0188 = {
    "session-guard": (
        "stewardship_family_session_mutable_v1",
        'OR NEW."assisted_by_id" IS DISTINCT FROM OLD."assisted_by_id" ',
        "",
    ),
    "form-guard": (
        "stewardship_family_baseline_guard_v1",
        "(session_row.assisted_by_id IS NULL OR campaign_row.state<>'closed')",
        "true",
    ),
    "submit-guard": (
        "stewardship_submission_guard_v1",
        "(campaign_row.state='closed' AND session_row.assisted_by_id IS NOT NULL)",
        "false",
    ),
    "receipt-guard": (
        "stewardship_submission_receipt_command_v1",
        "WHERE b.id=s.baseline_id AND fs.assisted_by_id IS NOT NULL",
        "WHERE false",
    ),
    "close-revocation": (
        "stewardship_token_campaign_effects_v1",
        "\n          AND (NEW.state<>'closed' OR assisted_by_id IS NULL);",
        ";",
    ),
}
# The SECURITY DEFINER functions 0188 replaces, each also tested as invoker.
INVOKERS_0188 = {
    "receipt-invoker": "stewardship_submission_receipt_command_v1",
    "effects-invoker": "stewardship_token_campaign_effects_v1",
}


@pytest.mark.parametrize("stale", [*STALE_0188, *INVOKERS_0188])
def test_migration_0188_self_check_refuses_a_stale_install(stale):
    """0188's DO block passes on the installed schema and fails when any one
    replaced body lacks #2294's change, or a replaced definer lost SECURITY
    DEFINER. Each stale install runs in a rolled-back transaction."""
    text = (MIGRATIONS / "0188_staff_open_after_close.sql").read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    with connection.cursor() as cursor:
        cursor.execute(check)
        with transaction.atomic():
            if stale in INVOKERS_0188:
                cursor.execute(
                    f"ALTER FUNCTION public.{INVOKERS_0188[stale]}() SECURITY INVOKER"
                )
            else:
                function, new, old = STALE_0188[stale]
                body = frozen_body("0188_staff_open_after_close.sql", function)
                assert body.count(new) == 1
                cursor.execute(body.replace(new, old))
            with (
                pytest.raises(DatabaseError, match="Migration 0188"),
                transaction.atomic(),
            ):
                cursor.execute(check)
            transaction.set_rollback(True)
        cursor.execute(check)
