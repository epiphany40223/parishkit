"""Initial identity budgets; full source/report/task scale belongs to later owners."""

import json
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.family_authentication import FamilyRuntime, lookup
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilySession,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus, code_context
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign

from .auth_builders import signed_in
from .campaign_builders import add_draft, campaign_clock, command
from .credential_builders import keys, populate
from .plan_work import analyze_all, rows_read_by
from .test_family_auth_postgresql import family_service, login  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def _measure(operation, *, samples=20, query_limit=64):
    """Record safe aggregate evidence and enforce the ordinary-page budget.

    The budget is work, not wall-clock time (#690): CI packs three PostgreSQL
    partitions onto each runner, so elapsed time is shared-CPU noise. The
    statement count is bounded here and returned with the rows one call
    reads (``rows_read``, counted by the server), so callers compare both
    across populations. The p95 bound only guards against catastrophe.
    """
    timings, counts, database_timings = [], [], []
    for _ in range(samples):
        with CaptureQueriesContext(connection) as queries:
            started = perf_counter()
            operation()
            timings.append(perf_counter() - started)
        counts.append(len(queries))
        # Never print SQL or parameters: even synthetic authentication probes
        # exercise private credential bindings. Ordinals let a failed CI sample
        # identify which query needs profiling without exposing those values.
        database_timings.append(
            [
                {"ordinal": index, "seconds": float(query["time"])}
                for index, query in enumerate(queries.captured_queries, start=1)
            ]
        )
    ranked = sorted(range(samples), key=timings.__getitem__)
    percentile = ranked[int(len(timings) * 0.95) - 1]
    p95 = timings[percentile]
    evidence = {
        "operation": operation.__name__,
        "sample_seconds": [round(value, 4) for value in timings],
        "sample_queries": counts,
        "p95_sample_queries": database_timings[percentile],
        "slowest_sample_queries": database_timings[ranked[-1]],
    }
    assert max(counts) <= query_limit, json.dumps(evidence, sort_keys=True)
    assert p95 < 10, json.dumps(evidence, sort_keys=True)
    return {
        "queries_max": max(counts),
        # Another connection's late statistics flush can only add rows to
        # one count, never remove them, so the fewest of three is this
        # operation's own.
        "rows_read": min(rows_read_by(operation) for _ in range(3)),
        "p95_seconds": round(p95, 4),
    }


def assert_population_independent(small, large):
    """An indexed lookup reads about as many rows at 5,000 Families as at one.

    A scan of the Families (or their codes) would add thousands of rows; the
    margin absorbs a few extra index entries in a deeper index.
    """
    assert large["rows_read"] < small["rows_read"] + 100, (small, large)


def test_reference_family_population_does_not_expand_interactive_queries(
    family_service,  # noqa: F811
    google,  # noqa: F811
    monkeypatch,
):
    """Rehearsal admission stays bounded with 5,000 production identity overlays.

    Concurrent-session population means 100 unexpired sessions, not a claim of
    100 simultaneous HTTP requests. The full mixed-worker/load demonstration
    remains OPS-09/ARC-08. No placeholder Members or Ministries are fabricated.
    """
    service = family_service.service

    def code_lookup():
        """Exercise current source/epoch/key admission and indexed credential lookup."""
        assert lookup(service, code=family_service.code) is not None

    small = _measure(code_lookup)
    populate(
        family_service.campaign,
        family_service.rings,
        [FamilyStatus(index, True, True, True, True) for index in range(1, 5001)],
        generation=2,
    )
    assert FamilyCampaign.objects.count() == 5000
    analyze_all()
    large = _measure(code_lookup)
    assert large["queries_max"] == small["queries_max"]
    assert_population_independent(small, large)
    for _ in range(100):
        browser, response = login(family_service.code)
        assert response.status_code == 302
    assert FamilySession.objects.filter(revoked_at__isnull=True).count() == 100

    def family_page():
        """Measure actual response rendering and current session authorization."""
        assert browser.get("/family/").status_code == 200

    admin, response = signed_in()
    assert response.status_code == 302

    def admin_page():
        """Exercise the dashboard shell through current Google/policy session state."""
        assert admin.get("/admin/").status_code == 200

    # Family and Admin pages read the Family maintenance switch, which caches
    # its state for a few seconds of wall-clock time, so a slower run whose
    # samples straddled that expiry counted one extra query in one sample
    # (49 > 48 on CI). Hold the cache for the measurement and fill it first,
    # so every sample counts the same.
    from parishkit.stewardship.accounts import family_maintenance

    monkeypatch.setattr(family_maintenance, "CACHE_SECONDS", 3600)
    family_page()
    admin_page()

    # The dashboard's security event panel is one fixed query on top of the
    # identity shell, present for every Administrator; the shell itself gives
    # that query back only once a source snapshot has been promoted.
    result = {
        "lookup_1_family": small,
        "lookup_5000_families": large,
        "family_page_100_sessions": _measure(family_page),
        # The request verifies its configuration corpus once (request_scope)
        # and the chrome reuses the view's clock. Counts include each
        # transaction's BEGIN/COMMIT; the page measured 46 (66 before), plus
        # one fixed query for the "no campaign mail can reach" line while a
        # campaign is open (47), plus one fixed query for the ParishSoft
        # Ministry changes notice once a source is promoted (48; #342; one
        # statement whether or not the campaign selects Ministries, which
        # test_ministry_catalog_postgresql pins), and
        # the ceiling keeps one statement of headroom so regressions are
        # caught. Home's System health problem lines (ADM-13) ride on the
        # refresh-status statement and add none, whichever problems are
        # open (test_system_health_postgresql pins that with a backup
        # overdue). An Administrator's chrome also reads whether an
        # integration key change holds the settings queue, for its banner
        # (one small indexed query, #456), and the menu's open counts on
        # Additional information and Ministry follow-up are one statement
        # (#585): the page measures 51.
        "admin_shell": _measure(admin_page, query_limit=51),
    }
    print("Identity baseline: " + json.dumps(result, sort_keys=True))
    # A Family page reads its own session and Family, never the population.
    assert result["family_page_100_sessions"]["rows_read"] < 1000, result


def test_production_lookup_and_sessions_at_reference_population(auth_service, settings):
    """Measure the actual 5,000-row Production MAC index, not a rehearsal index."""
    actor = uuid4()
    _, row, _ = add_draft(auth_service.store, auth_service.store.active(), actor)
    campaign = Campaign.objects.get(pk=UUID(row["id"]))
    ring = keys()
    populate(campaign, ring)
    service = FamilyRuntime(
        auth_service.store, auth_service.limiter, ring.general, ring.mac, ring.public
    )
    settings.STEWARDSHIP_FAMILY_RUNTIME = service
    family = FamilyCampaign.objects.get()
    code = ring.general.decrypt(
        family.code_ciphertext, context=code_context(family.pk)
    ).decode()
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)

        def production_lookup():
            """Pin the returned mode so the benchmark cannot silently test rehearsal."""
            result = lookup(service, code=code)
            assert result is not None and result[2] == "production"

        small = _measure(production_lookup)
        started = perf_counter()
        populate(
            campaign,
            ring,
            [FamilyStatus(index, True, True, True, True) for index in range(1, 5001)],
            generation=2,
        )
        promotion_seconds = perf_counter() - started
        analyze_all()
        large = _measure(production_lookup)
        assert small["queries_max"] == large["queries_max"]
        assert_population_independent(small, large)
        assert FamilyCampaign.objects.count() == 5000
        for _ in range(100):
            browser, response = login(code)
            assert response.status_code == 302
        assert FamilySession.objects.filter(mode="production").count() == 100

        def production_page():
            """A Family page under one of 100 Production sessions."""
            assert browser.get("/family/").status_code == 200

        page = _measure(production_page)
        print(
            "Production identity baseline: "
            + json.dumps(
                {
                    "lookup_1_family": small,
                    "lookup_5000_families": large,
                    "family_page_100_sessions": page,
                    "atomic_arrival_seconds": round(promotion_seconds, 4),
                },
                sort_keys=True,
            )
        )
        assert page["rows_read"] < 1000, page
