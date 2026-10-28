"""Email Ministry leaders, sent by hand (#1592), under the real database logins.

Migration 0120: the facts function counts each Ministry leader user's open
requests as the follow-up queue does; the review definer answers only a
live Administrator session and audits by count; the request guard admits
only the web login's reviewed request and records the facts itself; the
scheduler, worker and mail dispatch deliver one email per leader through the
Administrator-notice engine, routed to the Testing recipient in Testing,
with the compiled content kept and rechecked at send; and the Users page's
section reviews and applies it.
"""

import base64
import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.accounts.leader_emails import (
    LeaderEmailsChanged,
    LeaderEmailsRefused,
    leader_rows,
    read_facts,
    request_emails,
)
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.user_changes import plan_changes
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.family_mail_dispatch import FamilyDeliveryHeld
from parishkit.stewardship.jobs.leader_email_models import (
    LeaderEmailCohort,
    LeaderEmailRecipient,
    LeaderEmailRequest,
)
from parishkit.stewardship.jobs.leader_email_owner import (
    LEADER,
    TASK_TYPE,
    leader_owner,
)
from parishkit.stewardship.jobs.models import TaskRun, TaskRunEvent
from parishkit.stewardship.jobs.operational_dispatch import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.operational_fanout import fanout_handler, produce_fanout
from parishkit.stewardship.jobs.outbox_dispatch import delivery_handler
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

from .auth_builders import signed_in, unguarded
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_leader_import_postgresql import leaders_source
from .test_ministry_reports_postgresql import setup
from .user_builders import USERS, convert, post, user_id, web

pytestmark = pytest.mark.django_db(transaction=True)

ORIGIN = "https://portal.example.org"
EMAILS = "/admin/users/leader-emails/"
# Ticking every leader the Testing and Production fixtures create.
EVERYONE = {"email": ["ann@example.org", "both@example.org"]}
# The Ministry leader users each deployment adds (the leader Members
# test_ministry_reports' setup promotes lead these Ministries; nobody@ is a
# user ParishSoft lists as leading nothing).
LEADER_USERS = (
    ("leader-9@example.org", "Nine, Leader"),
    ("leader-4-9@example.org", "Both, Leader"),
    ("nobody@example.org", "Nobody, Leader"),
)
# Ticking every one of them, as the Production fixture's Email selected would.
EVERYONE_PRODUCTION = {"email": [email for email, _name in LEADER_USERS]}


# The three checks as they were before 0120 (frozen copies of the
# baseline's definitions), to compare the new ones against.
OLD_CHECKS = {
    "outbox_purpose": (
        "CHECK (((purpose)::text = ANY ((ARRAY['initial'::character varying, "
        "'reminder'::character varying, 'receipt'::character varying, "
        "'family_test'::character varying, 'daily_digest'::character varying, "
        "'weekly_digest'::character varying, 'operational'::character varying, "
        "'security_event'::character varying])::text[])))"
    ),
    "outbox_routing": (
        "CHECK ((((family_id IS NULL) AND ((purpose)::text = ANY "
        "((ARRAY['operational'::character varying, 'security_event'::character "
        "varying])::text[])) AND ((routing)::text = 'operational'::text)) OR "
        "((NOT ((purpose)::text = ANY ((ARRAY['operational'::character varying,"
        " 'security_event'::character varying])::text[]))) AND (campaign_id IS "
        "NOT NULL) AND ((((mode)::text = 'testing'::text) AND ((routing)::text "
        "= 'testing_override'::text)) OR (((mode)::text = 'production'::text) "
        "AND ((routing)::text = 'production'::text))))))"
    ),
    "outbox_family_purpose": (
        "CHECK ((((family_id IS NOT NULL) AND ((purpose)::text = ANY "
        "((ARRAY['initial'::character varying, 'reminder'::character varying, "
        "'receipt'::character varying, 'family_test'::character "
        "varying])::text[]))) OR ((family_id IS NULL) AND ((purpose)::text = "
        "ANY ((ARRAY['daily_digest'::character varying, "
        "'weekly_digest'::character varying, 'operational'::character varying, "
        "'security_event'::character varying])::text[])))))"
    ),
}


def add_users(store, *entries):
    """Add users-form users (email, name, role) through the real change path."""
    records = store.active().document()["sections"]["login_rules"]
    _, patch = plan_changes(
        records,
        [{"email": email, "name": name, "role": role} for email, name, role in entries],
    )
    assert change(store, store.active(), uuid4(), patch).state == "applied"


@pytest.fixture
def mail_ready(monkeypatch):
    """The root configuration gets the Workspace mail channel dispatch needs.

    As the Family mail worker suite does: the shared root document gains the
    google_workspace integration (and email, when it lacks one) before the
    deployment fixture installs it.
    """
    from parishkit.stewardship.accounts.key_files import file_fingerprint

    from . import campaign_builders

    original = campaign_builders.configuration_document

    def document():
        """The root document with both mail channels."""
        value = original()
        integrations = value["sections"]["integrations"]
        present = {item["values"]["kind"] for item in integrations}
        for kind, settings, fingerprint in (
            (
                "email",
                {"sender": "sender@example.org", "reply_to": "reply@example.org"},
                None,
            ),
            (
                "google_workspace",
                {"delegated_email": "sender@example.org"},
                file_fingerprint(b"synthetic-workspace"),
            ),
        ):
            if kind not in present:
                integrations.append(
                    {
                        "id": str(uuid4()),
                        "values": {
                            "kind": kind,
                            "settings": settings,
                            "credential_fingerprint": fingerprint,
                        },
                    }
                )
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)


@pytest.fixture
def production(mail_ready, request, google):
    """A Production campaign with live requests, leaders, and an Administrator.

    test_ministry_reports' setup submits Member 3's request to join Food
    pantry (9) and to leave Choir (4); the deployment is converted to users
    and gets three Ministry leader users and one Staff user who also leads.
    """
    harness = setup(request.getfixturevalue("response_service"))
    store = harness.service.store
    convert(store)
    add_users(
        store,
        *((email, name, "ministry_leader") for email, name in LEADER_USERS),
        ("leader-4@example.org", "Four, Staff", "staff"),
    )
    browser, login = signed_in()
    assert login.status_code == 302
    return harness, browser


@pytest.fixture
def testing(mail_ready, request, google):
    """A draft campaign in Testing with leaders (no requests yet)."""
    from .leader_builders import leader

    store = request.getfixturevalue("auth_service").store
    convert(store)
    leaders_source(
        store, [leader("ann@example.org", 9), leader("both@example.org", 4, 9)]
    )
    add_users(
        store,
        ("ann@example.org", "Ann, Leader", "ministry_leader"),
        ("both@example.org", "Both, Leader", "ministry_leader"),
    )
    browser, login = signed_in()
    assert login.status_code == 302
    return store, browser


def key(browser):
    """The Admin session key this browser's cookie carries."""
    return browser.cookies["pk_admin"].value


def admin_id():
    """The signed-in Administrator's portal user."""
    return PortalUser.objects.get(email="admin@example.org").pk


def facts(addresses=None):
    """The facts function's answer, as the schema owner runs it."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_leader_email_facts_v1(%s::jsonb)::text",
            [None if addresses is None else json.dumps(addresses)],
        )
        return json.loads(cursor.fetchone()[0])


def counted(answer):
    """{email: [(duid, join, leave), ...]} of a facts answer."""
    return {
        item["email"]: [(m["duid"], m["join"], m["leave"]) for m in item["ministries"]]
        for item in answer
    }


def test_facts_count_each_leaders_open_requests(production):
    """Each Ministry leader user's campaign Ministries, with the queue's counts.

    Staff and Administrators are not leaders here, however they lead; a
    leader user who leads nothing has no Ministries; nothing names a
    parishioner.
    """
    answer = facts()
    assert counted(answer) == {
        "leader-4-9@example.org": [(4, 0, 1), (9, 1, 0)],
        "leader-9@example.org": [(9, 1, 0)],
        "nobody@example.org": [],
    }
    assert [item["email"] for item in answer] == sorted(counted(answer))
    for item in answer:
        assert set(item) == {"email", "ministries"}
        for ministry in item["ministries"]:
            assert set(ministry) == {"duid", "name", "join", "leave"}
            assert ministry["name"]
    text = json.dumps(answer)
    assert "valid@example.org" not in text and "Member" not in text
    # Only the given addresses, and an unknown one is simply absent.
    assert counted(facts(["leader-9@example.org", "stranger@example.org"])) == {
        "leader-9@example.org": [(9, 1, 0)]
    }


def test_the_review_definer_answers_only_a_live_administrator(production):
    """A live Administrator session reads the facts once, audited by count."""
    _harness, browser = production
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        answer = read_facts(key(browser))
        for refused in (None, "", "not-a-session-key"):
            with pytest.raises(LeaderEmailsRefused):
                read_facts(refused)
    assert {email: len(item.ministries) for email, item in answer.items()} == {
        "leader-4-9@example.org": 2,
        "leader-9@example.org": 1,
        "nobody@example.org": 0,
    }
    events = AuditEvent.objects.filter(event_type="user_leader_emails_reviewed")
    assert events.count() == 1 and events.get().actor_id == admin_id()
    context = AuditContext.objects.get(event__in=events)
    assert context.context == {"outcome": "succeeded", "count": 3}
    assert "@" not in str(list(events.values())) + str(context.context)
    # Only web may run it.
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="permission denied"),
    ):
        read_facts("no-session")


def recorded(addresses, *, mode=None, actor=None, configuration=None, testing=None):
    """Record a request under the web login, as Apply does."""
    runtime = SystemConfiguration.objects.get()
    mode = mode or runtime.mode
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return request_emails(
            actor or admin_id(),
            key=uuid4(),
            addresses=addresses,
            configuration_id=configuration or runtime.active_configuration_id,
            campaign_id=runtime.current_campaign_id,
            mode=mode,
            testing_recipient=testing
            if testing is not None
            else (runtime.testing_recipient if mode == "testing" else None),
        )


def test_the_guard_records_the_facts_and_the_audit(production):
    """The web's reviewed request gets its facts from SQL, never the form."""
    request, created = recorded(["leader-9@example.org", "leader-4-9@example.org"])
    assert created
    request.refresh_from_db()
    assert counted(request.recipients) == {
        "leader-4-9@example.org": [(4, 0, 1), (9, 1, 0)],
        "leader-9@example.org": [(9, 1, 0)],
    }
    assert request.mode == "production" and request.testing_recipient is None
    event = AuditEvent.objects.get(event_type="user_leader_emails_requested")
    assert (event.subject_id, event.actor_id) == (request.pk, admin_id())
    assert AuditContext.objects.get(event=event).context == {
        "outcome": "succeeded",
        "count": 2,
    }
    # History is immutable, in SQL too.
    for statement in (
        "UPDATE stewardship_leader_email_request SET recipient_count=1 WHERE id=%s",
        "DELETE FROM stewardship_leader_email_request WHERE id=%s",
    ):
        with (
            pytest.raises(IntegrityError, match="immutable"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [request.pk])


@pytest.mark.parametrize(
    "addresses",
    [
        # Staff, however they lead, get no leader email.
        ["leader-4@example.org"],
        # A leader user who leads none of the campaign's Ministries.
        ["nobody@example.org"],
        # Not a user at all.
        ["stranger@example.org"],
        ["leader-9@example.org", "leader-9@example.org"],
        [],
    ],
)
def test_the_guard_refuses_addresses_it_would_not_email(production, addresses):
    """Only distinct Ministry leader users who lead a campaign Ministry."""
    with pytest.raises(IntegrityError):
        recorded(addresses)
    assert not LeaderEmailRequest.objects.exists()


def test_the_guard_refuses_a_changed_review_and_other_logins(production):
    """A review of another mode or configuration, a non-Administrator actor and
    any login but web are refused; nothing is recorded."""
    target = ["leader-9@example.org"]
    for options in (
        {"mode": "testing"},
        {"configuration": uuid4()},
        {"actor": uuid4()},
    ):
        with pytest.raises((IntegrityError, DatabaseError)):
            recorded(target, **options)
    runtime = SystemConfiguration.objects.get()
    with (
        pytest.raises(IntegrityError, match="current Administrator"),
        work_transaction(),
    ):
        # The schema owner is not the web login.
        LeaderEmailRequest.objects.create(
            id=(identifier := uuid4()),
            actor_id=admin_id(),
            correlation_id=identifier,
            configuration_id=runtime.active_configuration_id,
            campaign_id=runtime.current_campaign_id,
            mode=runtime.mode,
            testing_recipient=None,
            addresses=target,
            recipients=[],
            recipient_count=1,
        )
    assert not LeaderEmailRequest.objects.exists()


def test_a_replayed_apply_records_nothing_more(production):
    """The same review twice is one request; a different binding is refused."""
    runtime = SystemConfiguration.objects.get()
    identifier = uuid4()
    arguments = dict(
        key=identifier,
        addresses=["leader-9@example.org"],
        configuration_id=runtime.active_configuration_id,
        campaign_id=runtime.current_campaign_id,
        mode=runtime.mode,
        testing_recipient=None,
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        first, created = request_emails(admin_id(), **arguments)
        again, replayed = request_emails(admin_id(), **arguments)
        assert created and not replayed and again.pk == first.pk == identifier
        with pytest.raises(LeaderEmailsChanged, match="already bound"):
            request_emails(
                admin_id(), **(arguments | {"addresses": ["leader-4-9@example.org"]})
            )
    assert LeaderEmailRequest.objects.count() == 1


def schedule():
    """The scheduler's own SQL rights allocate the opaque preparation Task."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return produce_fanout(guard, LEADER)


def consume(identifier, store):
    """The worker prepares one page with the deployment's public address."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return execute_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fanout_handler(store, owner=leader_owner(ORIGIN))},
        )


def prepared(store, addresses):
    """Record, schedule and prepare one request; its recipient rows by address."""
    recorded(addresses)
    (identifier,) = schedule()
    assert schedule() == ()
    assert consume(identifier, store)
    assert not consume(identifier, store)
    assert TaskRun.objects.get(pk=identifier).state == "succeeded"
    return {
        row.address: row
        for row in LeaderEmailRecipient.objects.select_related("outbox__render")
    }


def claim(message, store):
    """Claim through the composite MAIL handler, opening no provider pipe."""
    from pathlib import Path

    return claim_hint(
        message.task_id,
        queue=WorkQueue.MAIL,
        worker_id=uuid4(),
        handlers={
            "outbox_delivery": delivery_handler(
                store, credential_path=Path("/synthetic/not-read")
            )
        },
    )


def begin(message, execution, store):
    """The last local boundary before the provider."""
    return begin_submission(
        message.pk,
        execution.claim,
        store=store,
        configuration_id=SystemConfiguration.objects.get().active_configuration_id,
        owner=LEADER,
    )


def test_production_emails_go_to_each_leader_with_the_kept_content(production):
    """One parish-scoped, campaign-free message per leader, sent as compiled."""
    harness, _browser = production
    store = harness.service.store
    rows = prepared(store, ["leader-9@example.org", "leader-4-9@example.org"])
    cohort = LeaderEmailCohort.objects.get()
    assert cohort.mode == "production" and cohort.testing_recipient is None
    assert sorted(cohort.addresses) == sorted(rows)
    for address, row in rows.items():
        message = row.outbox
        assert message.purpose == "leader_email" and message.routing == "production"
        assert message.campaign_id is message.family_id is None
        assert message.scope_id == cohort.parish_id
        assert message.render.intended_recipients == [address]
        assert message.render.routed_recipients == [address]
        assert (row.subject, row.html, row.text) == (
            message.render.subject,
            message.render.html,
            message.render.text,
        )
        assert row.subject.startswith("Sign in to see your Ministry requests")
        assert f"Your sign-in address is {address}" in row.text
        assert f"{ORIGIN}/admin/login" in row.text
        assert f"{ORIGIN}/admin/reports/ministries/" in row.text
        # No parishioner: the requester's address and name never appear.
        assert "valid@example.org" not in row.text + row.html
        assert "Example, Member" not in row.text + row.html
    assert "1 asking to join · 0 asking to leave" in rows["leader-9@example.org"].text
    assert "0 asking to join · 1 asking to leave" in rows["leader-4-9@example.org"].text
    message = rows["leader-9@example.org"].outbox
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
        mail, deadline, attempt = begin(message, execution, store)
        assert isinstance(mail, WeeklyDeliveryMail)
        assert mail.recipients == ("leader-9@example.org",)
        assert (mail.subject, mail.html, mail.text) == (
            rows["leader-9@example.org"].subject,
            rows["leader-9@example.org"].html,
            rows["leader-9@example.org"].text,
        )
        assert attempt == 1 and deadline is not None
        result = finish_submission(
            message.pk,
            execution.claim,
            FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 1),
            LEADER,
        )
        assert result.state.value == "delivered"


def sent_mail(browser, path="/admin/mail/sent/", **query):
    """A Sent mail page as the web login serves it: (status, HTML).

    A query naming a Recipient is posted, as the page's filter form posts
    it (the Recipient is private filter state, #1562); any other is a GET.
    """
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        if "recipient" in query:
            token = browser.cookies["pk_admin_csrf"].value
            response = browser.post(path, {"csrfmiddlewaretoken": token, **query})
        else:
            response = browser.get(path, query)
    return response.status_code, response.content.decode()


def test_sent_mail_lists_ministry_leader_emails(production):
    """Sent mail lists the Ministry leader emails (#1562): each is named by
    its leader's Users-page Name, which links that user's Timeline (#1885)
    rather than filtering in place, the Purpose filter names the kind, and
    its own page says whom it was for, never its content, and offers only
    a note while it is still being sent (#2300: it is settled from evidence
    only once Not sure it arrived)."""
    harness, browser = production
    rows = prepared(
        harness.service.store, ["leader-9@example.org", "leader-4-9@example.org"]
    )
    ids = {address: str(row.outbox_id) for address, row in rows.items()}
    status, html = sent_mail(browser, purpose="leader_email")
    assert status == 200
    for address, name in (
        ("leader-9@example.org", "Nine, Leader"),
        ("leader-4-9@example.org", "Both, Leader"),
    ):
        assert f'href="/admin/mail/sent/{ids[address]}/"' in html
        record = user_id(harness.service.store, address)
        assert f'<td><a href="/admin/users/{record}/timeline/">{name}</a></td>' in html
        assert f'name="recipient" value="{address}"' not in html
    assert (
        '<option value="leader_email" selected>Ministry leader email</option>' in html
    )
    # Listed by default too, beside every other kind.
    status, html = sent_mail(browser)
    assert status == 200 and all(f"/admin/mail/sent/{i}/" in html for i in ids.values())
    # By the leader's address, in any case: that leader's email only.
    status, html = sent_mail(browser, recipient="Leader-9@Example.org")
    assert status == 200
    assert f"/admin/mail/sent/{ids['leader-9@example.org']}/" in html
    assert f"/admin/mail/sent/{ids['leader-4-9@example.org']}/" not in html
    # Still being sent, so nothing to act on: Needs attention lists it only
    # once it is Not sure it arrived (test_settle_leader_emails).
    status, html = sent_mail(browser, state="needs_attention")
    assert status == 200 and not any(i in html for i in ids.values())
    status, own = sent_mail(browser, f"/admin/mail/sent/{ids['leader-9@example.org']}/")
    assert status == 200
    assert "<h1>Ministry leader email</h1>" in own
    assert "<p>Ministry leader email</p>" in own
    assert "leader-9@example.org" in own
    assert "Resolve this email" not in own
    # A queued leader email asks nothing; only the closed note form (#2300).
    assert re.findall(r'name="action" value="(\w+)"', own) == ["note"]
    panel = own[own.index('id="delivery-resolve"') : own.index('id="delivery-state"')]
    assert "disabled" not in panel and "belong to no campaign" not in panel
    # Whom it was for, never what it said.
    assert rows["leader-9@example.org"].subject not in own


def test_a_leader_email_without_a_users_page_name_takes_the_best_member_name(
    mail_ready, request, google, monkeypatch
):
    """A Ministry leader email whose address has no Users-page Name (a user
    since removed, who never signed in) shows the ParishSoft Member name
    the Users page would give it (Administrator decision 2026-10-20,
    #1562): of two Members sharing the address, the one in an active
    Family. The Member-name read ranks as the Users page's own read."""
    from parishkit.stewardship.accounts.users_upgrade import member_names
    from parishkit.stewardship.jobs import delivery_reads

    from .leader_builders import leader

    store = request.getfixturevalue("auth_service").store
    convert(store)
    leaders_source(
        store,
        [
            leader("shared@example.org", 9, family="inactive"),
            leader(" Shared@Example.org ", 9),
            leader("ann@example.org", 9),
        ],
    )
    add_users(
        store,
        ("shared@example.org", "Shared, Leader", "ministry_leader"),
        ("ann@example.org", "Ann, Leader", "ministry_leader"),
    )
    browser, login = signed_in()
    assert login.status_code == 302
    rows = prepared(store, ["shared@example.org", "ann@example.org"])
    ids = {address: row.outbox_id for address, row in rows.items()}
    with task_login(ServiceRole.WEB, exact=True):
        expected = member_names()
        found = delivery_reads.member_names(list(ids.values()), admin_id())
    assert expected["shared@example.org"] == ["Example 1, Leader", "Example 0, Leader"]
    assert found == {
        ids["shared@example.org"]: "Example 1, Leader",
        ids["ann@example.org"]: expected["ann@example.org"][0],
    }
    # With no Users-page Name, Sent mail names each by its Member. Each
    # leader is a current user, so the name links that user's Timeline
    # (#1885).
    monkeypatch.setattr(delivery_reads, "administrator_names", lambda *_: {})
    status, html = sent_mail(browser, purpose="leader_email")
    assert status == 200
    shared = user_id(store, "shared@example.org")
    assert f'<a href="/admin/users/{shared}/timeline/">Example 1, Leader</a>' in html
    # And a name finds it (#1849), in any case.
    status, html = sent_mail(browser, recipient="example 1")
    assert status == 200
    assert f"/admin/mail/sent/{ids['shared@example.org']}/" in html
    assert f"/admin/mail/sent/{ids['ann@example.org']}/" not in html


def test_a_leader_who_is_no_longer_one_is_not_emailed(production):
    """A user demoted to Staff before the send: the unsent email is cancelled."""
    harness, _browser = production
    store = harness.service.store
    rows = prepared(store, ["leader-9@example.org"])
    records = store.active().document()["sections"]["login_rules"]
    record = next(r for r in records if r["values"]["email"] == "leader-9@example.org")
    _, patch = plan_changes(
        records, [{"id": record["id"], "name": "Nine, Leader", "role": "staff"}]
    )
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    message = rows["leader-9@example.org"].outbox
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
        assert begin(message, execution, store) is None
    message.refresh_from_db()
    assert message.state == "cancelled" and message.reason == "recipient_revoked"
    assert message.attempt == 0


def seed(statement, *parameters):
    """Change runtime or campaign state as a superuser would (guards skipped)."""
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(statement, list(parameters))


PAUSE = (
    "UPDATE stewardship_campaign SET delivery_paused=true,"
    " paused_at=clock_timestamp(), pause_actor_id=gen_random_uuid(),"
    " pause_version=pause_version+1"
    " WHERE id=(SELECT current_campaign_id FROM stewardship_system_configuration)"
)
RESUME = "UPDATE stewardship_campaign SET delivery_paused=false"


def assert_revoked(message, store):
    """Mail dispatch cancels the unsent email before any provider attempt."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
        assert begin(message, execution, store) is None
    message.refresh_from_db()
    assert message.state == "cancelled" and message.reason == "recipient_revoked"
    assert message.attempt == 0 and message.submitted_at is None


def test_a_switch_to_testing_cancels_a_prepared_production_email(production):
    """Production to Testing before the send: a real leader's email never goes."""
    harness, _browser = production
    store = harness.service.store
    rows = prepared(store, ["leader-9@example.org"])
    seed(
        "UPDATE stewardship_system_configuration SET mode='testing', "
        "testing_recipient=%s",
        "tester@example.org",
    )
    assert_revoked(rows["leader-9@example.org"].outbox, store)


def test_a_switch_to_production_cancels_a_prepared_testing_email(testing):
    """Testing to Production before the send: the Testing email is cancelled."""
    store, _browser = testing
    rows = prepared(store, ["ann@example.org"])
    seed("UPDATE stewardship_system_configuration SET mode='production'")
    assert_revoked(rows["ann@example.org"].outbox, store)


def test_a_changed_testing_recipient_cancels_a_prepared_email(testing):
    """The email never goes to the old Testing recipient; nor is a request
    made for the old one prepared: its Task is cancelled, with no error, and
    Last emailed says why."""
    from parishkit.stewardship.audit.models import OperationalLog

    store, browser = testing
    rows = prepared(store, ["ann@example.org"])
    seed(
        "UPDATE stewardship_system_configuration SET testing_recipient=%s",
        "other-tester@example.org",
    )
    assert_revoked(rows["ann@example.org"].outbox, store)
    seed(
        "UPDATE stewardship_system_configuration SET testing_recipient=%s",
        "first-tester@example.org",
    )
    recorded(["both@example.org"])
    seed(
        "UPDATE stewardship_system_configuration SET testing_recipient=%s",
        "second-tester@example.org",
    )
    (identifier,) = schedule()
    assert consume(identifier, store)
    assert TaskRun.objects.get(pk=identifier).state == "cancelled"
    assert LeaderEmailCohort.objects.count() == 1
    assert not OperationalLog.objects.filter(level="ERROR").exists()
    with web():
        section = region(browser.get(USERS).content.decode())
    assert section.count("Cancelled: the mode or Testing recipient changed") == 1


def test_a_mode_change_before_preparation_cancels_the_request(production):
    """Production to Testing between Apply and preparation: cancelled cleanly."""
    harness, _browser = production
    recorded(["leader-9@example.org"])
    seed(
        "UPDATE stewardship_system_configuration SET mode='testing', "
        "testing_recipient=%s",
        "tester@example.org",
    )
    (identifier,) = schedule()
    assert consume(identifier, harness.service.store)
    assert TaskRun.objects.get(pk=identifier).state == "cancelled"
    assert not LeaderEmailCohort.objects.exists()
    assert not OutboxMessage.objects.filter(purpose="leader_email").exists()


def test_pause_holds_a_prepared_email_until_resume(production):
    """Pause and resume mail holds a Production leader email, in Python and in
    SQL; it stays pending, untried, and goes out after Resume."""
    from parishkit.stewardship.jobs import leader_email_owner

    harness, _browser = production
    store = harness.service.store
    rows = prepared(store, ["leader-9@example.org"])
    row = rows["leader-9@example.org"]
    message = row.outbox
    # The mail and render dispatch would compile, read before the pause.
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True), work_transaction():
        early = leader_email_owner._current_mail(
            store, row.cohort, row.address, message.semantic_key
        )
    # A claim taken before the pause meets the hold at its effect (a held
    # email is not claimed at all once paused).
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
    seed(PAUSE)
    with (
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
        pytest.raises(FamilyDeliveryHeld, match="paused"),
    ):
        begin(message, execution, store)
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0
    # SQL holds it too when the Python check is skipped.
    owner = leader_owner()
    skipping = owner.__class__(
        **{name: getattr(owner, name) for name in owner.__dataclass_fields__}
        | {"current_mail": lambda *args: early}
    )
    # The held attempt keeps its claim; the same claim tries again.
    configuration = SystemConfiguration.objects.get().active_configuration_id
    with (
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
        pytest.raises(IntegrityError),
    ):
        begin_submission(
            message.pk,
            execution.claim,
            store=store,
            configuration_id=configuration,
            owner=skipping,
        )
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0
    seed(RESUME)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        mail, _, attempt = begin(message, execution, store)
    assert mail.recipients == ("leader-9@example.org",) and attempt == 1


def test_a_paused_email_is_not_claimed_until_resume(production, tmp_path, monkeypatch):
    """While mail is paused, the scheduler's due scan does not hint the email
    and the real MAIL handler takes no claim, writing no Task event however
    often it is offered it; after Resume it is sent exactly once."""
    from parishkit.stewardship import family_delivery_process
    from parishkit.stewardship.accounts.key_files import write_private
    from parishkit.stewardship.jobs.storage import _status

    harness, _browser = production
    store = harness.service.store
    message = prepared(store, ["leader-9@example.org"])["leader-9@example.org"].outbox
    path = tmp_path / "workspace"
    write_private(path, b"synthetic-workspace")
    sent = []

    def submit(candidate, settings, mail, *, seconds, check):
        """The provider boundary: accept and record the one send."""
        sent.append(mail.recipients)
        return FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 1)

    monkeypatch.setattr(family_delivery_process, "submit_weekly", submit)
    handler = delivery_handler(store, credential_path=path)

    def deliver():
        """One MAIL claim cycle for the email's Task."""
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            return execute_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={"outbox_delivery": handler},
            )

    seed(PAUSE)
    task = TaskRun.objects.get(pk=message.task_id)
    events = TaskRunEvent.objects.count()
    for _ in range(3):
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True), work_transaction():
            # The scheduler's due scan asks this before it hints the Task.
            assert handler.admit("hint", _status(task)) is False
        # A hint that arrives anyway is refused before anything is written.
        with pytest.raises(PermissionError, match="not admitted"):
            deliver()
    assert TaskRunEvent.objects.count() == events
    after = TaskRun.objects.get(pk=message.task_id)
    assert (after.state, after.attempt, after.version) == (
        task.state,
        task.attempt,
        task.version,
    )
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0 and sent == []
    seed(RESUME)
    assert deliver()
    assert not deliver()
    message.refresh_from_db()
    assert message.state == "delivered" and message.attempt == 1
    assert sent == [("leader-9@example.org",)]


def test_no_apply_while_mail_is_paused(production):
    """The review and Apply refuse in plain words, and the guard refuses in SQL."""
    _harness, browser = production
    with web():
        review = post(browser, EMAILS, EVERYONE_PRODUCTION)
    preview = re.search(r'name="preview" value="([^"]+)"', review.content.decode())[1]
    seed(PAUSE)
    with web():
        refused_review = post(browser, EMAILS, EVERYONE_PRODUCTION)
        refused_apply = post(browser, EMAILS, {"action": "confirm", "preview": preview})
    for response in (refused_review, refused_apply):
        assert response.status_code == 400
        assert "Mail is paused" in response.content.decode()
    with pytest.raises(IntegrityError, match="mail is paused"):
        recorded(["leader-9@example.org"])
    assert not LeaderEmailRequest.objects.exists()


def test_a_replayed_apply_during_a_pause_says_its_emails_are_held(production):
    """A double click landing after a pause names the first Apply's emails."""
    _harness, browser = production
    with web():
        review = post(browser, EMAILS, EVERYONE_PRODUCTION)
        preview = re.search(r'name="preview" value="([^"]+)"', review.content.decode())[
            1
        ]
        applied = post(browser, EMAILS, {"action": "confirm", "preview": preview})
    assert applied.status_code == 200
    seed(PAUSE)
    with web():
        replayed = post(browser, EMAILS, {"action": "confirm", "preview": preview})
    assert replayed.status_code == 400
    body = replayed.content.decode()
    assert "queued at its first Apply" in body and "Nothing was sent" not in body
    assert LeaderEmailRequest.objects.count() == 1


def test_a_testing_pause_flag_does_not_refuse(testing):
    """Pause holds only Production mail, so a Testing review and request are
    not refused by it, in Python or in SQL."""
    _store, browser = testing
    seed(PAUSE)
    with web():
        review = post(browser, EMAILS, EVERYONE_PRODUCTION)
    assert review.status_code == 200
    request, created = recorded(["ann@example.org"])
    assert created and request.mode == "testing"


def test_an_expired_review_gets_the_shared_refusal(production, monkeypatch):
    """Apply after the review's fifteen minutes is the plain stale refusal."""
    from parishkit.stewardship.web import refusals

    _harness, browser = production
    with web():
        review = post(browser, EMAILS, EVERYONE_PRODUCTION)
    preview = re.search(r'name="preview" value="([^"]+)"', review.content.decode())[1]
    monkeypatch.setattr(refusals, "PREVIEW_MAX_AGE", -1)
    with web():
        refused = post(browser, EMAILS, {"action": "confirm", "preview": preview})
    assert refused.status_code == 409
    assert "This preview is out of date." in refused.content.decode()
    assert not LeaderEmailRequest.objects.exists()


def test_apply_refuses_counts_that_differ_from_the_review(production, monkeypatch):
    """Ministries or counts the guard works out differently from the review
    refuse Apply as a changed page; nothing is recorded or audited."""
    from parishkit.stewardship.accounts import leader_email_views

    _harness, browser = production
    real = leader_email_views.read_facts

    def stale(session_key):
        """The review saw one more request to join than there is now."""
        from dataclasses import replace

        answer = dict(real(session_key))
        found = answer["leader-9@example.org"]
        answer["leader-9@example.org"] = replace(
            found,
            ministries=tuple(
                replace(item, join=item.join + 1) for item in found.ministries
            ),
        )
        return answer

    monkeypatch.setattr(leader_email_views, "read_facts", stale)
    with web():
        review = post(browser, EMAILS, EVERYONE_PRODUCTION)
    monkeypatch.undo()
    preview = re.search(r'name="preview" value="([^"]+)"', review.content.decode())[1]
    with web():
        refused = post(browser, EMAILS, {"action": "confirm", "preview": preview})
    assert refused.status_code == 409
    assert "Something changed since this review" in refused.content.decode()
    assert not LeaderEmailRequest.objects.exists()
    assert not AuditEvent.objects.filter(
        event_type="user_leader_emails_requested"
    ).exists()
    # The same review read now applies.
    with web():
        review = post(browser, EMAILS, EVERYONE_PRODUCTION)
        preview = re.search(r'name="preview" value="([^"]+)"', review.content.decode())[
            1
        ]
        applied = post(browser, EMAILS, {"action": "confirm", "preview": preview})
    assert applied.status_code == 200
    assert LeaderEmailRequest.objects.count() == 1


def test_a_leader_user_with_no_name_is_listed_by_email():
    """A Ministry leader user without a Name is listed under their email."""
    rows = leader_rows(
        [
            {"email": "plain@example.org", "name": "", "role": "ministry_leader"},
            {"email": "staff@example.org", "name": "", "role": "staff"},
        ]
    )
    assert [(row["email"], row["name"]) for row in rows] == [
        ("plain@example.org", "plain@example.org")
    ]


def test_sql_refuses_content_other_than_the_kept_content(production, monkeypatch):
    """Mail dispatch may not send a render that differs from the recipient row."""
    from parishkit.stewardship.jobs import leader_email_owner

    harness, _browser = production
    store = harness.service.store
    rows = prepared(store, ["leader-9@example.org"])
    original = leader_email_owner._current_mail

    def altered(*args):
        """Keep every binding valid while attempting other text."""
        mail, render = original(*args)
        from dataclasses import replace

        return mail, replace(render, text=render.text + "\n\nprivate detail")

    monkeypatch.setattr(leader_email_owner, "_current_mail", altered)
    owner = leader_owner()
    message = rows["leader-9@example.org"].outbox
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
        with pytest.raises(IntegrityError):
            begin_submission(
                message.pk,
                execution.claim,
                store=store,
                configuration_id=SystemConfiguration.objects.get().active_configuration_id,
                owner=owner.__class__(
                    **{
                        name: getattr(owner, name)
                        for name in owner.__dataclass_fields__
                    }
                    | {"current_mail": altered}
                ),
            )
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0


def test_testing_emails_go_only_to_the_testing_recipient(testing):
    """In Testing every email is routed to the Testing recipient and says so."""
    store, _browser = testing
    runtime = SystemConfiguration.objects.get()
    assert runtime.mode == "testing"
    rows = prepared(store, ["ann@example.org", "both@example.org"])
    cohort = LeaderEmailCohort.objects.get()
    assert cohort.testing_recipient == runtime.testing_recipient
    for address, row in rows.items():
        message = row.outbox
        assert message.routing == "testing_override" and message.mode == "testing"
        assert message.render.intended_recipients == [address]
        assert message.render.routed_recipients == [runtime.testing_recipient]
        assert row.subject.startswith("[TEST] ")
        assert row.text.startswith(
            f"TEST — sent to {runtime.testing_recipient} instead of Ministry "
            f"leader {address}."
        )
        assert "No open requests" in row.text
    message = rows["ann@example.org"].outbox
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
        mail, _, _ = begin(message, execution, store)
        assert mail.recipients == (runtime.testing_recipient,)


def test_the_cohort_binds_its_request(testing, monkeypatch):
    """A cohort naming other addresses than its request's is refused by SQL."""
    from parishkit.stewardship.jobs import leader_email_owner
    from parishkit.stewardship.jobs.operational_fanout import prepare_page

    store, _browser = testing
    recorded(["ann@example.org"])
    (identifier,) = schedule()
    original = LeaderEmailCohort.objects.create

    def widened(**values):
        """Add an address the request never named."""
        return original(
            **(values | {"addresses": ["ann@example.org", "x@example.org"]})
        )

    monkeypatch.setattr(LeaderEmailCohort.objects, "create", widened)
    owner = leader_owner(ORIGIN)
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fanout_handler(store, owner=owner)},
        )
        with (
            pytest.raises(IntegrityError, match="match its request"),
            work_transaction(),
        ):
            prepare_page(execution.claim, store, owner)
    assert not LeaderEmailCohort.objects.exists()
    assert not OutboxMessage.objects.filter(purpose="leader_email").exists()
    assert leader_email_owner.TASK_TYPE == TASK_TYPE


def test_the_scheduler_sees_no_address(testing):
    """The scheduler allocates work from opaque identities alone."""
    store, _browser = testing
    prepared(store, ["ann@example.org"])
    with task_login(ServiceRole.SCHEDULER, exact=True):
        for statement in (
            "SELECT addresses FROM stewardship_leader_email_request",
            "SELECT addresses FROM stewardship_leader_email_cohort",
            "SELECT address FROM stewardship_leader_email_recipient",
            "SELECT html FROM stewardship_leader_email_recipient",
        ):
            with pytest.raises(DatabaseError), work_transaction():
                connection.cursor().execute(statement)
    # Web lists recipients for Last emailed, never their content.
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        work_transaction(),
    ):
        connection.cursor().execute(
            "SELECT html FROM stewardship_leader_email_recipient"
        )


def region(body):
    """The Email Ministry leaders section of the Users page."""
    start = body.index('<section id="emails-table"')
    return body[start : body.index("</section>", start)]


def test_the_users_page_reviews_and_applies_the_emails(testing):
    """The section lists the leaders; Email selected reviews; Apply records once."""
    store, browser = testing
    with web():
        response = browser.get(USERS)
    assert response.status_code == 200
    # The help names the page that lists these emails, Sent mail (#2102).
    assert "Sent mail lists these emails too." in response.content.decode()
    assert "Outgoing mail" not in response.content.decode()
    section = region(response.content.decode())
    assert "Email Ministry leaders" in section
    for email in ("ann@example.org", "both@example.org"):
        assert f'name="email" value="{email}"' in section
    assert section.count("Never") >= 2
    with web():
        review = post(browser, EMAILS, EVERYONE)
    assert review.status_code == 200
    body = review.content.decode()
    assert "data-review-answer" in body and "2 emails" in body
    assert "Testing mode: every email goes to the Testing recipient" in body
    assert "Choir: No open requests" in body or "No open requests" in body
    preview = re.search(r'name="preview" value="([^"]+)"', body)[1]
    for _ in range(2):
        with web():
            applied = post(browser, EMAILS, {"action": "confirm", "preview": preview})
        assert applied.status_code == 200
    assert LeaderEmailRequest.objects.count() == 1
    assert sorted(LeaderEmailRequest.objects.get().addresses) == [
        "ann@example.org",
        "both@example.org",
    ]
    with web():
        listed = region(browser.get(USERS).content.decode())
    assert "Still sending (queued)" in listed
    # A tampered review is refused.
    with web():
        refused = post(browser, EMAILS, {"action": "confirm", "preview": "x" + preview})
    assert refused.status_code == 400


def token_payload(token):
    """The base64url payload of a signed review, decoded without the secret.

    Django signing does not encrypt, so anyone holding a review can read
    this much of it.
    """
    payload = token.split(":", 1)[0]
    return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()


def test_the_review_binds_the_testing_recipient_only_by_digest(testing):
    """The signed review never carries the Testing recipient's address, and
    a changed Testing recipient refuses Apply as changed."""
    _store, browser = testing
    recipient = SystemConfiguration.objects.get().testing_recipient
    with web():
        review = post(browser, EMAILS, EVERYONE)
    preview = re.search(r'name="preview" value="([^"]+)"', review.content.decode())[1]
    payload = token_payload(preview)
    assert '"recipient"' in payload
    assert recipient and recipient not in payload
    seed(
        "UPDATE stewardship_system_configuration SET testing_recipient=%s",
        "other-tester@example.org",
    )
    with web():
        refused = post(browser, EMAILS, {"action": "confirm", "preview": preview})
    assert refused.status_code == 409
    assert "Something changed since this review" in refused.content.decode()
    assert not LeaderEmailRequest.objects.exists()
    # The same review read now applies and records the current address.
    with web():
        review = post(browser, EMAILS, EVERYONE)
        preview = re.search(r'name="preview" value="([^"]+)"', review.content.decode())[
            1
        ]
        applied = post(browser, EMAILS, {"action": "confirm", "preview": preview})
    assert applied.status_code == 200
    assert LeaderEmailRequest.objects.get().testing_recipient == (
        "other-tester@example.org"
    )


def test_email_selected_needs_a_choice_and_skips_non_leaders(testing):
    """No tick is refused; a non-leader address is listed as skipped."""
    _store, browser = testing
    with web():
        empty = post(browser, EMAILS, {})
        skipped = post(
            browser,
            EMAILS,
            {"email": ["ann@example.org", "admin@example.org"]},
        )
    assert empty.status_code == 400
    assert skipped.status_code == 200
    body = skipped.content.decode()
    assert "1 email" in body and "No longer a Ministry leader user" in body


def test_other_roles_get_no_review(auth_service, google):
    """A Staff session is refused by the route, and nothing is audited."""
    from .leader_builders import grant_role

    grant_role(auth_service.store, "reader@example.org", "staff")
    convert(auth_service.store)
    google[0]["email"] = "reader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        response = post(browser, EMAILS, EVERYONE)
    assert response.status_code == 403
    assert not AuditEvent.objects.filter(
        event_type="user_leader_emails_reviewed"
    ).exists()


# Every outbox purpose, routing class and mode, with and without a Family
# and a campaign.
PURPOSES = (
    "initial",
    "reminder",
    "receipt",
    "family_test",
    "daily_digest",
    "weekly_digest",
    "operational",
    "security_event",
    "leader_email",
)


def admitted(checks):
    """Which (purpose, routing, mode, family, campaign) rows all checks admit."""
    rows = [
        (purpose, routing, mode, family, campaign)
        for purpose in PURPOSES
        for routing in ("operational", "testing_override", "production")
        for mode in ("testing", "production")
        for family in (False, True)
        for campaign in (False, True)
    ]
    # Each check evaluated over one row of literal values.
    expression = " AND ".join(f"({check.removeprefix('CHECK ')})" for check in checks)
    statement = (
        f"SELECT {expression} FROM (SELECT %s::varchar(24) AS purpose, "
        "%s::varchar(24) AS routing, %s::varchar(16) AS mode, "
        "%s::uuid AS family_id, %s::uuid AS campaign_id) shape"
    )
    result = set()
    with connection.cursor() as cursor:
        for row in rows:
            purpose, routing, mode, family, campaign = row
            cursor.execute(
                statement,
                [
                    purpose,
                    routing,
                    mode,
                    str(uuid4()) if family else None,
                    str(uuid4()) if campaign else None,
                ],
            )
            if cursor.fetchone()[0]:
                result.add(row)
    return result


def test_the_outbox_checks_admit_leader_email_and_change_nothing_else():
    """Old and new checks agree on every other purpose; a leader email is only
    parish-scoped, with no Family or campaign, and routed by its mode."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='public.stewardship_outbox_message'::regclass "
            "AND conname = ANY(%s)",
            [list(OLD_CHECKS)],
        )
        new = dict(cursor.fetchall())
    assert set(new) == set(OLD_CHECKS)
    before, after = admitted(OLD_CHECKS.values()), admitted(new.values())
    assert {row for row in before if row[0] == "leader_email"} == set()
    assert {row for row in after if row[0] != "leader_email"} == before
    assert {row for row in after if row[0] == "leader_email"} == {
        ("leader_email", "testing_override", "testing", False, False),
        ("leader_email", "production", "production", False, False),
    }


MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0120_leader_emails.sql"
)


def check_block():
    """0120's closing DO block, run on its own."""
    text = MIGRATION.read_text(encoding="utf-8")
    return re.search(r"^DO \$check\$.*?^\$check\$;", text, re.M | re.S)[0]


def test_the_check_block_passes_only_with_everything_installed():
    """It passes on the installation, and refuses an older check or guard."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check_block())
    refusals = [
        (
            [
                "ALTER TABLE stewardship_outbox_message DROP CONSTRAINT outbox_purpose",
                "ALTER TABLE stewardship_outbox_message "
                "ADD CONSTRAINT outbox_purpose "
                + OLD_CHECKS["outbox_purpose"]
                + " NOT VALID",
            ],
            "the outbox checks do not admit leader_email",
        ),
        (
            [
                "ALTER FUNCTION public.stewardship_leader_emails_v1(text, uuid) "
                "SECURITY INVOKER"
            ],
            "not installed as declared",
        ),
        (
            [
                "DROP TRIGGER stewardship_leader_email_request_guard "
                "ON public.stewardship_leader_email_request"
            ],
            "trigger is missing",
        ),
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('public.stewardship_family_dispatch_write_v1()'"
            "::regprocedure)"
        )
        dispatch = cursor.fetchone()[0]
    start = dispatch.index("    IF own.purpose='leader_email' THEN")
    end = dispatch.index("    END IF;\n", dispatch.index("RETURN NEW;", start)) + len(
        "    END IF;\n"
    )
    refusals.append(
        ([dispatch[:start] + dispatch[end:]], "a replaced outbox guard lacks")
    )
    # The outbox state guard as it was before Pause and resume mail held
    # leader emails.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('public.stewardship_outbox_state_v1()'"
            "::regprocedure)"
        )
        state = cursor.fetchone()[0]
    paused = (
        "\n                  AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign"
        " paused\n                      WHERE paused.id=s.current_campaign_id AND"
        " paused.delivery_paused)"
    )
    assert state.count(paused) == 1
    refusals.append(([state.replace(paused, "")], "a replaced outbox guard lacks"))
    for statements, refusal in refusals:
        with (
            pytest.raises(DatabaseError, match=refusal),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            for statement in statements:
                cursor.execute(statement)
            cursor.execute(check_block())
