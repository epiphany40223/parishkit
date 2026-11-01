"""Additional information counts its requests by follow-up state (#720, 0100).

Frozen migration 0100 replaces ``stewardship_information_report_v2`` in
place so it returns, beside the total, every matching request by follow-up
state. The counts must cover every matching request, not the page, and
follow the filters; the page itself, now the review worklist (#1685), counts
by stage instead and draws no chart. The migration's
closing check must pass on the installed schema and refuse 0094's body or a
changed grant or setting, and the whole file must install over 0094's body
(an upgrade from the previous schema). Each check statement runs in a
transaction that is rolled back.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.information import InformationQuery, information_page
from parishkit.stewardship.responses.information import update_information
from parishkit.stewardship.responses.models import AdditionalInformationItem

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition
from .test_report_workspace_postgresql import read
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = SCHEMA_FILES / "migrations"
MIGRATION = (MIGRATIONS / "0100_information_status_counts.sql").read_text(
    encoding="utf-8"
)
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
# The statement recording the selection's grants and settings, which the
# check compares against (a transaction-local setting).
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0100_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0100_acl"))
        + len(", true);")
    )
]
NAME = "stewardship_information_report_v2"
# 0094's body, the one 0100 replaced.
EARLIER = definition(
    (MIGRATIONS / "0094_source_status_names.sql").read_text(encoding="utf-8"), NAME
)
# 0100's own body: 0197 (#2335) has replaced the selection since, without
# the legacy active branch 0100's check names, so its check tests put this
# back first, rolled back with the test's transaction.
OWN = definition(MIGRATION, NAME)
STATES = ("completed", "needed_open", "not_needed_open")


def three_states(harness):
    """One Family's three requests, each in a different follow-up state.

    Two submissions replace the first request twice, so two are superseded
    and the last is current. Staff then marks the first completed and the
    second needed; the current one is left as submitted (not needed).
    """
    respond(harness, "First request")
    for text in ("Second request", "Third request"):
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = text
        submit(harness, form, answers)
    first, second, _ = AdditionalInformationItem.objects.order_by(
        "submission__submitted_at"
    )
    actor = PortalUser.objects.get(email="admin@example.org").pk
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for item, completed in ((first, True), (second, False)):
            update_information(
                harness.service.store,
                actor,
                item.pk,
                expected_version=item.version,
                request_key=uuid4(),
                follow_up_needed=True,
                followed_up=completed,
                confirm_clear=False,
                notes="",
            )
    return harness


def counted(campaign_id, query):
    """One page of a single row, with the counts over every matching request."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        result = information_page(campaign_id, query, page_size=1)
    return result["total"], len(result["rows"]), result["counts"]


def test_counts_cover_every_matching_request_and_follow_the_filters(
    live_response_service, google
):
    """The counts are the whole selection's, partition it, and obey filters."""
    signed_in()
    harness = three_states(live_response_service)
    campaign = harness.campaign.pk
    assert counted(campaign, InformationQuery(disposition="all")) == (
        3,
        1,
        dict.fromkeys(STATES, 1),
    )
    assert counted(campaign, InformationQuery(disposition="all", completed="no")) == (
        2,
        1,
        {"completed": 0, "needed_open": 1, "not_needed_open": 1},
    )
    assert counted(campaign, InformationQuery(disposition="all", needed="yes")) == (
        2,
        1,
        {"completed": 1, "needed_open": 1, "not_needed_open": 0},
    )
    # The default view: only the current request, which is not needed.
    assert counted(campaign, InformationQuery()) == (
        1,
        1,
        {"completed": 0, "needed_open": 0, "not_needed_open": 1},
    )


def test_the_worklist_draws_no_chart_and_counts_by_stage(live_response_service, google):
    """The review worklist (#1685) has no chart: its Stage choices count."""
    browser, _ = signed_in()
    route = reverse("admin:information_queue")
    respond(live_response_service, "Please call us")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, route)
    assert response.status_code == 200
    assert b"information-followup-spec" not in body
    assert b'id="information-status"' not in body
    assert b">To review (1)</option>" in body and b">Every stage (1)</option>" in body


def check_refuses(statement, message):
    """Run ``statement`` and then 0100's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0100 {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(ACL)
        cursor.execute(OWN)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it holds for 0100's body."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL)
        cursor.execute(OWN)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


def test_the_closing_check_refuses_the_earlier_body():
    """A selection left on 0094's body is not installed as declared."""
    assert "'counts'" not in EARLIER
    check_refuses(EARLIER, "\\(Additional information status counts\\)")


def test_the_closing_check_refuses_changed_grants():
    """CREATE OR REPLACE keeps grants; a change to one is refused."""
    check_refuses(
        f"REVOKE ALL ON FUNCTION public.{NAME}(uuid, jsonb, integer, uuid, integer) "
        "FROM PUBLIC",
        "changed the selection's grants or settings",
    )


def test_the_closing_check_refuses_changed_settings():
    """CREATE OR REPLACE names the same settings; a change to one is refused."""
    check_refuses(
        f"ALTER FUNCTION public.{NAME}(uuid, jsonb, integer, uuid, integer) "
        "SET jit TO on",
        "changed the selection's grants or settings",
    )


def test_the_migration_installs_over_the_earlier_definition():
    """Run whole over 0094's body (the previous schema), the file installs.

    Rolled back afterwards, so later tests see the installed schema.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(EARLIER)
        cursor.execute(
            "SELECT prosrc LIKE '%%''counts''%%' FROM pg_proc "
            "WHERE oid=%s::regprocedure",
            [f"public.{NAME}(uuid,jsonb,integer,uuid,integer)"],
        )
        assert cursor.fetchone() == (False,)
        cursor.execute(MIGRATION)
        cursor.execute(
            "SELECT prosrc LIKE '%%''counts''%%' FROM pg_proc "
            "WHERE oid=%s::regprocedure",
            [f"public.{NAME}(uuid,jsonb,integer,uuid,integer)"],
        )
        assert cursor.fetchone() == (True,)
        transaction.set_rollback(True)
