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
from uuid import uuid4

import pytest
from django.db import connection
from django.urls import reverse
from PIL import Image

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.archive import imports
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
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
