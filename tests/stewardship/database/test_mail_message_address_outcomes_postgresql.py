"""Per-address outcomes on the Mail message page (#806, frozen migration 0068).

``stewardship_delivery_recipient_outcomes_v1`` returns, for an email's newest
rendering, each routed address with what its latest attempt's validated
evidence recorded for it. These tests build real accepted and refused
attempts with genuine SMTP result evidence (``result_evidence``), check the
function's rows and refusals, check that the page shows the outcomes after
the addresses and how many an accepted attempt reached, and run the file's
DO block against the installed function (it passes) and against a missing
or different one (it raises).
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.family_mail_results import result_evidence
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity

from ..policy_factory import address
from ..test_outbox_validation import rendering
from .auth_builders import signed_in
from .campaign_builders import change as change_policy
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_outbox_postgresql import change, permit, provider_evidence, submit

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations"
    / "0068_mail_message_address_outcomes.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
FUNCTION = "public.stewardship_delivery_recipient_outcomes_v1(uuid, uuid)"
OUTCOMES = (
    "SELECT address, outcome FROM stewardship_delivery_recipient_outcomes_v1(%s,%s)"
)
ADDRESSES = ("first@example.org", "second@example.org")
# The To: line with no outcome after its addresses.
BARE_TO = '<p id="email-recipients">To: first@example.org, second@example.org</p>'

pytestmark = pytest.mark.django_db(transaction=True)


def message(harness, *, testing=False):
    """A Family Initial to two addresses with no attempt yet.

    In Testing mode it is routed to one Testing recipient instead, as the
    real preparation routes it.
    """
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    key = uuid4()
    status = create_message(
        identity=DeliveryIdentity(
            scope_id=harness.campaign.pk,
            campaign_id=harness.campaign.pk,
            family_id=family.pk,
            semantic_key=key,
            mode="testing" if testing else "production",
            routing="testing_override" if testing else "production",
            purpose="initial",
        ),
        render=rendering(
            configuration_id=harness.campaign.active_configuration.configuration_id,
            intended_recipients=ADDRESSES,
            routed_recipients=("testing@example.org",) if testing else ADDRESSES,
        ),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=permit,
    )
    return status, key


def attempted(
    harness, status=FamilyDeliveryStatus.ACCEPTED, *, testing=False, **refused
):
    """One finished attempt of a new message with genuine result evidence.

    ``refused`` gives the ``permanent`` and ``transient`` envelope positions,
    as the mail helper reports them.
    """
    created, key = message(harness, testing=testing)
    result = FamilyDeliveryResult(status, 1 if testing else 2, **refused)
    action = {
        FamilyDeliveryStatus.ACCEPTED: DeliveryAction.ACCEPT,
        FamilyDeliveryStatus.UNKNOWN: DeliveryAction.MARK_UNKNOWN,
        FamilyDeliveryStatus.PERMANENT: DeliveryAction.FAIL_UNACCEPTED,
    }[status]
    change(
        submit(created),
        action,
        evidence=result_evidence(result, semantic_key=key, with_stats=False),
    )
    return created.message_id


def outcomes_as(service, message_id, actor_id, *, exact=True):
    """Call the outcomes function as ``service``'s login; its rows or SQLSTATE."""
    with task_login(service, exact=exact):
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(OUTCOMES, (message_id, actor_id))
                return cursor.fetchall()
        except DatabaseError as error:
            return error.__cause__.sqlstate


def page(browser, message_id):
    """The Mail message page's HTML as the web login serves it."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(f"/admin/mail/outgoing/{message_id}/")
    assert response.status_code == 200
    return response.content.decode()


def test_each_address_shows_what_the_latest_attempt_recorded(response_service, google):
    """An accepted attempt that one address refused for now: the function
    names each address's outcome, and the page follows each To: address with
    it and says how many addresses the email reached."""
    harness = activate_response_service(response_service)
    browser, _ = signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    partial = attempted(harness, transient=(1,))
    assert outcomes_as(ServiceRole.WEB, partial, admin) == [
        ("first@example.org", "delivered"),
        ("second@example.org", "refused_for_now"),
    ]
    html = page(browser, partial)
    assert (
        '<p id="email-recipients">To: first@example.org (delivered), '
        "second@example.org (refused for now)</p>"
    ) in html
    assert ('<p id="email-reached">Delivered to 1 of 2 addresses.</p>') in html
    # Refused for good: the same count.
    html = page(browser, attempted(harness, permanent=(0,)))
    assert (
        "first@example.org (refused permanently), second@example.org (delivered)"
    ) in html
    assert '<p id="email-reached">Delivered to 1 of 2 addresses.</p>' in html
    # Every address reached it: no count line.
    html = page(browser, attempted(harness))
    assert "first@example.org (delivered), second@example.org (delivered)" in html
    assert 'id="email-reached"' not in html
    # Not accepted: no address got it.
    whole = attempted(harness, FamilyDeliveryStatus.PERMANENT)
    assert [row[1] for row in outcomes_as(ServiceRole.WEB, whole, admin)] == [
        "not_delivered",
        "not_delivered",
    ]
    assert 'id="email-reached"' not in page(browser, whole)
    # The provider could not say whether it arrived, except where it refused.
    unsure = attempted(harness, FamilyDeliveryStatus.UNKNOWN, transient=(0,))
    assert outcomes_as(ServiceRole.WEB, unsure, admin) == [
        ("first@example.org", "refused_for_now"),
        ("second@example.org", "unknown"),
    ]
    assert "second@example.org (not sure it arrived)" in page(browser, unsure)


def evidence(key, status, count=2, **refused):
    """Genuine SMTP result evidence for an attempt of the email keyed ``key``."""
    return result_evidence(
        FamilyDeliveryResult(status, count, **refused),
        semantic_key=key,
        with_stats=False,
    )


def test_the_latest_attempt_on_a_rendering_wins(response_service, google):
    """An attempt refused for now leaves the email waiting to retry: its
    addresses are "not delivered yet". The retry, on the same rendering, is
    accepted except at one address, and only that latest attempt is shown."""
    harness = activate_response_service(response_service)
    browser, _ = signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    created, key = message(harness)
    first = submit(created)
    task = TaskRun.objects.get(pk=first.run_id)
    waiting = change(
        first,
        DeliveryAction.RETRY_UNACCEPTED,
        retry_seconds=1,
        evidence=evidence(key, FamilyDeliveryStatus.TRANSIENT),
    )
    assert outcomes_as(ServiceRole.WEB, created.message_id, admin) == [
        (value, "not_delivered") for value in ADDRESSES
    ]
    assert (
        "To: first@example.org (not delivered yet), second@example.org"
        " (not delivered yet)</p>"
    ) in page(browser, created.message_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    second = change(
        waiting,
        DeliveryAction.SUBMIT,
        run_id=task.pk,
        task_fence=task.fence,
        actor_id=task.worker_id,
        provider_seconds=30,
    )
    assert second.attempt == 2 and second.render_id == first.render_id
    change(
        second,
        DeliveryAction.ACCEPT,
        evidence=evidence(key, FamilyDeliveryStatus.ACCEPTED, transient=(1,)),
    )
    assert outcomes_as(ServiceRole.WEB, created.message_id, admin) == [
        ("first@example.org", "delivered"),
        ("second@example.org", "refused_for_now"),
    ]
    html = page(browser, created.message_id)
    assert "first@example.org (delivered), second@example.org (refused for now)" in html
    assert "not delivered yet" not in html


@pytest.mark.parametrize("valid", ["synthetic", "wrong_count"])
def test_evidence_that_does_not_validate_gives_no_outcome(
    response_service, google, valid
):
    """Evidence the validator refuses (not a result at all, or a result for
    an envelope of another size) gives no rows, and the addresses no label."""
    harness = activate_response_service(response_service)
    browser, _ = signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    created, key = message(harness)
    change(
        submit(created),
        DeliveryAction.ACCEPT,
        evidence=provider_evidence()
        if valid == "synthetic"
        else evidence(key, FamilyDeliveryStatus.ACCEPTED, count=3),
    )
    assert outcomes_as(ServiceRole.WEB, created.message_id, admin) == []
    html = page(browser, created.message_id)
    assert BARE_TO in html and 'id="email-reached"' not in html


def test_testing_mode_outcomes_follow_the_testing_recipient(response_service, google):
    """In Testing mode the email went to the Testing recipient, so the
    outcome follows that address and the To: line has none. The campaign
    is still in Testing (not activated), where Testing mail is admitted."""
    harness = response_service
    browser, _ = signed_in()
    html = page(browser, attempted(harness, testing=True))
    assert BARE_TO in html
    assert (
        '<p id="email-routed">Testing mode sends it to: testing@example.org'
        " (delivered)</p>"
    ) in html


def test_no_outcome_before_the_first_attempt(response_service, google):
    """A rendering with no finished attempt has no outcomes; the page shows
    the addresses alone."""
    harness = activate_response_service(response_service)
    browser, _ = signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    created, _ = message(harness)
    assert outcomes_as(ServiceRole.WEB, created.message_id, admin) == []
    assert outcomes_as(ServiceRole.WEB, uuid4(), admin) == []
    html = page(browser, created.message_id)
    assert BARE_TO in html


def test_only_the_web_login_reads_outcomes_for_an_administrator(
    response_service, google
):
    """The function refuses (42501) what 0058's recipient read refuses: a
    Staff user, an unknown or missing actor, other logins, and a login that
    holds the web grants without being the web login."""
    harness = activate_response_service(response_service)
    signed_in()
    admin = PortalUser.objects.get(email="admin@example.org").pk
    message_id = attempted(harness, transient=(1,))
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
    for actor in (staff, uuid4(), None):
        assert outcomes_as(ServiceRole.WEB, message_id, actor) == "42501"
    for service in (ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH):
        assert outcomes_as(service, message_id, admin) == "42501"
    assert outcomes_as(ServiceRole.WEB, message_id, admin, exact=False) == "42501"


def test_the_check_passes_on_the_installed_function():
    """A migrated database has the function exactly as 0068 declares it."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)


@pytest.mark.parametrize(
    "change_sql",
    [
        # Before 0068 the function does not exist at all.
        f"DROP FUNCTION {FUNCTION}",
        f"ALTER FUNCTION {FUNCTION} SECURITY INVOKER",
        f"ALTER FUNCTION {FUNCTION} SET search_path TO public",
        f"ALTER FUNCTION {FUNCTION} VOLATILE",
        f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO PUBLIC",
        # A body that does not read the evidence through its validator.
        "CREATE OR REPLACE FUNCTION public.stewardship_delivery_recipient_outcomes_v1("
        "message_uuid uuid, actor_uuid uuid) RETURNS TABLE(address text, outcome text)"
        " LANGUAGE plpgsql STABLE SECURITY DEFINER"
        " SET search_path TO pg_catalog,public,pg_temp AS $$ BEGIN RETURN; END $$",
    ],
)
def test_the_check_refuses_a_missing_or_different_function(change_sql):
    """Without the function, or with any declared attribute or its body
    changed, the DO block raises, so the migration would not commit."""
    with (
        pytest.raises(DatabaseError, match="0068"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(change_sql)
        cursor.execute(CHECK)
