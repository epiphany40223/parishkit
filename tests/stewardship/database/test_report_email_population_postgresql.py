"""Report emails count today's campaign Families and name request stages (#2331).

The Administrator (2026-10-31) chose that a report email's figures follow
"In this campaign today", as the report pages do by default (#2324), with
a fixed line naming the Families counted and the report day, and that the
open requests block names the stages it adds up ("To review N · To handle
M") and leaves out Ignored requests, as the Additional information page
and the menu badge do. Frozen migration 0196 makes the snapshot guard pin
the current generation and read ``stewardship_report_email_requests_v2``,
and lets a report email's capture build both scopes (its Compared with past
campaigns block stays historical, like the archives). These tests send
real report emails through the scheduler and worker logins.
"""

# ruff: noqa: F811 -- the imported fixtures are injected by pytest name.

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts import admin_context
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Action
from parishkit.stewardship.reports.models import CampaignDailyFactSet
from parishkit.stewardship.reports.report_email_models import ReportEmailSnapshot
from parishkit.stewardship.responses.models import AdditionalInformationItem

from .campaign_builders import campaign_clock
from .report_builders import DAILY_INSTANT, WEEKLY_INSTANT, add_digest, allocated
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_information_limitation_review_postgresql import decide, staff_actor
from .test_ministry_reports_postgresql import actor as principal_for
from .test_source_families_postgresql import prepare, promote
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def kept_requests(snapshot):
    """The open requests block's kept items in ``snapshot``."""
    (data,) = [
        entry["data"]
        for entry in snapshot.blocks
        if entry["block"] == "additional_information"
    ]
    return (data or {}).get("items", [])


@pytest.mark.parametrize("stage", ["to_review", "to_handle", "ignored"])
def test_the_email_counts_the_requests_the_page_and_the_badge_count(
    live_response_service, google, stage
):
    """One request at each stage: the email lists it only to review or to
    handle, under a heading naming both stages' counts, and those counts
    are the Additional information page's (the menu's read by stage); the
    badge is its To review count. An Ignored request is in none of them."""
    harness = live_response_service
    # A Staff user, added before the report's users-form change.
    staff = principal_for(harness, ("staff",), ())
    respond(harness, "Please call about the parish picnic")
    item = AdditionalInformationItem.objects.get()
    if stage != "to_review":
        action = Action.ACCEPT if stage == "to_handle" else Action.IGNORE
        decide(harness, staff_actor(), action, item)
    with campaign_clock(WEEKLY_INSTANT):
        preparation = allocated(harness, weekly=True)
    snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
    menu = SimpleNamespace()
    entries = [SimpleNamespace(name="information_queue", url="/information/")]
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        admin_context._open_counts(menu, staff, entries, harness.campaign)
    stages = menu._stewardship_open_stages["information_queue"]
    assert stages == {stage: 1}
    review, handle = stages.get("to_review", 0), stages.get("to_handle", 0)
    assert menu._stewardship_open_counts["information_queue"] == review
    listed = kept_requests(snapshot)
    assert [entry[5] for entry in listed] == ([] if stage == "ignored" else [stage])
    if stage == "ignored":
        assert "No open requests." in snapshot.text
        assert "To review" not in snapshot.text
    else:
        assert f"To review {review} · To handle {handle}" in snapshot.text
        assert [entry[0] for entry in listed] == [str(item.pk)]


def test_the_email_counts_todays_campaign_families_and_compares_like_for_like(
    live_response_service, google
):
    """A Family that responded and then left the campaign: the send's pinned
    generation counts today's campaign Families (it is not counted), under
    the Families counted line; the historical generation its Compared with
    past campaigns block reads, at the same inputs, still counts it."""
    harness = live_response_service
    respond(harness, "Thank you")
    data = response_source()
    data.family_groups[7] = "Inactive"
    source, lease = prepare(data)
    promote(source, lease, harness.campaign, harness.rings)
    identifier = add_digest(harness.service.store, harness.campaign, comparison=True)
    with campaign_clock(DAILY_INSTANT):
        preparation = allocated(harness, identifier=identifier)
    assert preparation.phase in {"fanout", "complete"}
    snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
    pinned = CampaignDailyFactSet.objects.get(pk=snapshot.fact_set_id)
    assert pinned.population_scope == "current"
    assert pinned.source_id == source.pk == preparation.source_id
    compared = CampaignDailyFactSet.objects.get(
        campaign=harness.campaign,
        population_scope="historical",
        source_id=pinned.source_id,
        submission_watermark=pinned.submission_watermark,
        review_watermark=pinned.review_watermark,
        through_date=pinned.through_date,
        state="ready",
    )

    def last(generation):
        """The generation's last day: (Families out of, Families responded)."""
        day = generation.days.order_by("-local_date").first()
        return day.cohort_denominator, day.cumulative_responses

    assert last(pinned) == (0, 0)
    assert last(compared) == (1, 1)
    day = re.search(r"as of the end of (\w+ \d+, \d{4})", snapshot.text)[1]
    line = f"Families counted: In this campaign today, as of the end of {day}"
    assert snapshot.text.count(line) == 1
    assert "Families that have responded: 0 out of 0" in snapshot.text


MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
MIGRATION = MIGRATIONS / "0196_report_email_population.sql"
# stewardship_fact_input_pin_commit_v1 before 0196, exactly as the schema
# baseline (report_facts.sql) installed it: no frozen file replaced it, so
# its old body lives in no other file (only its header is rewrapped).
OLD_PIN_COMMIT = """CREATE OR REPLACE FUNCTION
public.stewardship_fact_input_pin_commit_v1()
RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND NEW.parent_kind='facts'
        AND NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
            JOIN stewardship_task_run t ON t.id=f.task_id
            WHERE f.id=NEW.parent_id AND f.source_id=NEW.snapshot_id
                AND f.population_scope='current' AND f.state='building'
                AND t.task_type IN ('report_facts','report_exact_export')
                AND stewardship_fact_live(f.task_id,f.task_fence,f.worker_id)) THEN
        RAISE EXCEPTION 'Worker fact input pin requires its exact retained builder'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;"""


def frozen_body(name, function):
    """The last definition of ``function`` in frozen file ``name``.

    It is the body a database that applied the file (and no later one
    replacing it) holds, as CREATE OR REPLACE.
    """
    text = (MIGRATIONS / name).read_text(encoding="utf-8")
    found = list(
        re.finditer(
            rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{function}\(", text, re.M
        )
    )
    start = found[-1].start()
    body = text[start : text.index("$$;", start) + 3]
    return body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def stageless_guard():
    """0185's snapshot guard moved to requests v2 and the current scope.

    Only its stage comparison (``item->5``) is missing, so the DO block's
    stage check alone must refuse it.
    """
    body = frozen_body(
        "0185_series_in_force.sql", "stewardship_report_email_snapshot_guard_v1"
    )
    for old, new in (
        (
            "stewardship_report_email_requests_v1(",
            "stewardship_report_email_requests_v2(",
        ),
        (
            "ROW(p.campaign_id,'historical'::varchar",
            "ROW(p.campaign_id,'current'::varchar",
        ),
    ):
        assert body.count(old) == 1
        body = body.replace(old, new)
    return body


# Each install 0196's DO block must refuse: the real bodies a database
# held before 0196 (the latest frozen file defining each, or the schema
# baseline's), one at a time, and two partial installs.
STALE_0196 = {
    "requests-v2-missing": lambda: (
        "DROP FUNCTION public.stewardship_report_email_requests_v2"
        "(uuid,uuid,timestamptz);"
    ),
    "requests-v2-as-v1": lambda: frozen_body(
        "0156_report_pipeline.sql", "stewardship_report_email_requests_v1"
    ).replace(
        "stewardship_report_email_requests_v1(",
        "stewardship_report_email_requests_v2(",
        1,
    ),
    "snapshot-guard-0185": lambda: frozen_body(
        "0185_series_in_force.sql", "stewardship_report_email_snapshot_guard_v1"
    ),
    "snapshot-guard-without-stages": stageless_guard,
    "runtime-binding-0185": lambda: frozen_body(
        "0185_series_in_force.sql", "stewardship_fact_runtime_binding_v1"
    ),
    "source-pin-guard-0161": lambda: frozen_body(
        "0161_report_conversion.sql", "stewardship_source_pin_guard"
    ),
    "pin-commit-baseline": lambda: OLD_PIN_COMMIT,
}


@pytest.mark.parametrize("stale", STALE_0196)
def test_migration_0196_self_check_refuses_a_stale_install(stale):
    """0196's DO block passes on the installed schema and fails when any one
    function is as it was before 0196 (or v2 is missing, or the guard
    ignores the stages). The file's own transaction-local ACL capture runs
    first, as in the migration; each stale install runs in a rolled-back
    transaction."""
    text = MIGRATION.read_text(encoding="utf-8")
    acl = re.search(r"^SELECT set_config\(.*?, true\);", text, re.M | re.S)[0]
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    # A missing v2 fails the block's regprocedure cast, not its message.
    refused = "does not exist" if stale == "requests-v2-missing" else "Migration 0196"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(acl)
        cursor.execute(check)
        cursor.execute(STALE_0196[stale]())
        with (
            pytest.raises(DatabaseError, match=refused),
            transaction.atomic(),
        ):
            cursor.execute(check)
        transaction.set_rollback(True)
