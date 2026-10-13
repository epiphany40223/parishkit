"""The daily digest's Compared with past campaigns section (#1018 slice 7).

On PostgreSQL: a daily digest schedule with the section turned on, a real
pinned fact set and synthetic imported archives (``archive_factory``; every
value invented). The worker login reads the comparison from the digest's own
facts (the definer functions and the archive tables, by frozen file 0072's
and the worker's grants), the ready row retains the section and its chart
PNGs, the per-Admin mail is allocated through the closed boundary, and the
saved digest page shows the retained section unchanged after its imported
year is removed and imported again from a different file.

The chart engine's server renderer is replaced by a stand-in PNG here (its
own tests run the real helper), except for the kill, which runs a real
helper process that outlives its limit.
"""

import json
import sys
from functools import partial
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection
from django.urls import reverse
from PIL import Image

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.archive import imports
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.reports import chart_rendering
from parishkit.stewardship.reports.daily_digest import render_daily_digest
from parishkit.stewardship.reports.digest_building import (
    admit_daily_facts,
    begin_daily_facts,
    load_daily_document,
    retain_daily_content,
)
from parishkit.stewardship.reports.digest_capture import capture_daily_snapshot
from parishkit.stewardship.reports.digest_comparison import DIGEST_CHART, HEADING
from parishkit.stewardship.reports.digest_fanout import fanout_daily
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestReady,
    DailyDigestRecipient,
)
from parishkit.stewardship.reports.digest_planning import cover_dates, discover_dates
from parishkit.stewardship.reports.digest_tasks import daily_handler
from parishkit.stewardship.reports.facts import FactUnavailable
from parishkit.stewardship.reports.materialization import materialize_fact_set
from parishkit.stewardship.web.digest_content import COMPARISON_CHART_IDS

from ..campaign_factory import schedule
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_campaign_comparison_postgresql import (
    archive_bytes,
    import_archive,
    in_process,
)
from .test_daily_digest_fanout_postgresql import configure_content
from .test_daily_digest_planning_postgresql import INSTANT, allocate
from .test_daily_digest_tasks_postgresql import execute
from .test_daily_digest_views_postgresql import read
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

# The current campaign's stewardship year; the archives are the years before.
CURRENT_YEAR = 2055


@pytest.fixture(autouse=True)
def archives(monkeypatch):
    """Check workbooks in this process, with a fixed current year."""
    monkeypatch.setattr(imports, "check_archive", in_process)
    monkeypatch.setattr(imports, "stewardship_year", lambda: CURRENT_YEAR)
    monkeypatch.setattr(imports, "year_from_values", lambda values: CURRENT_YEAR)


@pytest.fixture
def renderer(monkeypatch):
    """A stand-in for the server renderer: one PNG per chart, calls kept."""
    calls = []

    def render_chart(spec, *, format, what, limit_seconds=60):
        """A white PNG at the email width."""
        calls.append(what)
        stream = BytesIO()
        Image.new("RGB", (1760, 640), "white").save(stream, format="PNG")
        return chart_rendering.ChartImage(stream.getvalue(), "png", 880, 320)

    monkeypatch.setattr(chart_rendering, "render_chart", render_chart)
    return calls


def compile_digest(harness, *, comparison=True):
    """Prepare, capture and compile one daily digest, as ``build`` does.

    Its schedule has the section on (``comparison``), as Dates and mail
    schedules saves it; the rest is the real owners' path.
    """
    row = schedule(
        str(harness.campaign.pk),
        kind="daily_digest",
        date=None,
        weekday=None,
        time="00:15:00",
        **({"comparison": True} if comparison else {}),
    )
    assert (
        change(
            harness.service.store,
            harness.service.store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **row}],
        ).state
        == "applied"
    )
    # The digest's email, which the per-Admin envelope wraps.
    configure_content(harness)
    claim = allocate()
    with task_login(ServiceRole.WORKER, exact=True):
        state = DailyDigestPreparation.objects.get(task_id=claim.run_id)
        for _ in range(10):
            if state.phase not in {"dates", "cover"}:
                break
            with work_transaction():
                step = discover_dates if state.phase == "dates" else cover_dates
                state = step(claim)
        with work_transaction():
            capture_daily_snapshot(claim)
            facts = begin_daily_facts(claim)
        materialize_fact_set(facts.pk, claim, admit=partial(admit_daily_facts, claim))
        with work_transaction():
            document = load_daily_document(claim, facts.pk)
    content = render_daily_digest(document, public_origin="https://parish.example")
    return claim, document, content


def administrator():
    """The signed-in Administrator's id, for the archive service."""
    signed_in()
    return PortalUser.objects.get(email="admin@example.org").pk


def test_the_section_is_retained_mailed_and_shown_unchanged(
    family_mail,  # noqa: F811
    google,
    renderer,
):
    harness = family_mail
    store = harness.service.store
    with campaign_clock(INSTANT):
        actor = administrator()
        first = import_archive(
            store, actor, archive_bytes(CURRENT_YEAR - 1, [1, 2, 99])
        )
        older = import_archive(store, actor, archive_bytes(CURRENT_YEAR - 2, [1, 98]))
        claim, document, content = compile_digest(harness)
        section = document.comparison
        assert section is not None
        assert section["archives"] == [
            {"year": CURRENT_YEAR - 1, "checksum": first.checksum},
            {"year": CURRENT_YEAR - 2, "checksum": older.checksum},
        ]
        # The comparison day is the digest's report day, and its current
        # figures are the digest's own fact row's.
        assert section["report_date"] == document.report_day.local_date.isoformat()
        tiles = {tile["label"]: tile for tile in section["tiles"]}
        assert tiles["Families responded"]["value"] == str(
            document.report_day.cumulative_responses
        )
        assert tiles["Families responded"]["lines"][0].startswith(
            f"Last year on day {section['day']}: "
        )
        # No Financial stewardship here: the Families chart alone.
        assert [chart["measure"] for chart in section["charts"]] == ["families"]
        assert renderer == [DIGEST_CHART]
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            ready = retain_daily_content(claim, document, content)
        ready = DailyDigestReady.objects.get(pk=ready.pk)
        assert ready.comparison == json.loads(json.dumps(section))
        assert ready.comparison_images() == (
            (COMPARISON_CHART_IDS[0], content.comparison[0].data),
        )
        assert ready.comparison_pledges_chart is None
        assert HEADING in ready.html and HEADING in ready.text
        # Each Admin's mail is allocated through the closed boundary, which
        # admits the section's image with the participation chart.
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "complete"
        recipient = DailyDigestRecipient.objects.select_related("outbox__render").get()
        assert recipient.outbox.render.html.endswith(ready.html)

        # Later imports never change the sent digest: remove last year and
        # import a different file for it.
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            imports.remove(store, actor, CURRENT_YEAR - 1, expected_version=2)
        replacement = import_archive(
            store, actor, archive_bytes(CURRENT_YEAR - 1, [1, 2, 3, 4, 5, 6, 7])
        )
        assert replacement.checksum != first.checksum
        browser, _ = signed_in()
        path = reverse("admin:daily_digest_snapshot", args=[ready.snapshot_id])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, page = read(browser, path)
        page = page.decode()
        assert "data-digest-comparison" in page
        assert first.checksum in page and replacement.checksum not in page
        for tile in section["tiles"]:
            for line in tile["lines"]:
                assert line.replace("'", "&#x27;") in page
        assert 'id="comparison-families-spec"' in page
        assert f"/admin/reports/comparison/?day={section['day']}" in page


def test_without_the_setting_there_is_no_section(response_service, google, renderer):
    harness = response_service
    with campaign_clock(INSTANT):
        actor = administrator()
        import_archive(
            harness.service.store, actor, archive_bytes(CURRENT_YEAR - 1, [1, 2])
        )
        claim, document, content = compile_digest(harness, comparison=False)
        assert document.comparison is None and content.comparison == ()
        assert renderer == []
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            ready = retain_daily_content(claim, document, content)
        ready = DailyDigestReady.objects.get(pk=ready.pk)
        assert ready.comparison is None and ready.comparison_images() == ()
        browser, _ = signed_in()
        path = reverse("admin:daily_digest_snapshot", args=[ready.snapshot_id])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, page = read(browser, path)
        assert b"data-digest-comparison" not in page


def test_with_no_past_campaign_the_section_is_left_out(
    response_service, google, renderer
):
    harness = response_service
    with campaign_clock(INSTANT):
        _, document, content = compile_digest(harness)
        assert document.comparison is None and content.comparison == ()
        assert HEADING not in content.html


def test_a_killed_chart_render_leaves_a_durable_timeout_entry(
    response_service, monkeypatch
):
    """The renderer's kill names digest_chart and chart_render_worker."""
    monkeypatch.setattr(
        chart_rendering,
        "COMMAND",
        (sys.executable, "-c", "import time; time.sleep(30)"),
    )
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(chart_rendering.ChartRenderError, match="time limit"),
    ):
        chart_rendering.render_chart(
            {"mark": "point"}, format="png", what=DIGEST_CHART, limit_seconds=1
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT context FROM stewardship_operational_log"
            " WHERE event='helper_timed_out'"
        )
        [(context,)] = cursor.fetchall()
    context = context if isinstance(context, dict) else json.loads(context)
    assert context["what"] == "digest_chart"
    assert context["helper"] == "chart_render_worker"
    assert context["limit_seconds"] == 1 and context["elapsed_seconds"] >= 1


# ------------------------------------------------- failures retry, then stop


def queued_with_comparison(harness):
    """A daily digest task with the section on, queued but not claimed."""
    row = schedule(
        str(harness.campaign.pk),
        kind="daily_digest",
        date=None,
        weekday=None,
        time="00:15:00",
        comparison=True,
    )
    assert (
        change(
            harness.service.store,
            harness.service.store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **row}],
        ).state
        == "applied"
    )
    configure_content(harness)
    return allocate(claim_task=False)


def run(status):
    """Execute the real daily handler once as the worker login."""
    with task_login(ServiceRole.WORKER, exact=True):
        execute(status, daily_handler(public_origin="https://parish.example"))
    return TaskRun.objects.get(pk=status.run_id)


@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize(
    "error",
    [
        chart_rendering.ChartRenderError("Chart rendering failed."),
        FactUnavailable("Synthetic disagreement"),
    ],
)
def test_a_failed_section_compile_retries_then_fails_at_attempt_5(
    family_mail,  # noqa: F811
    google,
    renderer,
    monkeypatch,
    error,
    final,
):
    """A compile failure or unavailable facts wait to retry; the fifth fails.

    The retry delays run on the database's real clock (minutes), so the
    fifth attempt is shown by having the failure branch read attempt 5
    (``final``), as the fifth claim would: digest_tasks._execute then ends
    the task as a permanent failure instead of scheduling another retry.
    Here the whole compile fails, even without its charts; a chart the
    renderer alone cannot draw is withheld on the last attempt instead
    (``test_a_chart_that_never_draws_is_withheld_on_the_last_attempt``).
    """
    from dataclasses import replace

    from parishkit.stewardship.reports import digest_tasks

    failed = []

    def failing(*args, **kwargs):
        """The compile's failure, as the renderer or the facts raise it."""
        failed.append(True)
        raise error

    real_status = digest_tasks._status

    def status_of(run):
        """The task's status; attempt 5 once the compile has failed (``final``)."""
        found = real_status(run)
        return replace(found, attempt=5) if final and failed else found

    monkeypatch.setattr(digest_tasks, "render_daily_digest", failing)
    monkeypatch.setattr(digest_tasks, "_status", status_of)
    with campaign_clock(INSTANT):
        import_archive(
            family_mail.service.store,
            administrator(),
            archive_bytes(CURRENT_YEAR - 1, [1, 2]),
        )
        status = queued_with_comparison(family_mail)
        task = run(status)
    assert failed
    assert task.state == ("failed" if final else "retry_wait")
    assert not DailyDigestReady.objects.exists()


@pytest.mark.parametrize("final", [False, True])
def test_a_chart_that_never_draws_is_withheld_on_the_last_attempt(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
    final,
):
    """A renderer that always fails retries, then the digest goes without charts.

    #477: a chart the server renderer cannot draw must not stop the day's
    digest. Earlier attempts wait to retry; the last (attempt 5, read as in
    the test above) compiles without any server-drawn chart, keeps the
    section's figures with no charts and no images, allocates every Admin's
    mail with no dangling ``cid:`` reference, and leaves one durable
    ERROR entry naming the chart and the attempt.
    """
    import re
    from dataclasses import replace

    from parishkit.stewardship.reports import digest_tasks

    failed = []

    def broken(spec, *, format, what, limit_seconds=60):
        """The renderer refusing every chart."""
        failed.append(what)
        raise chart_rendering.ChartRenderError("Chart rendering failed.")

    real_status = digest_tasks._status

    def status_of(run):
        """The task's status; attempt 5 once a chart has failed (``final``)."""
        found = real_status(run)
        return replace(found, attempt=5) if final and failed else found

    monkeypatch.setattr(chart_rendering, "render_chart", broken)
    monkeypatch.setattr(digest_tasks, "_status", status_of)
    with campaign_clock(INSTANT):
        import_archive(
            family_mail.service.store,
            administrator(),
            archive_bytes(CURRENT_YEAR - 1, [1, 2]),
        )
        status = queued_with_comparison(family_mail)
        task = run(status)
    assert failed == [DIGEST_CHART]
    if not final:
        assert task.state == "retry_wait"
        assert not DailyDigestReady.objects.exists()
        return
    assert task.state == "succeeded"
    ready = DailyDigestReady.objects.get()
    assert ready.comparison["charts"] == [] and ready.comparison["tiles"]
    assert ready.images() == () and ready.funnel_chart is None
    assert ready.comparison_families_chart is None
    assert re.findall(r'src="cid:([^"]+)"', ready.html) == ["participation@parishkit"]
    assert HEADING in ready.html and HEADING in ready.text
    recipient = DailyDigestRecipient.objects.select_related("outbox__render").get()
    assert "cid:comparison-" not in recipient.outbox.render.html
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT level, context FROM stewardship_operational_log"
            " WHERE event='task_failed'"
        )
        [(level, context)] = cursor.fetchall()
    context = context if isinstance(context, dict) else json.loads(context)
    assert level == "ERROR"
    assert context["failure"] == "digest_comparison_chart"
    assert (context["attempt"], context["attempt_limit"]) == (5, 5)
    assert context["task_id"] == str(status.run_id)


def test_a_comparison_that_disagrees_with_its_facts_is_retried(
    family_mail,  # noqa: F811
    google,
    renderer,
    monkeypatch,
):
    """The SQL refusal (23514) becomes unavailable facts, so the task retries.

    The fact rows are forced apart from the submissions, as only
    a superuser could, after the facts are built and before the comparison
    reads them; the definer function then refuses, as in
    test_the_cutoff_is_derived_in_sql_and_checked_against_the_fact_row.
    """
    from django.db import connections

    from parishkit.stewardship.reports import digest_tasks
    from parishkit.stewardship.reports.models import CampaignDailyFact

    real = digest_tasks.load_daily_document

    def forced_apart(claim, fact_set_id):
        """Move every day's fact row (the report day's too), as a superuser."""
        other = connections.create_connection("default")
        try:
            with other.cursor() as cursor:
                cursor.execute("SET session_replication_role = replica")
                cursor.execute(
                    "UPDATE stewardship_daily_fact SET"
                    " cumulative_responses=cumulative_responses+1,"
                    " cohort_denominator=cohort_denominator+1"
                    " WHERE fact_set_id=%s",
                    [fact_set_id],
                )
                assert cursor.rowcount >= 1
        finally:
            other.close()
        return real(claim, fact_set_id)

    monkeypatch.setattr(digest_tasks, "load_daily_document", forced_apart)
    with campaign_clock(INSTANT):
        import_archive(
            family_mail.service.store,
            administrator(),
            archive_bytes(CURRENT_YEAR - 1, [1, 2]),
        )
        status = queued_with_comparison(family_mail)
        task = run(status)
    assert task.state == "retry_wait"
    assert not DailyDigestReady.objects.exists()
    assert CampaignDailyFact.objects.exists()


# ------------------------------------------- 0072's check, on old bodies


MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)


# 0072's own comparison chart check, which 0083 (#477) relaxed so a section
# may be kept without images; 0072's DO block checks for this one.
CHECK_0072 = (
    "ALTER TABLE public.stewardship_daily_digest_ready DROP CONSTRAINT "
    "daily_digest_comparison_charts; "
    + next(
        line
        for line in (
            Path(__file__).resolve().parents[3]
            / "src/parishkit/stewardship/schema/migrations/0072_digest_comparison.sql"
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith(
            "ALTER TABLE public.stewardship_daily_digest_ready ADD CONSTRAINT"
        )
    )
)


def replaced(name, function):
    """One function's definition from a frozen file, as CREATE OR REPLACE."""
    from ..test_schema_migration_files import function_bodies

    text = (MIGRATIONS / name).read_text(encoding="utf-8")
    bodies = function_bodies(text, replace=False) | function_bodies(text, replace=True)
    return "CREATE OR REPLACE FUNCTION" + bodies[function]


@pytest.mark.parametrize(
    ("installs", "refusal"),
    [
        (
            [("0065_staff_entered.sql", "stewardship_safe_context_v1")],
            "does not admit the chart timeout",
        ),
        (
            [
                ("0067_campaign_comparison.sql", "stewardship_campaign_comparison_v1"),
                (
                    "0067_campaign_comparison.sql",
                    "stewardship_campaign_archive_renewal_v1",
                ),
            ],
            "comparison functions are not installed",
        ),
    ],
)
def test_the_0072_check_refuses_the_old_bodies(installs, refusal):
    """Each replaced body, put back as it was, fails 0072's DO block."""
    from django.db import DatabaseError, transaction

    from .test_campaign_comparison_postgresql import ACL_NOW

    text = (MIGRATIONS / "0072_digest_comparison.sql").read_text(encoding="utf-8")
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL_NOW)
        # The table as 0072 left it (rolled back below); 0083 changes it.
        cursor.execute(CHECK_0072)
        cursor.execute(check)  # The installed bodies pass.
        with (
            pytest.raises(DatabaseError, match=f"Migration 0072: .*{refusal}"),
            transaction.atomic(),
        ):
            for name, function in installs:
                cursor.execute(replaced(name, function))
            cursor.execute(check)
        cursor.execute(check)  # Rolled back: the installed bodies pass again.
        transaction.set_rollback(True)


def test_the_0072_check_refuses_a_body_without_0065s_sorts():
    """0072's body built on 0059's (a stale restack) fails its DO block.

    The chart words alone are not enough: the body must keep 0065's Entered
    by Staff sort tokens, or a restacked file would silently revert them.
    """
    from django.db import DatabaseError, transaction

    from .test_campaign_comparison_postgresql import ACL_NOW

    text = (MIGRATIONS / "0072_digest_comparison.sql").read_text(encoding="utf-8")
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    body = replaced("0072_digest_comparison.sql", "stewardship_safe_context_v1")
    tokens = ",'staff_entered','-staff_entered'"
    assert body.count(tokens) == 1
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL_NOW)
        cursor.execute(CHECK_0072)  # As 0072 left the table; rolled back below.
        with (
            pytest.raises(DatabaseError, match="Migration 0072: .*chart timeout"),
            transaction.atomic(),
        ):
            cursor.execute(body.replace(tokens, ""))
            cursor.execute(check)
        cursor.execute(check)  # Rolled back: the installed body passes again.
        transaction.set_rollback(True)
