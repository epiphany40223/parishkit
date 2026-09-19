"""Real-role source/campaign directory, exact-code lookup and postal complement."""

import json
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.http import QueryDict

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.directories import DirectoryQuery, directory_page
from parishkit.stewardship.reports.statistics import calculate_statistics
from parishkit.stewardship.reports.statistics_selection import capture_statistics

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_information_followup_postgresql import search
from .test_recipient_suppressions_postgresql import refused, remember
from .test_report_workspace_postgresql import read
from .test_source_families_postgresql import prepare, promote
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def page(harness, *, postal=False, **filters):
    """The actual WEB role reads the query inside the same transaction contract."""
    parameters = QueryDict(mutable=True)
    parameters.update(filters)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return directory_page(
            harness.campaign.pk,
            DirectoryQuery.parse(parameters),
            postal=postal,
            general=harness.rings.general,
            mac=harness.rings.mac,
        )


def test_native_directory_code_filters_contacts_and_response(
    live_response_service, google
):
    """Full source names/contact details, exact code and response are not guesses."""
    harness = live_response_service
    result = page(harness)
    assert result["total"] == result["active_total"] == 1
    item = result["rows"][0]
    assert item["family_name"] == "Household Example"
    assert item["code"] == harness.code
    assert item["address"]["primaryAddress1"] == "1 Example Street"
    assert item["phones"][0]["value"] == "202-555-0123"
    assert (
        item["email_deliverable"] and item["email_eligible"] and not item["responded"]
    )
    assert page(harness, postal=True)["total"] == 0
    for filters in (
        {"search": "EXAMP"},
        {"search": "1"},
        {"search": "example street"},
        {"phone": "yes"},
        {"exact_code": harness.code.lower()},
    ):
        assert page(harness, **filters)["total"] == 1
    for filters in (
        {"exact_code": "ZZZZZZZZ"},
        {"response": "yes"},
        {"phone": "no"},
        {"search": "Empty"},
        {"page": "2"},
    ):
        assert page(harness, **filters)["rows"] == []
    respond(harness, "Do not show this private text in the directory")
    assert page(harness, response="yes")["total"] == 1
    browser, _ = signed_in()
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, route)
        assert response.status_code == 200 and harness.code.encode() in body
        assert b"Household Example" in body and b"Do not show this private" not in body
        assert response["Cache-Control"] == "no-store"
        assert f'href="{route}"'.encode() in body
        assert f'href="/admin/reports/{harness.campaign.pk}/postal/"'.encode() in body
        assert browser.post(route, {"exact_code": harness.code}).status_code == 403
        response, body = search(browser, route, {"exact_code": harness.code.lower()})
        assert response.status_code == 200 and harness.code.encode() in body
        response, body = read(browser, route + "?exact_code=" + harness.code)
        assert response.status_code == 400 and harness.code.encode() not in body
        statistics = calculate_statistics(capture_statistics(harness.campaign.pk))
    assert result["active_total"] == statistics.active.families
    assert result["postal_total"] == statistics.active.no_deliverable_email
    contexts = list(
        AuditContext.objects.filter(
            event__event_type="family_directory_viewed"
        ).values_list("context", flat=True)
    )
    assert contexts and harness.code not in json.dumps(contexts)
    assert "Example" not in json.dumps(contexts)
    assert any(context["exact_code_used"] for context in contexts)
    assert all(context["directory_sort"] == "name" for context in contexts)
    assert any(
        context["matching_count"] == context["count"] == 1 for context in contexts
    )
    with connection.cursor() as cursor:
        for key in (
            "directory_reason",
            "directory_phone",
            "directory_response",
            "directory_sort",
            "search_used",
            "exact_code_used",
        ):
            cursor.execute(
                "SELECT stewardship_safe_context_v1('action',%s::jsonb)",
                [json.dumps({key: "private@example.org"})],
            )
            assert cursor.fetchone()[0] is False


def test_postal_reasons_and_exact_statistics_complement(live_response_service):
    """Changed source and actual refusal evidence share the card's population."""
    harness = live_response_service
    for reason in ("no_head", "no_address", "invalid_address", "deliverable"):
        data = response_source()
        if reason == "no_head":
            data.members[3]["memberType"] = "Other"
        elif reason == "no_address":
            data.members[3]["emailAddress"] = ""
        elif reason == "invalid_address":
            data.members[3]["emailAddress"] = "not-an-address"
        snapshot, claim = prepare(data)
        promote(snapshot, claim, harness.campaign, harness.rings)
        report = page(harness)
        assert report["rows"][0]["reason"] == reason
        assert report["rows"][0]["code"] == harness.code
        postal = page(harness, postal=True)
        stats = calculate_statistics(capture_statistics(harness.campaign.pk))
        assert postal["total"] == stats.active.no_deliverable_email
        assert report["active_total"] == stats.active.families == 1
        assert page(harness, reason=reason)["total"] == 1
    # No synthetic denial flags: write immutable provider refusal evidence using
    # the existing TaskRun/outbox test owner, then query without a source refresh.
    remember(refused(harness))
    postal = page(harness, postal=True)
    stats = calculate_statistics(capture_statistics(harness.campaign.pk))
    assert postal["total"] == stats.active.no_deliverable_email == 1
    assert postal["rows"][0]["reason"] == "provider_refused"
    assert postal["rows"][0]["email_eligible"]
    assert not postal["rows"][0]["email_deliverable"]


def test_directory_pages_are_bounded_and_exclude_nonparishioners(response_service):
    """Real promoted population yields stable pages rather than browser filtering."""
    harness = response_service
    data = response_source()
    for duid in range(10, 61):
        data.families[duid] = data.families[1] | {
            "familyDUID": duid,
            "familyID": duid + 100,
            "firstName": "Repeated",
            "lastName": "Family",
        }
        data.members[1000 + duid] = data.members[3] | {
            "memberDUID": 1000 + duid,
            "familyDUID": duid,
            "memberType": "Other",
            "emailAddress": "",
        }
    data.families[90] = data.families[1] | {
        "familyDUID": 90,
        "familyID": 190,
        "registeredOrganizationID": 999,
    }
    data.members[1090] = data.members[3] | {"memberDUID": 1090, "familyDUID": 90}
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    first, second = page(harness, sort="duid"), page(harness, sort="duid", page="2")
    assert first["total"] == second["total"] == 52
    assert len(first["rows"]) == 50 and len(second["rows"]) == 2
    assert [row["family_duid"] for row in first["rows"] + second["rows"]] == [
        1,
        *range(10, 61),
    ]
    postal = page(harness, postal=True)
    assert postal["total"] == 51 and len(postal["rows"]) == 50
    assert all(row["reason"] == "no_head" and row["code"] for row in postal["rows"])
    assert page(harness, search="Repeated")["total"] == 51
    # This tests our bounded contact projection, not PostgreSQL's aggregate
    # correctness: inspect actual work for this same 52-Family source/50-row page.
    from parishkit.stewardship.reports.directory_query import DIRECTORY

    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "EXPLAIN (ANALYZE, VERBOSE, FORMAT JSON) " + DIRECTORY,
            {
                "campaign": harness.campaign.pk,
                "filters": json.dumps(DirectoryQuery().form_values()),
                "exact": False,
                "candidates": "{}",
                "postal": False,
                "size": 50,
                "offset": 0,
            },
        )
        plan = cursor.fetchone()[0][0]["Plan"]
    nodes, detail_loops = [plan], []
    while nodes:
        node = nodes.pop()
        nodes.extend(node.get("Plans", []))
        if node["Node Type"] == "Aggregate" and any(
            "jsonb_build_object('owner'" in output
            or "jsonb_build_object('duid'" in output
            for output in node.get("Output", [])
        ):
            detail_loops.append(node["Actual Loops"])
    assert len(detail_loops) == 2 and all(loops == 50 for loops in detail_loops)


def test_staff_directories_survive_limiter_outage_but_not_revocation(
    live_response_service,
    google,
    monkeypatch,
    request,
):
    """The shared read gate and live role checks remain mandatory with Valkey down."""
    harness = live_response_service
    store = harness.service.store
    rule = address("reader@example.org", roles=("staff", "ministry_leader"))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "reader@example.org"
    browser, signed = signed_in()
    assert signed.status_code == 302

    def unavailable(*args, **kwargs):
        """A directory must never ask the public guessing limiter for admission."""
        raise AssertionError("Directory unexpectedly used the public limiter")

    limiter = harness.service.limiter
    for name in ("window_script", "bucket_script", "aggregate_script"):
        monkeypatch.setattr(limiter, name, unavailable)
    monkeypatch.setattr(limiter.client, "execute_command", unavailable)
    # Restore the outage before the provider fixture scans its own disposable
    # namespace during teardown; report assertions still run fully offline.
    request.addfinalizer(monkeypatch.undo)
    routes = [
        f"/admin/reports/{harness.campaign.pk}/{kind}/"
        for kind in ("families", "postal")
    ]
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for route in routes:
            assert read(browser, route)[0].status_code == 200
        response, body = search(browser, routes[0], {"exact_code": harness.code})
        assert response.status_code == 200 and harness.code.encode() in body
    # Future purge owner's sentinel only, in a disposable test DB. Preparation
    # must preserve these read-only views without granting campaign mutation.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign=harness.campaign,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for route in routes:
            assert read(browser, route)[0].status_code == 200
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "login_rules",
                "id": rule["id"],
                "values": {
                    "roles": ["ministry_leader"],
                    "grants": {
                        "ministry_leader": rule["values"]["grants"]["ministry_leader"]
                    },
                },
            }
        ],
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for route in routes:
            response, body = read(browser, route)
            assert response.status_code == 403 and harness.code.encode() not in body
            assert (
                search(browser, route, {"exact_code": harness.code})[0].status_code
                == 403
            )


def test_archived_directory_keeps_its_retained_source(response_service, google):
    """A successor's global import cannot rewrite an archived Family directory."""
    from parishkit.stewardship.campaigns.lifecycle import Action
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.source.leases import release_source
    from parishkit.stewardship.source.snapshots import promote_snapshot

    from .campaign_builders import campaign_clock, close_campaign, command
    from .response_builders import activate_response_service
    from .test_source_snapshots_postgresql import permit
    from .test_taskrun_postgresql import act

    harness = activate_response_service(response_service)
    retained = page(harness)
    actor = uuid4()
    close_campaign(harness.campaign, actor)
    for row in TaskRun.objects.filter(state="running"):
        act(_status(row), "permanent_failure")
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        command(harness.campaign, actor, Action.ARCHIVE)
        data = response_source()
        data.families[1]["lastName"] = "Successor"
        snapshot, claim = prepare(data)
        try:
            with work_transaction():
                promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=permit)
        finally:
            release_source(claim)
        assert page(harness) == retained
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = read(
                browser, f"/admin/reports/{harness.campaign.pk}/families/"
            )
        assert response.status_code == 200
        assert b"Household Example" in body and b"Successor" not in body


def test_directory_unavailability_and_invalid_filters_are_private(
    live_response_service, google, monkeypatch
):
    """The new route uses shared read admission and never reflects private errors."""
    from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
    from parishkit.stewardship.reports import directory_views

    harness = live_response_service
    browser, _ = signed_in()
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, route, {"exact_code": "private!invalid"})
        assert response.status_code == 400 and b"private!invalid" not in body
        assert response["Cache-Control"] == "no-store"

        def unavailable(*args, **kwargs):
            """Inject a source/read-gate failure at the actual view boundary."""
            raise ReadUnavailable("private source failure details")

        monkeypatch.setattr(directory_views, "directory_page", unavailable)
        response, body = read(browser, route)
        assert response.status_code == 503
        assert b"private source" not in body and harness.code.encode() not in body
        assert response["Cache-Control"] == "no-store"
        assert response["Retry-After"] == "5"
