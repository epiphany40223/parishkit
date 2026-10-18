"""Sent mail's recipient reads, filters and every kind of email (#1562).

Frozen migration 0116 adds ``stewardship_delivery_ids_for_address_v1`` (the
emails meant for one address, through the GIN index
``outbox_render_intended_lower``) and
``stewardship_delivery_page_recipients_v1`` (the addresses of a page's
Administrator reports and notices). These tests check what each returns and
refuses, that the index serves the address read, the file's DO block, and
Sent mail's Recipient, Purpose, Delivery, date and mode filters through the
real page, including operational notices, which no campaign owns.
"""

import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_reads import (
    administrator_names,
    read_listing,
    with_recipients,
)
from parishkit.stewardship.jobs.operational_content import IncidentKind, IncidentLevel
from parishkit.stewardship.jobs.operational_models import OperationalRecipient
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.jobs.operational_storage import record_observation
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity

from ..policy_factory import address, user
from ..test_outbox_validation import rendering
from .auth_builders import signed_in
from .campaign_builders import change as change_policy
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_operational_fanout_postgresql import consume, schedule
from .test_outbox_postgresql import permit
from .user_builders import add_rules, convert, users

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0116_sent_mail_recipients.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
ADDRESS_FUNCTION = "public.stewardship_delivery_ids_for_address_v1(text, uuid)"
PAGE_FUNCTION = "public.stewardship_delivery_page_recipients_v1(uuid[], uuid)"
BY_ADDRESS = "SELECT * FROM stewardship_delivery_ids_for_address_v1(%s,%s)"
BY_PAGE = (
    "SELECT message_id, address FROM "
    "stewardship_delivery_page_recipients_v1(%s::uuid[],%s)"
)
# A Family's two heads, stored with capitals as ParishSoft may give them.
HEADS = ("First.Head@Example.org", "second@example.org")


def family_message(harness, *, mode="production", purpose="initial"):
    """A Family email to ``HEADS`` with no attempt yet; returns its id.

    In Testing mode it is routed to the Testing recipient, as the real
    preparation routes it.
    """
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    status = create_message(
        identity=DeliveryIdentity(
            scope_id=harness.campaign.pk,
            campaign_id=harness.campaign.pk,
            family_id=family.pk,
            semantic_key=uuid4(),
            mode=mode,
            routing="testing_override" if mode == "testing" else "production",
            purpose=purpose,
        ),
        render=rendering(
            configuration_id=harness.campaign.active_configuration.configuration_id,
            intended_recipients=HEADS,
            routed_recipients=("testing@example.org",) if mode == "testing" else HEADS,
        ),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=permit,
    )
    return status.message_id


def notices(harness):
    """One operational notice to each current Administrator, as the real
    scheduler and worker allocate it; returns {address: recipient}."""
    record_observation(
        IncidentKind.STORAGE_INTEGRITY,
        level=IncidentLevel.CRITICAL,
        policy=IncidentPolicy(),
    )
    (identifier,) = schedule()
    assert consume(identifier, harness.service.store)
    return {each.address: each for each in OperationalRecipient.objects.all()}


def named_users(store):
    """Convert the sign-in rules to users (#952) and add a named Administrator.

    The converted admin@example.org keeps the Name the upgrade gave it, or
    none; returns that Name.
    """
    convert(store)
    add_rules(store, user("morgan@example.org", name="Morgan Administrator"))
    return users(store)["admin@example.org"][0]


def call_as(service, sql, parameters, *, exact=True):
    """Call a definer read as ``service``'s login; its rows or SQLSTATE."""
    with task_login(service, exact=exact):
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                return cursor.fetchall()
        except DatabaseError as error:
            return error.__cause__.sqlstate


def sent_mail(browser, *, link=False, **query):
    """Sent mail as the web login serves it for ``query``: (status, HTML).

    A query with a Recipient is posted, as the page's filter form posts it
    (#1562: the Recipient is private filter state); any other is a link
    (a GET), as is every query when ``link`` is set.
    """
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        if "recipient" in query and not link:
            token = browser.cookies["pk_admin_csrf"].value
            response = browser.post(
                "/admin/mail/sent/", {"csrfmiddlewaretoken": token, **query}
            )
        else:
            response = browser.get("/admin/mail/sent/?" + urlencode(query))
    return response.status_code, response.content.decode()


def region(html, element_id):
    """The markup of the element ``element_id`` opens, up to its next div end."""
    start = html.index(f'id="{element_id}"')
    return html[start : html.index("</div>", start)]


def shown_mode(html):
    """The mode whose Production / Testing segment is current (#1575)."""
    match = re.search(
        r'<button type="submit" name="mode" value="(\w+)" aria-current="page">', html
    )
    return match and match.group(1)


def listed(html, ids):
    """The ids in ``ids`` whose email page Sent mail's ``html`` links."""
    return {each for each in ids if f'href="/admin/mail/sent/{each}/"' in html}


def test_the_address_read_finds_the_emails_meant_for_an_address(
    response_service, google
):
    """In any case, for an Administrator only; nothing else of the rendering,
    and never the Testing recipient a Testing email was routed to."""
    # Testing mail is admitted before the campaign goes live, Production
    # mail after.
    testing = family_message(response_service, mode="testing")
    harness = activate_response_service(response_service)
    signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    production = family_message(harness)
    for typed in ("first.head@example.org", "FIRST.HEAD@EXAMPLE.ORG"):
        found = call_as(ServiceRole.WEB, BY_ADDRESS, (typed, admin))
        assert sorted(found) == sorted([(production,), (testing,)])
    assert call_as(ServiceRole.WEB, BY_ADDRESS, ("testing@example.org", admin)) == []
    assert call_as(ServiceRole.WEB, BY_ADDRESS, ("nobody@example.org", admin)) == []
    # Malformed input is refused, not read.
    for bad in ("no-at-sign", "", "x" * 255 + "@example.org"):
        assert call_as(ServiceRole.WEB, BY_ADDRESS, (bad, admin)) == "22023"


def test_both_reads_refuse_all_but_an_administrator_on_the_web_login(
    response_service, google
):
    """Staff, an unknown or missing actor, other logins, and a login holding
    the web grants without being the web login are refused (42501)."""
    harness = activate_response_service(response_service)
    signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    message_id = family_message(harness)
    rule = address("staff@example.org", ("staff",))
    change_policy(
        harness.service.store,
        harness.service.store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    signed_in()
    staff = PortalUser.objects.get(email="staff@example.org").pk
    for sql, value in ((BY_ADDRESS, "second@example.org"), (BY_PAGE, [message_id])):
        for actor in (staff, uuid4(), None):
            assert call_as(ServiceRole.WEB, sql, (value, actor)) == "42501"
        for service in (ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH):
            assert call_as(service, sql, (value, admin)) == "42501"
        assert call_as(ServiceRole.WEB, sql, (value, admin), exact=False) == "42501"


def test_the_page_read_returns_only_administrator_email_addresses(
    response_service, google
):
    """A notice's address comes back; a Family email's never does; a list
    longer than any page is refused."""
    harness = activate_response_service(response_service)
    recipients = list(notices(harness).values())
    signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    ids = [recipient.outbox_id for recipient in recipients]
    rows = call_as(ServiceRole.WEB, BY_PAGE, (ids, admin))
    assert sorted(rows) == sorted(
        (recipient.outbox_id, recipient.address) for recipient in recipients
    )
    assert call_as(ServiceRole.WEB, BY_PAGE, ([uuid4()] * 501, admin)) == "22023"


def test_a_family_email_address_is_never_returned_by_the_page_read(
    response_service, google
):
    """The Family's name stands for it on the list (#1562 decision 4)."""
    harness = activate_response_service(response_service)
    signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    message_id = family_message(harness)
    assert call_as(ServiceRole.WEB, BY_PAGE, ([message_id], admin)) == []


def test_the_index_serves_the_address_read(response_service):
    """With sequential scans priced out, the read's predicate uses the
    expression index, so it never needs to read every rendering."""
    harness = activate_response_service(response_service)
    family_message(harness)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            "EXPLAIN SELECT r.message_id FROM stewardship_outbox_render r "
            "WHERE (lower((r.intended_recipients)::text))::jsonb "
            "@> jsonb_build_array(lower(%s))",
            ["second@example.org"],
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())
    assert "outbox_render_intended_lower" in plan


def test_the_check_passes_on_the_installed_reads():
    """A migrated database has both reads and the index as 0116 declares."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)


@pytest.mark.parametrize(
    "change",
    [
        f"DROP FUNCTION {ADDRESS_FUNCTION}",
        f"DROP FUNCTION {PAGE_FUNCTION}",
        "DROP INDEX public.outbox_render_intended_lower",
        f"ALTER FUNCTION {ADDRESS_FUNCTION} SECURITY INVOKER",
        f"ALTER FUNCTION {PAGE_FUNCTION} SECURITY INVOKER",
        f"ALTER FUNCTION {PAGE_FUNCTION} SET search_path TO public",
        f"ALTER FUNCTION {ADDRESS_FUNCTION} VOLATILE",
        f"GRANT EXECUTE ON FUNCTION {ADDRESS_FUNCTION} TO PUBLIC",
        f"GRANT EXECUTE ON FUNCTION {PAGE_FUNCTION} TO PUBLIC",
        # An index over the raw (case-sensitive) recipients is not the one.
        "DROP INDEX public.outbox_render_intended_lower; "
        "CREATE INDEX outbox_render_intended_lower ON "
        "public.stewardship_outbox_render USING gin "
        "(intended_recipients)",
        # Nor is the same expression with the default operator class.
        "DROP INDEX public.outbox_render_intended_lower; "
        "CREATE INDEX outbox_render_intended_lower ON "
        "public.stewardship_outbox_render USING gin "
        "(((lower((intended_recipients)::text))::jsonb))",
    ],
)
def test_the_check_refuses_a_missing_or_different_read(change):
    """Without either read or the index, or with any declared attribute
    changed, the DO block raises, so the migration would not commit."""
    with (
        pytest.raises(DatabaseError, match="0116"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(change)
        cursor.execute(CHECK)


def test_sent_mail_filters_by_recipient_purpose_delivery_and_mode(
    response_service, google
):
    """Each filter narrows the real page, and a Production system shows
    Production email by default."""
    # Testing mail is admitted before the campaign goes live, Production
    # mail after.
    testing = family_message(response_service, mode="testing")
    harness = activate_response_service(response_service)
    browser, _ = signed_in()
    production = family_message(harness)
    reminder = family_message(harness, purpose="reminder")
    ids = {production, reminder, testing}
    status, html = sent_mail(browser)
    assert status == 200
    assert listed(html, ids) == {production, reminder}
    # An address in any case, a DUID, and a delivery ID.
    status, html = sent_mail(browser, recipient="FIRST.head@example.ORG")
    assert status == 200 and listed(html, ids) == {production, reminder}
    assert 'value="FIRST.head@example.ORG"' in html
    status, html = sent_mail(browser, recipient="1", mode="testing")
    assert status == 200 and listed(html, ids) == {testing}
    status, html = sent_mail(browser, recipient=str(reminder))
    assert status == 200 and listed(html, ids) == {reminder}
    status, html = sent_mail(browser, recipient="nobody@example.org")
    assert status == 200 and listed(html, ids) == set()
    # Purpose, and the grouped Delivery choices (all three are queued).
    status, html = sent_mail(browser, purpose="reminder")
    assert status == 200 and listed(html, ids) == {reminder}
    for state, shown in (
        ("still_sending", {production, reminder}),
        ("needs_attention", set()),
        ("not_delivered", set()),
    ):
        status, html = sent_mail(browser, state=state)
        assert status == 200 and listed(html, ids) == shown, state
    # The mode choices post the applied filters, the Recipient too, and
    # start again at page 1; the page's address keeps only the linkable
    # filters, never the Recipient (#1562).
    status, html = sent_mail(
        browser, purpose="reminder", page="1", recipient="first.head@example.org"
    )
    assert status == 200
    modes = region(html, "delivery-mode")
    assert '<input type="hidden" name="purpose" value="reminder">' in modes
    assert '<button type="submit" name="mode" value="testing">' in modes
    assert '<input type="hidden" name="mode"' not in modes
    assert 'name="recipient" value="first.head@example.org"' in modes
    assert 'name="page"' not in modes
    assert shown_mode(modes) == "production"
    (address,) = re.findall(r'<a href="([^"]*)" data-page-address hidden>', html)
    assert "purpose=reminder" in address
    assert "recipient" not in address and "first.head" not in address
    assert "page=" not in address
    # A link (a GET) never carries a Recipient: it is refused, unechoed.
    status, html = sent_mail(browser, link=True, recipient="first.head@example.org")
    assert status == 400 and "first.head" not in html
    # Refused filters answer 400, never a wider list.
    for query in (
        {"recipient": "not an address"},
        {"recipient": "a@b@c"},
        {"purpose": "newsletter"},
        {"mode": "all"},
        {"state": "nothing"},
    ):
        assert sent_mail(browser, **query)[0] == 400, query


def test_sent_mail_dates_are_days_in_the_browser_zone(response_service, google):
    """From and Through bound When in the zone the form carries; a day
    without a known zone, or Through before From, is refused."""
    harness = activate_response_service(response_service)
    browser, _ = signed_in()
    message_id = family_message(harness)
    changed = OutboxMessage.objects.get(pk=message_id).updated_at
    zone = "Pacific/Kiritimati"  # UTC+14: the day differs from UTC's.
    day = changed.astimezone(ZoneInfo(zone)).date()
    for start, end, shown in (
        (day, day, True),
        (day, None, True),
        (None, day, True),
        (day - timedelta(days=3), day, True),
    ):
        query = {"zone": zone}
        if start:
            query["start"] = start.isoformat()
        if end:
            query["end"] = end.isoformat()
        status, html = sent_mail(browser, **query)
        assert status == 200
        assert bool(listed(html, {message_id})) is shown, query
    for start, end in (
        (day + timedelta(days=1), None),
        (None, day - timedelta(days=1)),
    ):
        query = {"zone": zone}
        query |= {"start": start.isoformat()} if start else {"end": end.isoformat()}
        status, html = sent_mail(browser, **query)
        assert status == 200 and not listed(html, {message_id}), query
    for query in (
        {"start": day.isoformat()},
        {"start": day.isoformat(), "zone": "Mars/Olympus"},
        {
            "start": day.isoformat(),
            "end": (day - timedelta(days=1)).isoformat(),
            "zone": zone,
        },
        {"start": "20261001", "zone": zone},
    ):
        assert sent_mail(browser, **query)[0] == 400, query


def test_reports_and_notices_name_their_administrator(response_service, google):
    """Operational notices are listed (#1562), each named by its
    Administrator's Users-page Name, else address, and open their own page,
    which offers no resolution: no campaign owns them."""
    harness = activate_response_service(response_service)
    admin_name = named_users(harness.service.store)
    recipients = notices(harness)
    assert {"admin@example.org", "morgan@example.org"} <= set(recipients)
    browser, _ = signed_in()
    status, html = sent_mail(browser, purpose="operational")
    assert status == 200
    named = recipients["morgan@example.org"].outbox_id
    assert listed(html, {named}) == {named}
    # Recipient posts that Administrator's address, never a link (#1562).
    assert (
        '<input type="hidden" name="recipient" value="morgan@example.org">'
        '<input type="hidden" name="mode" value="production">'
    ) in html
    assert f'id="recipient-{named}">Morgan Administrator</button>' in html
    assert "?recipient=" not in html
    # The converted Administrator shows the Name the upgrade gave, or the
    # address when it gave none.
    assert f">{admin_name or 'admin@example.org'}</button></form></td>" in html
    # Sorting by Recipient orders the Administrators' emails by what it
    # shows, the Name or else the address, in both directions (#1562).
    shown = {
        each.outbox_id: (
            "Morgan Administrator" if address == "morgan@example.org" else None
        )
        or (admin_name if address == "admin@example.org" else None)
        or address
        for address, each in recipients.items()
    }
    expected = sorted(shown, key=lambda message: shown[message].casefold())
    for sort, order in (("name", expected), ("-name", expected[::-1])):
        status, html = sent_mail(browser, purpose="operational", sort=sort)
        assert status == 200
        place = {
            message: html.index(f'href="/admin/mail/sent/{message}/"')
            for message in shown
        }
        assert sorted(shown, key=place.get) == order, sort
    assert "Operational notice" in html
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        own = browser.get(f"/admin/mail/sent/{named}/")
    assert own.status_code == 200
    page = own.content.decode()
    assert "Administrator email" in page
    assert "Resolve this email" not in page
    assert 'name="action"' not in page
    # By address, in any case: that Administrator's notice only.
    status, html = sent_mail(browser, recipient="Morgan@Example.org")
    assert status == 200
    assert listed(html, {each.outbox_id for each in recipients.values()}) == {named}


def test_a_testing_system_shows_testing_email_by_default(response_service, google):
    """Before Production is activated the system is in Testing mode, so the
    page (and every link into it) lists Testing email by default (#1562)."""
    testing = family_message(response_service, mode="testing")
    browser, _ = signed_in()
    status, html = sent_mail(browser)
    assert status == 200 and listed(html, {testing}) == {testing}
    assert shown_mode(html) == "testing"
    status, html = sent_mail(browser, state="delivery_unknown")
    assert status == 200 and shown_mode(html) == "testing"
    status, html = sent_mail(browser, mode="production")
    assert status == 200 and listed(html, {testing}) == set()


def test_recipient_names_take_one_read_and_one_query(response_service, google):
    """However many rows, the page's Administrator names cost one definer read
    and one query of the current users; a name matches in any case, and an
    address with no user entry keeps no name."""
    harness = activate_response_service(response_service)
    admin_name = named_users(harness.service.store)
    notices(harness)
    signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    assert administrator_names({"MORGAN@example.org", "gone@example.org"}) == {
        "morgan@example.org": "Morgan Administrator"
    }
    with task_login(ServiceRole.WEB, exact=True):
        rows = read_listing({}, actor=admin)["rows"]
        with CaptureQueriesContext(connection) as queries:
            named = with_recipients(rows, admin)
    # The definer read runs in its own transaction block (BEGIN or a
    # savepoint around it); only the two reads are queries.
    assert len([q for q in queries if q["sql"].startswith("SELECT")]) == 2
    by_address = {row["recipient_address"]: row["recipient_name"] for row in named}
    assert by_address["morgan@example.org"] == "Morgan Administrator"
    assert by_address["admin@example.org"] == (admin_name or None)
