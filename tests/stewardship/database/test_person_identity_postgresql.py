"""The person identity reads and the Family part of a user's Timeline (#1867).

Frozen migration 0157 adds ``stewardship_address_people_v1`` (the Families
and Members whose contacts hold one address) and
``stewardship_family_users_v1`` (the users whose address is a contact of one
Family or its Members). These tests check what each returns and refuses,
the file's DO block, the user Timeline's Family panel and merged lines, the
Family Timeline's "People in this Family who are users", both pages'
statement counts, and ``pk-admin users timeline``'s Family identifiers.

The source fixture (``response_builders.response_source``) gives Family 1 the
Family contact ``family@example.org`` and its Member 3 the contact
``valid@example.org``; Family 2 has no contact.
"""

import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts import family_maintenance
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity

from ..policy_factory import user
from ..test_outbox_validation import rendering
from .auth_builders import signed_in
from .automation_builders import paired
from .response_builders import activate_response_service
from .test_admin_status_cli_postgresql import admin, one  # noqa: F401
from .test_outbox_postgresql import permit
from .test_sent_mail_postgresql import call_as
from .test_timelines_postgresql import as_web
from .test_user_timelines_postgresql import get, timeline_path, update
from .user_builders import add_rules, convert, user_id

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0157_person_identity_reads.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
PEOPLE_FUNCTION = "public.stewardship_address_people_v1(text, uuid)"
USERS_FUNCTION = "public.stewardship_family_users_v1(bigint, uuid)"
BY_ADDRESS = "SELECT * FROM stewardship_address_people_v1(%s,%s)"
BY_FAMILY = "SELECT * FROM stewardship_family_users_v1(%s,%s)"
# Member 3's contact, and Family 1's own contact.
MEMBER = "valid@example.org"
HOUSEHOLD = "family@example.org"


def administrator():
    """The signed-in Administrator's portal user id."""
    return PortalUser.objects.get(email="admin@example.org").pk


def family_email(harness, address):
    """A Production invitation of Family 1 meant for ``address``; its id."""
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    status = create_message(
        identity=DeliveryIdentity(
            scope_id=harness.campaign.pk,
            campaign_id=harness.campaign.pk,
            family_id=family.pk,
            semantic_key=uuid4(),
            mode="production",
            routing="production",
            purpose="initial",
        ),
        render=rendering(
            configuration_id=harness.campaign.active_configuration.configuration_id,
            intended_recipients=(address,),
            routed_recipients=(address,),
        ),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=permit,
    )
    return status.message_id


def family_timeline(browser, family):
    """A Family's Timeline through the real read guard: (status, HTML)."""
    response, body = as_web(browser, f"/admin/reports/families/{family}/")
    return response.status_code, body.decode()


@pytest.fixture
def member(response_service, google):
    """A live campaign whose Family 1's Member 3 is a Staff user.

    Returns ``(harness, administrator browser, the Member's record id,
    Family 1's campaign record id)``. The harness signed Family 1 in, so
    its Timeline has a sign-in line.
    """
    harness = activate_response_service(response_service)
    store = harness.service.store
    convert(store)
    add_rules(store, user(MEMBER, role="ministry_leader", name="Example, Member"))
    record = user_id(store, MEMBER)
    update(store, record, {"role": "staff"})
    browser, login = signed_in()
    assert login.status_code == 302
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    return harness, browser, record, family.pk


def test_the_address_read_finds_the_family_through_either_contact(member):
    """A Member's contact names the Member and Family; a Family contact the
    Family alone; any case; nothing for an unknown address."""
    actor = administrator()
    for typed in (MEMBER, MEMBER.upper()):
        assert call_as(ServiceRole.WEB, BY_ADDRESS, (typed, actor)) == [(1, 3)]
    assert call_as(ServiceRole.WEB, BY_ADDRESS, (HOUSEHOLD, actor)) == [(1, None)]
    assert call_as(ServiceRole.WEB, BY_ADDRESS, ("nobody@example.org", actor)) == []
    for bad in ("no-at-sign", "", "x" * 255 + "@example.org"):
        assert call_as(ServiceRole.WEB, BY_ADDRESS, (bad, actor)) == "22023"


def test_the_family_read_names_only_its_current_users(member):
    """The Member's user is Family 1's; Family 2 has none; a removed user goes."""
    harness, browser, record, family = member
    actor = administrator()
    assert call_as(ServiceRole.WEB, BY_FAMILY, (1, actor)) == [(UUID(record),)]
    assert call_as(ServiceRole.WEB, BY_FAMILY, (2, actor)) == []
    for bad in (0, -1, None):
        assert call_as(ServiceRole.WEB, BY_FAMILY, (bad, actor)) == "22023"
    update(harness.service.store, record)
    assert call_as(ServiceRole.WEB, BY_FAMILY, (1, actor)) == []


def test_both_reads_refuse_all_but_an_administrator_on_the_web_login(member, google):
    """Staff, an unknown or missing actor, other logins, and a login holding
    the web grants without being the web login are refused (42501)."""
    actor = administrator()
    google[0].update(email=MEMBER, sub="synthetic-member")
    signed_in()
    staff = PortalUser.objects.get(email=MEMBER).pk
    for sql, value in ((BY_ADDRESS, MEMBER), (BY_FAMILY, 1)):
        for actor in (staff, uuid4(), None):
            assert call_as(ServiceRole.WEB, sql, (value, actor)) == "42501"
        for service in (ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH):
            assert call_as(service, sql, (value, actor)) == "42501"
        assert call_as(ServiceRole.WEB, sql, (value, actor), exact=False) == "42501"


def test_the_check_passes_on_the_installed_reads():
    """A migrated database has both reads as 0157 declares them."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)


@pytest.mark.parametrize(
    "change",
    [
        f"DROP FUNCTION {PEOPLE_FUNCTION}",
        f"DROP FUNCTION {USERS_FUNCTION}",
        f"ALTER FUNCTION {PEOPLE_FUNCTION} SECURITY INVOKER",
        f"ALTER FUNCTION {USERS_FUNCTION} SECURITY INVOKER",
        f"ALTER FUNCTION {USERS_FUNCTION} SET search_path TO public",
        f"ALTER FUNCTION {PEOPLE_FUNCTION} RESET search_path",
        f"ALTER FUNCTION {PEOPLE_FUNCTION} VOLATILE",
        f"GRANT EXECUTE ON FUNCTION {PEOPLE_FUNCTION} TO PUBLIC",
        f"GRANT EXECUTE ON FUNCTION {USERS_FUNCTION} TO PUBLIC",
    ],
)
def test_the_check_refuses_a_missing_or_different_read(change):
    """Without either read, or with any declared attribute changed, the DO
    block raises, so the migration would not commit."""
    with (
        pytest.raises(DatabaseError, match="0157"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(change)
        cursor.execute(CHECK)


def test_a_family_member_sees_the_familys_records_on_their_timeline(member):
    """The Family panel links its Timeline with its summary; its lines join
    the user's, tagged; its email to the address is listed once."""
    harness, browser, record, family = member
    message = family_email(harness, MEMBER)
    status, page = get(browser, timeline_path(record))
    assert status == 200, page
    assert "Family member of" in page
    assert f'href="/admin/reports/families/{family}/"' in page
    assert "Not found in ParishSoft as a Family member" not in page
    for shown in (
        "Campaign email can reach this Family",
        "Furthest step reached",
        "<code>",
        f'action="/admin/reports/families/{family}/open-form/"',
    ):
        assert shown in page, shown
    # Its lines are a sign-in and an email, no form step, so Kind does not
    # offer Family form activity (only Family form lines do).
    assert 'value="form"' not in page
    # The harness's Family sign-in is a tagged Family line.
    assert 'Signed in <span class="status-badge">' in page
    # The invitation meant for the address is listed once, as the Family's.
    table = page.split('id="events-heading"')[1]
    assert table.count(f'href="/admin/mail/sent/{message}/"') == 1
    assert "Invitation</a> <span" in page
    # The view is audited by the user's record id, and the Family it showed
    # as its own Timeline records a view: by its record id and campaign.
    assert AuditEvent.objects.filter(
        event_type="user_timeline_viewed", subject_id=record
    ).exists()
    shown = AuditEvent.objects.select_related("auditcontext").get(
        event_type="family_timeline_viewed", subject_id=family
    )
    assert shown.actor_id == administrator()
    assert shown.campaign_reference == harness.campaign.pk
    assert shown.auditcontext.context == {"outcome": "succeeded"}


def test_a_user_with_no_family_says_so(member):
    """The Administrator's own address is on no contact record."""
    harness, browser, record, family = member
    administrator_record = user_id(harness.service.store, "admin@example.org")
    status, page = get(browser, timeline_path(administrator_record))
    assert status == 200, page
    assert (
        "Not found in ParishSoft as a Family member, so there are no Family form "
        "records." in page
    )
    assert "Family member of" not in page and 'value="form"' not in page
    assert not AuditEvent.objects.filter(event_type="family_timeline_viewed").exists()


def test_the_family_part_is_unavailable_while_campaign_work_holds_it(
    member, monkeypatch
):
    """A refused report admission leaves the Family's records and lines out."""
    from parishkit.stewardship.reports import export_services

    harness, browser, record, family = member

    def refuse(campaign_id, *, mutating):
        """The admission while a restore review or other work holds it."""
        raise PermissionError("held")

    monkeypatch.setattr(export_services, "admit_campaign", refuse)
    status, page = get(browser, timeline_path(record))
    assert status == 200, page
    assert "form records are unavailable right now" in page
    assert "<code>" not in page and 'class="status-badge">Example' not in page
    # Nothing of the Family was shown, so no Family view is recorded.
    assert not AuditEvent.objects.filter(event_type="family_timeline_viewed").exists()


def test_the_family_timeline_names_its_users_for_administrators_only(member, google):
    """Administrators see the Member's user linked; Staff see no such field."""
    harness, browser, record, family = member
    status, page = family_timeline(browser, family)
    assert status == 200, page
    assert "People in this Family who are users" in page
    assert f'<a href="{timeline_path(record)}">Example, Member</a> (Staff)' in page
    google[0].update(email=MEMBER, sub="synthetic-member")
    staff, _ = signed_in()
    status, page = family_timeline(staff, family)
    assert status == 200, page
    assert "People in this Family who are users" not in page
    assert timeline_path(record) not in page


def test_the_family_timeline_reads_its_users_only_on_its_timeline_tab(
    member, monkeypatch
):
    """Another tab's panel never shows the field, so it reads nothing."""
    from parishkit.stewardship.reports import timeline_views

    harness, browser, record, family = member
    calls = []
    real = timeline_views.family_user_rows

    def counted(*arguments):
        """The users read, counted."""
        calls.append(arguments)
        return real(*arguments)

    monkeypatch.setattr(timeline_views, "family_user_rows", counted)
    status, page = family_timeline(browser, family)
    assert status == 200 and len(calls) == 1, page
    other = next(
        key for key in re.findall(r"\?tab=([a-z0-9-]+)", page) if key != "timeline"
    )
    response, body = as_web(browser, f"/admin/reports/families/{family}/?tab={other}")
    assert response.status_code == 200, body
    assert len(calls) == 1
    assert "People in this Family who are users" not in body.decode()


def test_both_pages_read_a_fixed_number_of_statements(member, monkeypatch):
    """More Family sessions and emails add rows, not statements."""
    harness, browser, record, family = member
    # The Admin chrome reads the Family portal switch, which each process
    # caches for a few seconds of wall-clock time; a counted view that
    # landed just after it expired read it once more (#1965: 205 vs 204 on
    # CI). Start it cold, so the warm-up view fills it from this test's
    # database, and hold it for the measurement, as the Home count does.
    monkeypatch.setitem(family_maintenance._cache, "at", None)
    monkeypatch.setattr(family_maintenance, "CACHE_SECONDS", 3600)

    def statements(read):
        """How many statements one view, ``read()``, runs.

        A first, uncounted view comes before the counted one and fills
        the Family portal switch's cache, which the test holds (above).
        """
        read()
        with CaptureQueriesContext(connection) as queries:
            status, _page = read()
        assert status == 200
        return len(queries)

    def person():
        """The Member's user Timeline."""
        return get(browser, timeline_path(record))

    def household():
        """Family 1's Timeline."""
        return family_timeline(browser, family)

    # One email first: with none, the user's email read skips its second
    # statement (#1866).
    family_email(harness, MEMBER)
    before = statements(person), statements(household)
    family_email(harness, MEMBER)
    for _ in range(5):
        AuditEvent.objects.create(event_type="family_login", actor_id=family)
    assert (statements(person), statements(household)) == before


def test_users_timeline_prints_family_identifiers_only(
    admin,  # noqa: F811
    response_service,
    google,
):
    """Families by DUID, record id and state, Family lines by DUID; the
    Family's name and code appear nowhere, and only a Family whose lines it
    printed is audited as viewed."""
    from parishkit.stewardship.reports.timeline_views import read_identity

    activate_response_service(response_service)
    harness = admin
    store = harness.service.store
    convert(store)
    # A Name unlike the Family's ("Example, Member"), which must not print.
    add_rules(store, user(MEMBER, role="staff", name="Staff, Sam"))
    record = user_id(store, MEMBER)
    _, secret, _ = paired(harness.service, scope="read-only")
    code, document = one(harness, "users", "timeline", "--user", record, secret=secret)
    assert code == 0 and document["ok"], document
    families = document["result"]["families"]
    assert [(each["duid"], each["state"]) for each in families] == [(1, "shown")]
    with transaction.atomic():
        identity = read_identity(
            FamilyCampaign.objects.get(pk=families[0]["family_id"]), codes=True
        )
    assert identity.name and identity.code
    printed = str(document["result"])
    assert identity.name not in printed and identity.code not in printed
    lines = document["result"]["lines"]
    assert any(line["family_duid"] == 1 for line in lines)
    for line in lines:
        assert line["family_duid"] in (None, 1) and "tag" not in line
    # The Family lines it printed are audited as a view of that Family.
    assert AuditEvent.objects.filter(
        event_type="family_timeline_viewed", subject_id=families[0]["family_id"]
    ).exists()
    code, document = one(
        harness, "users", "timeline", "--user", record, "--kind", "form", secret=secret
    )
    assert code == 0 and document["result"]["kind"] == "form", document
    # Role changes print no Family line, so no Family view is recorded.
    viewed = AuditEvent.objects.filter(event_type="family_timeline_viewed").count()
    code, document = one(
        harness, "users", "timeline", "--user", record, "--kind", "roles", secret=secret
    )
    assert code == 0 and document["ok"], document
    assert not any(line["family_duid"] for line in document["result"]["lines"])
    assert document["result"]["families"][0]["state"] == "shown"
    assert (
        AuditEvent.objects.filter(event_type="family_timeline_viewed").count() == viewed
    )
