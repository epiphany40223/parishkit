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
from .test_family_auth_postgresql import family_service, login  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def _measure(operation, *, samples=20, query_limit=64):
    """Record safe aggregate evidence and enforce the ordinary-page p95 budget."""
    timings, counts = [], []
    for _ in range(samples):
        with CaptureQueriesContext(connection) as queries:
            started = perf_counter()
            operation()
            timings.append(perf_counter() - started)
        counts.append(len(queries))
    p95 = sorted(timings)[int(len(timings) * 0.95) - 1]
    assert max(counts) <= query_limit
    assert p95 < 2.0
    return {"queries_max": max(counts), "p95_seconds": round(p95, 4)}


def test_reference_family_population_does_not_expand_interactive_queries(
    family_service,  # noqa: F811
    google,  # noqa: F811
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
    large = _measure(code_lookup)
    assert large["queries_max"] == small["queries_max"]
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

    result = {
        "lookup_1_family": small,
        "lookup_5000_families": large,
        "family_page_100_sessions": _measure(family_page),
        "admin_shell": _measure(admin_page),
    }
    print("Identity baseline: " + json.dumps(result, sort_keys=True))


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
        large = _measure(production_lookup)
        assert small["queries_max"] == large["queries_max"]
        assert FamilyCampaign.objects.count() == 5000
        for _ in range(100):
            browser, response = login(code)
            assert response.status_code == 302
        assert FamilySession.objects.filter(mode="production").count() == 100

        def production_page():
            assert browser.get("/family/").status_code == 200

        print(
            "Production identity baseline: "
            + json.dumps(
                {
                    "lookup_1_family": small,
                    "lookup_5000_families": large,
                    "family_page_100_sessions": _measure(production_page),
                    "atomic_arrival_seconds": round(promotion_seconds, 4),
                },
                sort_keys=True,
            )
        )
