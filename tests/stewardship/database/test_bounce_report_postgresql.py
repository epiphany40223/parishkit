"""An Administrator's bounce report sets an address aside like a refusal (#806).

Slice 4 of #806: on a delivered Production Family email's Mail message page
an Administrator reports that the email bounced at one of its addresses. The
report is a ``stewardship_recipient_refusal`` of kind ``reported_bounce``
(frozen migration 0081), written only through the web-only definer
``stewardship_report_recipient_bounce_v1`` and admitted by the refusal
guard's new branch. These tests use the real SQL boundary: the web login's
grants, the definer, the guard, the Family deliverability trigger and the
audit event, and prove that no Family credential changes.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.http import QueryDict

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_admin import (
    clear_recipient_refusal,
    report_recipient_bounce,
)
from parishkit.stewardship.jobs.delivery_reads import (
    BOUNCE_UNAVAILABLE,
    bounce_panel,
    email_refusals,
    read_refusals,
)
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.outbox_models import OutboxEvent, OutboxMessage
from parishkit.stewardship.jobs.recipient_models import RecipientRefusal
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StaleRecordError

from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_outbox_postgresql import change, provider_evidence, submit
from .test_policy_postgresql import user
from .test_recipient_suppressions_postgresql import email

pytestmark = pytest.mark.django_db(transaction=True)

ADDRESS = "valid@example.org"
NOTE = "Bounce arrived 10/12: 550 5.1.1 mailbox not found."
FILE = (
    __import__("pathlib").Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations"
)


def delivered(harness, **options):
    """One Production email to Family 1, accepted by the provider; its status."""
    return change(
        submit(email(harness, **options)),
        DeliveryAction.ACCEPT,
        evidence=provider_evidence(),
    )


@pytest.fixture
def scenario(response_service):
    """A delivered email to the Family's only eligible address, and an Admin."""
    harness = activate_response_service(response_service)
    status = delivered(harness)
    principal = user("admin@example.org")
    return harness, status, principal


def report(scenario, **overrides):
    """Report a bounce through the service under web's exact grants."""
    harness, status, principal = scenario
    values = (
        dict(
            message_id=status.message_id,
            address=ADDRESS,
            command_id=uuid4(),
            note=NOTE,
            observed=True,
        )
        | overrides
    )
    with task_login(ServiceRole.WEB, exact=True):
        return report_recipient_bounce(harness.service.store, principal.pk, **values)


def credentials(family_id):
    """The Family columns a report must never touch (its code and link state)."""
    return FamilyCampaign.objects.filter(pk=family_id).values(
        "family_duid", "campaign_id", "active", "email_eligible", "portal_eligible"
    )[0]


def test_report_sets_the_address_aside_audits_and_replays(scenario):
    """A report is a refusal of its own kind on the delivered attempt; the
    Family stops being deliverable, the audit names it, a repeat finds it,
    and nothing about the Family's credentials changes."""
    _, status, principal = scenario
    message = OutboxMessage.objects.get(pk=status.message_id)
    family = FamilyCampaign.objects.get(pk=message.family_id)
    assert family.email_deliverable
    before = credentials(family.pk)
    command = uuid4()
    refusal = report(scenario, command_id=command)
    assert refusal.pk == command and refusal.kind == "reported_bounce"
    assert (refusal.address, refusal.actor_id) == (ADDRESS, principal.pk)
    assert refusal.evidence_note == NOTE
    event = OutboxEvent.objects.get(pk=refusal.event_id)
    assert (event.message_id, event.state) == (message.pk, "delivered")
    family.refresh_from_db()
    assert not family.email_deliverable
    assert family.deliverability_reason == "provider_suppressed"
    assert credentials(family.pk) == before
    audit = AuditEvent.objects.get(event_type="recipient_bounce_reported")
    assert (audit.subject_id, audit.actor_id) == (command, principal.pk)
    # The same command again returns the report; nothing more is written.
    version = family.version
    assert report(scenario, command_id=command).pk == command
    family.refresh_from_db()
    assert family.version == version
    assert (
        AuditEvent.objects.filter(event_type="recipient_bounce_reported").count() == 1
    )
    # The same key with another intent is refused.
    with pytest.raises(ValueError, match="already bound"):
        report(scenario, command_id=command, note="Another note")
    # Sent mail's and Refused addresses' readers see it, with its kind.
    assert email_refusals(message.pk) == [
        dict(id=command, address=ADDRESS, kind="reported_bounce")
    ]
    rows = read_refusals(QueryDict(""))["rows"]
    assert [(row["id"], row["kind"]) for row in rows] == [(command, "reported_bounce")]
    assert message.pk not in OutboxMessage.objects.exclude(
        state="delivered"
    ).values_list("pk", flat=True)


def test_a_set_aside_address_or_undelivered_email_is_stale(scenario):
    """A second report of the same address, and a report on an email that was
    not delivered, are refused before the definer runs."""
    harness, _, _ = scenario
    report(scenario)
    with pytest.raises(StaleRecordError, match="already set aside"):
        report(scenario)
    pending = email(harness)
    with pytest.raises(StaleRecordError, match="delivered"):
        report(scenario, message_id=pending.message_id)


def test_only_a_current_administrator_may_report(scenario):
    """A user without the Administrator role, and an unknown one, are refused
    by the service's policy check."""
    harness, status, _ = scenario
    other = user("nobody@example.org")
    for actor in (other.pk, uuid4()):
        with (
            task_login(ServiceRole.WEB, exact=True),
            pytest.raises(PermissionError),
        ):
            report_recipient_bounce(
                harness.service.store,
                actor,
                message_id=status.message_id,
                address=ADDRESS,
                command_id=uuid4(),
                note=NOTE,
                observed=True,
            )
    assert not RecipientRefusal.objects.exists()


def test_clearing_a_report_lets_email_go_to_the_address_again(scenario):
    """Clear verified refusal undoes a report, as it does a provider refusal."""
    harness, status, principal = scenario
    refusal = report(scenario)
    current = SourceCurrent.objects.get()
    with task_login(ServiceRole.WEB, exact=True):
        clear_recipient_refusal(
            harness.service.store,
            principal.pk,
            refusal_id=refusal.pk,
            command_id=uuid4(),
            source_snapshot_id=current.snapshot_id,
            source_generation=current.generation,
            note="Reported in error; the mailbox works.",
            verified=True,
        )
    family = FamilyCampaign.objects.get(
        pk=OutboxMessage.objects.get(pk=status.message_id).family_id
    )
    assert family.email_deliverable
    # The cleared report cannot be filed again for the same email (one
    # refusal per email and address); the panel no longer offers it.
    with (
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
    ):
        report(scenario)


def call(principal, message_id, address, *, note=NOTE):
    """Call the definer directly, as the current login."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_report_recipient_bounce_v1(%s,%s,%s,%s,%s)",
            (uuid4(), message_id, address, principal.pk, note),
        )
        return cursor.fetchone()[0]


def test_the_definer_and_guard_refuse_what_the_page_never_sends(scenario):
    """The SQL boundary holds on its own: an address the email was not sent
    to, a blank note and a non-Administrator are refused, web cannot insert
    a refusal itself, and mail dispatch can neither call the definer nor
    insert a reported bounce."""
    harness, status, principal = scenario
    other = user("nobody@example.org")
    family = OutboxMessage.objects.get(pk=status.message_id).family
    with task_login(ServiceRole.WEB, exact=True):
        for address, note, actor, code in (
            ("other@example.org", NOTE, principal, "23514"),
            (ADDRESS, "   ", principal, "23514"),
            (ADDRESS, NOTE, other, "42501"),
        ):
            with pytest.raises(DatabaseError) as error, transaction.atomic():
                call(actor, status.message_id, address, note=note)
            assert error.value.__cause__.sqlstate == code
        with pytest.raises(DatabaseError) as error, transaction.atomic():
            RecipientRefusal.objects.create(
                id=uuid4(),
                actor_id=principal.pk,
                correlation_id=uuid4(),
                family_id=family.pk,
                event_id=OutboxEvent.objects.get(
                    message_id=status.message_id, state="delivered"
                ).pk,
                address=ADDRESS,
                organization_id=1,
                family_duid=family.family_duid,
                kind="reported_bounce",
                evidence_note=NOTE,
            )
        assert error.value.__cause__.sqlstate == "42501"
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        with pytest.raises(DatabaseError) as error, transaction.atomic():
            call(principal, status.message_id, ADDRESS)
        assert error.value.__cause__.sqlstate == "42501"
        organization = SourceCurrent.objects.values_list(
            "organization_id", flat=True
        ).get()
        with pytest.raises(DatabaseError) as error, work_transaction():
            RecipientRefusal.objects.create(
                id=uuid4(),
                actor_id=principal.pk,
                correlation_id=uuid4(),
                family_id=family.pk,
                event_id=OutboxEvent.objects.get(
                    message_id=status.message_id, state="delivered"
                ).pk,
                address=ADDRESS,
                organization_id=organization,
                family_duid=family.family_duid,
                kind="reported_bounce",
                evidence_note=NOTE,
            )
        assert error.value.__cause__.sqlstate == "23514"
    assert not RecipientRefusal.objects.exists()


def page_row(message_id):
    """An email's fields as the Mail message page reads them for the panel."""
    return OutboxMessage.objects.values(
        "id", "mode", "state", "family_id", "family__family_duid"
    ).get(pk=message_id)


def panel_for(message, addresses, *, can_change=True):
    """``bounce_panel`` under web's exact grants, for an email to ``addresses``."""
    recipients = {"intended": list(addresses), "routed": list(addresses)}
    with task_login(ServiceRole.WEB, exact=True):
        return bounce_panel(message, recipients, can_change=can_change)


def test_the_panel_offers_unset_addresses_and_says_why_it_cannot(scenario):
    """The Mail message page's panel: the email's addresses not set aside,
    each prerequisite's reason, and the reports already recorded."""
    harness, status, _ = scenario
    message = page_row(status.message_id)
    panel = panel_for(message, [ADDRESS])
    assert panel["addresses"] == [ADDRESS] and panel["unavailable"] is None
    assert panel["reported"] == []
    closed = panel_for(message, [ADDRESS], can_change=False)
    assert closed["unavailable"] == BOUNCE_UNAVAILABLE["closed"]
    waiting = page_row(email(harness).message_id)
    assert (
        panel_for(waiting, [ADDRESS])["unavailable"]
        == BOUNCE_UNAVAILABLE["not_delivered"]
    )
    refusal = report(scenario)
    panel = panel_for(message, [ADDRESS])
    assert panel["addresses"] == [] and panel["last"] is None
    assert panel["unavailable"] == BOUNCE_UNAVAILABLE["none_left"]
    assert [(row["id"], row["cleared"]) for row in panel["reported"]] == [
        (refusal.pk, False)
    ]
    assert panel_for(message | {"mode": "testing"}, [ADDRESS]) is None


def test_the_panel_warns_when_the_familys_last_address_is_offered(scenario):
    """The only-address notice: shown when the Family's current recipients
    (ParishSoft's addresses with no unresolved refusal) are one offered
    address, so reporting it would stop all its campaign email; hidden when
    the offered address is not that one, so another address stays usable."""
    harness, status, _ = scenario
    assert panel_for(page_row(status.message_id), [ADDRESS])["last"] == ADDRESS
    other = delivered(harness, address="old@example.org")
    panel = panel_for(page_row(other.message_id), ["old@example.org"])
    assert panel["addresses"] == ["old@example.org"] and panel["last"] is None


def force(statement, parameters=()):
    """Change a guarded row for a test only, with triggers off (as owner)."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(statement, parameters)


@pytest.mark.parametrize(
    "statement",
    [
        # Admin changes are not admitted (a restore under review).
        "UPDATE stewardship_system_configuration SET restore_review_required=true",
        # The email's campaign is archived.
        "UPDATE stewardship_campaign SET state='archived'",
        # The email's campaign is not the current one.
        "UPDATE stewardship_system_configuration SET current_campaign_id=NULL",
    ],
)
def test_the_guard_refuses_a_campaign_that_admits_no_change(scenario, statement):
    """The refusal guard admits a report only in the current, not archived
    campaign that admits Admin changes, whatever the service checked."""
    _, status, principal = scenario
    force(statement)
    with (
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
    ):
        call(principal, status.message_id, ADDRESS)
    assert error.value.__cause__.sqlstate == "23514"
    assert not RecipientRefusal.objects.exists()


def test_the_definer_refuses_an_email_that_was_not_delivered(scenario):
    """An email with no delivered event has nothing a report could name: the
    definer refuses it at the SQL boundary, not only in the service."""
    harness, _, principal = scenario
    pending = email(harness)
    with (
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
    ):
        call(principal, pending.message_id, ADDRESS)
    assert error.value.__cause__.sqlstate == "23514"
    assert not RecipientRefusal.objects.exists()


def test_a_cleared_report_on_the_same_email_answers_409(scenario, google):
    """Once a report is cleared, another of the same address on the same
    email repeats its (event, address): the unique key refuses it, and the
    page's form answers 409 (stale) rather than an outage."""
    harness, status, principal = scenario
    refusal = report(scenario)
    current = SourceCurrent.objects.get()
    with task_login(ServiceRole.WEB, exact=True):
        clear_recipient_refusal(
            harness.service.store,
            principal.pk,
            refusal_id=refusal.pk,
            command_id=uuid4(),
            source_snapshot_id=current.snapshot_id,
            source_generation=current.generation,
            note="Reported in error; the mailbox works.",
            verified=True,
        )
    browser, _ = signed_in()
    path = f"/admin/mail/sent/{status.message_id}/bounce-report/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.get(path.removesuffix("bounce-report/")).status_code == 200
        answer = browser.post(
            path,
            dict(command_id=str(uuid4()), address=ADDRESS, note=NOTE, observed="yes"),
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )
    assert answer.status_code == 409
    assert RecipientRefusal.objects.count() == 1


def test_the_page_reports_in_place_with_csrf(scenario, google):
    """The page's form records a report and answers with the page, whose
    panel lists it; without the CSRF header nothing is recorded."""
    _, status, _ = scenario
    browser, _ = signed_in()
    path = f"/admin/mail/sent/{status.message_id}/"
    values = dict(command_id=str(uuid4()), address=ADDRESS, note=NOTE, observed="yes")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = browser.get(path)
        assert page.status_code == 200
        assert b'id="bounce-report"' in page.content
        assert f'<option value="{ADDRESS}">'.encode() in page.content
        # The Family's only address is offered, so the page warns first.
        assert b'id="bounce-last"' in page.content
        assert b"only address left" in page.content
        assert browser.post(path + "bounce-report/", values).status_code == 403
        for _ in range(2):
            result = browser.post(
                path + "bounce-report/",
                values,
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
            assert result.status_code == 302 and result["Location"] == path
        page = browser.get(path)
        assert b"Bounces reported for this email" in page.content
        assert BOUNCE_UNAVAILABLE["none_left"].encode() in page.content
        assert b"bounce reported" in page.content
        missing = browser.post(
            path + "bounce-report/",
            {key: value for key, value in values.items() if key != "observed"},
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )
        assert missing.status_code == 400
    assert RecipientRefusal.objects.count() == 1


def test_the_migration_check_refuses_the_old_guard(scenario):
    """0081's DO block raises when the guard is still 0079's (the definition
    an upgrade replaces) or the definer has lost SECURITY DEFINER; all
    rolled back."""
    text = (FILE / "0081_bounce_report.sql").read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    old = (FILE / "0079_retry_refusal.sql").read_text(encoding="utf-8")
    start = old.index(
        "CREATE OR REPLACE FUNCTION public.stewardship_recipient_refusal_guard_v1("
    )
    guard = old[start : old.index("END $$;", start) + len("END $$;")]
    with connection.cursor() as cursor:
        cursor.execute(check)
        for statement in (
            guard,
            "ALTER FUNCTION public.stewardship_report_recipient_bounce_v1"
            "(uuid, uuid, text, uuid, text) SECURITY INVOKER",
        ):
            with pytest.raises(DatabaseError, match="0081"), transaction.atomic():
                cursor.execute(statement)
                cursor.execute(check)
        cursor.execute(check)
