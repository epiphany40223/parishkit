"""Real live information, authorized Staff revisions and independent SQL guards."""

import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.information import (
    InformationQuery,
    information_history,
    information_page,
)
from parishkit.stewardship.reports.weekly_observation import capture_weekly_observation
from parishkit.stewardship.responses.information import update_information
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    AdditionalInformationRevision,
)
from parishkit.stewardship.storage import StaleRecordError

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_report_workspace_postgresql import read
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def search(browser, route, values):
    """Consume native CSRF POST search using the response-owned transport."""
    server, peer = socket.socketpair()
    try:
        response = post(browser, route, values, **{"gunicorn.socket": server})
        body = (
            b"".join(response.streaming_content)
            if response.streaming
            else response.content
        )
        response.close()
        assert not connection.in_atomic_block
        return response, body
    finally:
        server.close()
        peer.close()


def test_followup_history_replay_confirmation_and_sql_pairing(
    live_response_service, google
):
    """A shared live fixture proves durable editing and unchanged denial cases."""
    harness = live_response_service
    submission = respond(harness, "Please contact our household")
    browser, login = signed_in()
    assert login.status_code == 302
    actor = PortalUser.objects.get(email="admin@example.org").pk
    item = AdditionalInformationItem.objects.get(submission=submission)
    intent = dict(
        expected_version=item.version,
        request_key=uuid4(),
        follow_up_needed=True,
        followed_up=True,
        confirm_clear=False,
        notes="Called the Family",
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        first = update_information(harness.service.store, actor, item.pk, **intent)
        assert (
            update_information(harness.service.store, actor, item.pk, **intent).pk
            == first.pk
        )
        item.refresh_from_db()
        assert item.version == intent["expected_version"] + 1
        assert item.follow_up_needed and item.followed_up_at == first.created_at
        assert first.followed_up_by_id == actor
        for changes, error in (
            ({"notes": "Different replay"}, ValueError),
            ({"request_key": uuid4()}, StaleRecordError),
            (
                {
                    "expected_version": item.version,
                    "request_key": uuid4(),
                    "followed_up": False,
                },
                ValueError,
            ),
        ):
            with pytest.raises(error):
                update_information(
                    harness.service.store, actor, item.pk, **(intent | changes)
                )
        second = update_information(
            harness.service.store,
            actor,
            item.pk,
            **(
                intent
                | {
                    "expected_version": item.version,
                    "request_key": uuid4(),
                    "notes": "Added a note",
                }
            ),
        )
        assert (second.followed_up_at, second.followed_up_by_id) == (
            first.followed_up_at,
            actor,
        )
        item.refresh_from_db()
        final = update_information(
            harness.service.store,
            actor,
            item.pk,
            **(
                intent
                | {
                    "expected_version": item.version,
                    "request_key": uuid4(),
                    "followed_up": False,
                    "confirm_clear": True,
                }
            ),
        )
        item.refresh_from_db()
        assert final.followed_up_at is None and final.followed_up_by_id is None
        assert item.followed_up_at is None
        assert AdditionalInformationRevision.objects.count() == 3
        page = information_page(harness.campaign.pk, InformationQuery(search="Called"))
        assert page["total"] == 1 and page["rows"][0]["id"] == str(item.pk)
        assert page["rows"][0]["notes"] == "Called the Family"
        route = f"/admin/reports/{harness.campaign.pk}/information/"
        response, body = read(browser, route)
        assert response.status_code == 200 and b"Please contact" in body
        assert response["Cache-Control"] == "no-store"
        response, body = read(browser, route + f"{item.pk}/")
        assert response.status_code == 200 and b"Called the Family" in body
        assert b"Staff edit history" in body
        rows, more = information_history(item.pk, 1, version=first.expected_version + 1)
        assert [row.pk for row in rows] == [first.pk] and not more
        assert information_history(item.pk, 2, version=item.version) == ([], False)
        wrong_route = f"/admin/reports/{uuid4()}/information/{item.pk}/"
        assert read(browser, wrong_route)[0].status_code == 403
        assert (
            post(
                browser,
                wrong_route + "update",
                {
                    "expected_version": str(item.version),
                    "request_key": str(uuid4()),
                    "notes": "Wrong campaign",
                },
            ).status_code
            == 403
        )
        for values, found in (
            ({"search": "Called"}, True),
            ({"completed": "yes"}, False),
        ):
            response, body = search(browser, route, values)
            assert response.status_code == 200
            assert (b"Please contact" in body) is found
            assert b"?search=" not in body
        for suffix in ("?search=Private", "?page=1"):
            assert read(browser, route + suffix)[0].status_code == 400
        update_path = route + f"{item.pk}/update"
        values = {
            "expected_version": str(item.version),
            "request_key": str(uuid4()),
            "notes": "Native browser edit",
            "follow_up_needed": "yes",
        }
        assert browser.post(update_path, values).status_code == 403
        assert post(browser, update_path, values).status_code == 302
        assert post(browser, update_path, values).status_code == 302
        assert (
            post(
                browser, update_path, values | {"request_key": str(uuid4())}
            ).status_code
            == 409
        )
        item.refresh_from_db()
        # Direct SQL cannot rewrite history or alter checkboxes without a receipt.
        for statement in (
            "UPDATE stewardship_information_revision SET notes='overwritten'",
            "DELETE FROM stewardship_information_revision",
            "UPDATE stewardship_additional_information "
            "SET follow_up_needed=false, version=version+1",
        ):
            with (
                pytest.raises(DatabaseError),
                work_transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        # A standalone revision cannot commit without its item projection/audit.
        with pytest.raises(DatabaseError), work_transaction():
            AdditionalInformationRevision.objects.create(
                item=item,
                actor_id=actor,
                request_key=uuid4(),
                expected_version=item.version,
                follow_up_needed=True,
                followed_up=False,
                confirm_clear=False,
                notes="Orphan",
            )
    assert AdditionalInformationRevision.objects.count() == 4
    first.refresh_from_db()
    assert first.notes == "Called the Family" and first.followed_up_at is not None
    events = AuditEvent.objects.filter(event_type="information_updated")
    assert events.count() == 4
    assert "Called the Family" not in str(
        list(
            AuditContext.objects.filter(event__in=events).values_list(
                "context", flat=True
            )
        )
    )
    observation = capture_weekly_observation(harness.campaign.pk)
    assert (
        observation.items[0].value.text == submission.answers["additional_information"]
    )
    assert item.disposition == "current_actionable"


def test_competing_staff_edits_and_family_replacement(live_response_service, google):
    """Independent writers cannot overwrite one another or a changed Family request."""
    from .test_response_revisit_postgresql import revisit
    from .test_response_submission_postgresql import submit

    harness = live_response_service
    respond(harness, "Original request")
    signed_in()
    actor = PortalUser.objects.get(email="admin@example.org").pk
    item = AdditionalInformationItem.objects.get()
    start = Barrier(2)
    intent = dict(
        expected_version=item.version,
        follow_up_needed=True,
        followed_up=False,
        confirm_clear=False,
    )

    def edit(note):
        """Each competing edit has its own actual web-role database connection."""
        try:
            connection.ensure_connection()
            start.wait(timeout=10)
            try:
                return update_information(
                    harness.service.store,
                    actor,
                    item.pk,
                    request_key=uuid4(),
                    notes=note,
                    **intent,
                ).pk
            except StaleRecordError:
                return None
        finally:
            connections.close_all()

    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        futures = [
            pool.submit(edit, note) for note in ("First writer", "Second writer")
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert AdditionalInformationRevision.objects.count() == 1
    item.refresh_from_db()
    stale_version = item.version
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = "Replacement request"
    submit(harness, form, answers)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        with pytest.raises(StaleRecordError):
            update_information(
                harness.service.store,
                actor,
                item.pk,
                **(intent | {"expected_version": stale_version}),
                request_key=uuid4(),
                notes="Stale form",
            )
        current = information_page(harness.campaign.pk, InformationQuery())
        history = information_page(
            harness.campaign.pk, InformationQuery(disposition="all")
        )
    assert current["total"] == 1 and current["rows"][0]["text"] == "Replacement request"
    assert history["total"] == 2
    old = next(row for row in history["rows"] if row["id"] == str(item.pk))
    assert old["disposition"] == "superseded"
    assert old["replacement_id"] == current["rows"][0]["id"]
    assert old["notes"] in {"First writer", "Second writer"}
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = ""
    submit(harness, form, answers)
    assert information_page(harness.campaign.pk, InformationQuery())["total"] == 0
    assert (
        information_page(
            harness.campaign.pk, InformationQuery(disposition="withdrawn")
        )["total"]
        == 1
    )
