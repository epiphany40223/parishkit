"""Real-role source/campaign directory, exact-code lookup and postal complement."""

import json
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.http import QueryDict
from django.test import Client

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
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
    assert item["family_name"] == "Example"
    assert item["code"] == harness.code
    assert item["address"]["primaryAddress1"] == "1 Example Street"
    assert item["phones"][0]["value"] == "202-555-0123"
    assert (
        item["email_deliverable"] and item["email_eligible"] and not item["responded"]
    )
    # Mailing columns ('postal') no longer narrow the rows (#202): the one
    # Family, reachable by email, is listed with its mailing details.
    mailing = page(harness, postal=True)
    assert mailing["total"] == 1 and mailing["rows"][0]["email_deliverable"]
    assert mailing["rows"][0]["mailable"] and mailing["postal_total"] == 0
    assert page(harness, postal=True, reach="email")["total"] == 1
    assert page(harness, postal=True, reach="mail")["total"] == 0
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
        assert (
            b">Example, Member</a></td>" in body
            and b"Do not show this private" not in body
        )
        # The name opens the Family's timeline by its campaign record id.
        assert re.search(
            rf'<td><a href="{route}[0-9a-f-]{{36}}/">Example, Member</a></td>'.encode(),
            body,
        )
        # Simplified columns: DUID on its own, Yes/No values, no retired rows.
        for text in (
            b"ParishSoft DUID",
            b"Campaign email deliverable",
            b"<td>1</td>",
            b"<td>Yes</td>",
        ):
            assert text in body
        for text in (
            b"Separate home and mailing addresses",
            b"Eligible email:",
            b"<td>Not yet responded</td>",
            b"Can&#x27;t be reached by email or mail",
        ):
            assert text not in body
        assert b'class="family-code"' in body
        assert response["Cache-Control"] == "no-store"
        assert f'href="{route}"'.encode() in body
        # One page covers both uses: mailing columns are a checkbox, off by
        # default, and the page no longer links a separate postal page.
        assert b'name="mailing" value="yes"' in body
        assert b'name="mailing" value="yes" checked' not in body
        assert b"/postal/" not in body and b"Mailing address" not in body
        assert browser.post(route, {"exact_code": harness.code}).status_code == 403
        response, body = search(browser, route, {"exact_code": harness.code.lower()})
        assert response.status_code == 200 and harness.code.encode() in body
        response, body = read(browser, route + "?exact_code=" + harness.code)
        assert response.status_code == 400 and harness.code.encode() not in body
        # Shared table (#203): sortable headings and the navigator are POST
        # forms carrying the private filters, and only the installed
        # selection's sort tokens and page size are accepted.
        response, body = search(
            browser, route, {"exact_code": harness.code, "sort": "duid", "size": "50"}
        )
        assert response.status_code == 200 and b"Page 1 of 1" in body
        assert b'aria-sort="ascending"' in body and b'value="name"' in body
        assert f'value="{harness.code}"'.encode() in body
        assert b'href="?' not in body and b"exact_code=" not in body
        for invalid in ({"sort": "family_duid"}, {"size": "25"}, {"size": "all"}):
            assert search(browser, route, invalid)[0].status_code == 400
        # The mailing columns and reach choice (#363) ride along on every
        # heading and navigator form (two of each) plus the export form.
        response, body = search(browser, route, {"mailing": "yes", "reach": "mail"})
        assert response.status_code == 200
        for field in (b'name="mailing" value="yes"', b'name="reach" value="mail"'):
            assert body.count(b'<input type="hidden" ' + field) >= 5
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
    assert {context["directory_sort"] for context in contexts} == {"name", "duid"}
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


def test_mailing_columns_merge_postal_outreach_into_the_directory(
    live_response_service, google
):
    """One page serves both uses; old postal links redirect to its preset (#202)."""
    harness = live_response_service
    browser, _ = signed_in()
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    legacy = f"/admin/reports/{harness.campaign.pk}/postal/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # A bookmarked postal page opens the merged page with mailing columns
        # and the Families that need postal mail, keeping only a known reach.
        response = browser.get(legacy)
        assert response.status_code == 302
        assert response["Location"] == route + "?reach=mail&mailing=yes"
        response = browser.get(legacy + "?reach=neither&search=private")
        assert response["Location"] == route + "?reach=neither&mailing=yes"
        response = browser.get(legacy + "?reach=private-text")
        assert response["Location"] == route + "?reach=mail&mailing=yes"
        # Mailing columns are independent of the filters: the one Family,
        # reachable by email, shows its addressee and mailing address.
        for response, body in (
            read(browser, route + "?mailing=yes"),
            search(browser, route, {"mailing": "yes", "reach": "email"}),
            search(browser, route, {"mailing": "yes", "reason": "deliverable"}),
        ):
            assert response.status_code == 200 and harness.code.encode() in body
            assert b"<td>Member Example</td>" in body
            assert b"<td>1 Example Street<br>" in body
            assert b'<option value="email"' in body
            assert b'<option value="deliverable"' in body
            assert b'<input type="hidden" name="mailing" value="yes">' in body
        response, body = search(browser, route, {"mailing": "yes", "reach": "mail"})
        assert response.status_code == 200 and b"No matching Families." in body
        # Without mailing columns the same filter finds the Family, without
        # the mailing columns.
        response, body = search(browser, route, {"reach": "email"})
        assert response.status_code == 200 and harness.code.encode() in body
        assert b"Addressee" not in body and b"<td>1 Example Street<br>" not in body
        # A form rendered before the merge still posts to the old route and
        # keeps its mailing columns.
        response, body = search(browser, legacy, {"reach": "any"})
        assert response.status_code == 200 and b"Addressee" in body
        for values in ({"mailing": "maybe"}, {"mailing": ["yes", "no"]}):
            response, body = search(browser, route, values)
            assert response.status_code == 400
        assert read(browser, route + "?mailing=yes&search=x")[0].status_code == 400
    events = list(
        AuditContext.objects.filter(
            event__event_type="postal_outreach_viewed"
        ).values_list("context", flat=True)
    )
    # Mailing-column views keep the postal-outreach audit event.
    assert len(events) >= 3 and all("mailing" not in item for item in events)


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
        assert postal["postal_total"] == stats.active.no_deliverable_email
        assert postal["total"] == report["total"]
        assert report["active_total"] == stats.active.families == 1
        assert page(harness, reason=reason)["total"] == 1
    # No synthetic denial flags: write immutable provider refusal evidence using
    # the existing TaskRun/outbox test owner, then query without a source refresh.
    remember(refused(harness))
    postal = page(harness, postal=True)
    stats = calculate_statistics(capture_statistics(harness.campaign.pk))
    assert postal["postal_total"] == stats.active.no_deliverable_email == 1
    assert postal["total"] == 1
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
    assert page(harness, postal=True)["total"] == 52
    postal = page(harness, postal=True, reach="mail")
    assert postal["total"] == 51 and len(postal["rows"]) == 50
    assert all(row["reason"] == "no_head" and row["code"] for row in postal["rows"])
    assert page(harness, search="Repeated")["total"] == 51
    # This tests our bounded contact projection, not PostgreSQL's aggregate
    # correctness: inspect actual work for this same 52-Family source/50-row page.
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        # Explain the installed selection body itself: PL/pgSQL's outer call
        # hides its inner plan. Do not maintain a second copy of the query.
        cursor.execute(
            "SELECT prosrc FROM pg_proc WHERE "
            "oid='stewardship_directory_report_v1(uuid,jsonb,integer)'::regprocedure"
        )
        statement = (
            cursor.fetchone()[0]
            .split("-- BEGIN DIRECTORY SELECTION")[1]
            .split("-- END DIRECTORY SELECTION")[0]
        )
        statement = (
            statement.replace("INTO answer", "")
            .replace("campaign_uuid", "%(campaign)s")
            .replace("SELECT f AS f", "SELECT %(filters)s::jsonb AS f")
            .replace("page_number", "%(page)s")
        )
        for expression, parameter in (
            ("(parameters->>'postal')::boolean", "%(postal)s"),
            ("(parameters->>'exact')::boolean", "%(exact)s"),
            ("(parameters->>'family_id')::uuid", "%(family)s::uuid"),
        ):
            statement = statement.replace(expression, parameter)
        cursor.execute(
            "EXPLAIN (ANALYZE, VERBOSE, FORMAT JSON) " + statement,
            {
                "campaign": harness.campaign.pk,
                "filters": json.dumps(DirectoryQuery().form_values()),
                "exact": False,
                "family": None,
                "postal": False,
                "page": 1,
            },
        )
        plan = cursor.fetchone()[0][0]["Plan"]
    nodes, phone_loops, head_groups = [plan], [], []
    while nodes:
        node = nodes.pop()
        nodes.extend(node.get("Plans", []))
        outputs = node.get("Output", [])
        if node["Node Type"] != "Aggregate":
            continue
        if any("jsonb_build_object('owner'" in output for output in outputs):
            phone_loops.append(node["Actual Loops"])
        elif any("jsonb_build_object('duid'" in output for output in outputs):
            head_groups.append(node)
    # Phones are gathered per shown row; heads in one grouped pass over the
    # page's Families only (page_heads), never the whole directory.
    assert phone_loops == [50]
    assert len(head_groups) == 1 and head_groups[0]["Actual Loops"] == 1
    assert head_groups[0]["Actual Rows"] <= 50


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
    # The directory without and with its mailing columns (once postal outreach).
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    routes = [route, route + "?mailing=yes"]
    find = f"/admin/reports/{harness.campaign.pk}/families/find"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for route in routes:
            assert read(browser, route)[0].status_code == 200
        response, body = search(browser, routes[0], {"exact_code": harness.code})
        assert response.status_code == 200 and harness.code.encode() in body
        # Staff get the header's Find a Family box and its results (#561).
        assert f'action="{find}"'.encode() in read(browser, "/admin/")[1]
        response, body = search(browser, find, {"search": "examp"})
        assert response.status_code == 200 and b"1 Family found" in body
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
        # A Ministry leader gets no box, and the route refuses them (#561).
        assert b"data-find-family" not in read(browser, "/admin/")[1]
        response, body = search(browser, find, {"search": "examp"})
        assert response.status_code == 403 and body == b""
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
        assert b">Example, Member</a></td>" in body and b"Successor" not in body


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
        recovery = f"/admin/campaign/{harness.campaign.pk}/family-codes"
        assert f'href="{recovery}"'.encode() in body
        recovered, codes = read(browser, recovery)
        assert recovered.status_code == 200 and harness.code.encode() in codes
        assert recovered["Cache-Control"] == "no-store"


def test_reach_filter_finds_families_no_campaign_mail_can_reach(
    live_response_service,
):
    """Email, postal-only and neither follow deliverability and the address."""
    harness = live_response_service
    report = page(harness)
    assert report["unreachable_total"] == 0 and report["rows"][0]["mailable"]
    assert page(harness, reach="email")["total"] == 1
    assert (
        page(harness, reach="mail")["total"]
        == page(harness, reach="neither")["total"]
        == 0
    )
    # No deliverable email: reachable only by postal mail.
    data = response_source()
    data.members[3]["emailAddress"] = ""
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    assert page(harness, reach="mail")["total"] == 1
    assert page(harness)["unreachable_total"] == 0
    # And no usable mailing address either: a street line alone is not enough.
    data.families[1].update(primaryCity="", primaryPostalCode="", primaryState="")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    report = page(harness, reach="neither")
    assert report["total"] == report["unreachable_total"] == 1
    assert not report["rows"][0]["mailable"]
    assert page(harness, reach="mail")["total"] == 0
    with pytest.raises(ValueError):
        page(harness, reach="sometimes")


def test_reach_preset_link_and_dashboard_readiness(live_response_service, google):
    """?reach=neither is the only accepted GET filter; the dashboard counts it."""
    harness = live_response_service
    data = response_source()
    data.members[3]["emailAddress"] = ""
    data.families[1].update(primaryAddress1="")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    browser, _ = signed_in()
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, route + "?reach=neither")
        assert response.status_code == 200 and b">Example, Member</a></td>" in body
        assert b'value="neither" selected' in body
        assert b"no campaign mail can reach" not in body
        # Only Testing mode explains that live codes wait for go-live.
        mode = SystemConfiguration.objects.values_list("mode", flat=True).get()
        assert (b"work only after go-live" in body) is (mode == "testing")
        # The "Open form" links appear only once those codes actually work.
        assert (b"data-open-form " in body) is (mode != "testing")
        assert read(browser, route + "?reach=sometimes")[0].status_code == 400
        assert read(browser, route + "?reach=neither&search=x")[0].status_code == 400
        home = browser.get("/admin/").content
    assert (
        b"Reaching every Family" in home and (route + "?reach=neither").encode() in home
    )


def test_family_name_is_the_surname_not_a_first_name_mailing_name(
    live_response_service,
):
    """A mailing name holding one person's given name never names the Family."""
    harness = live_response_service
    data = response_source()
    data.families[1]["mailingName"] = "Anna"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    report = page(harness)
    assert [row["family_name"] for row in report["rows"]] == ["Example"]
    assert page(harness, search="Anna")["total"] == 0


def test_directory_names_lead_with_the_surname_then_the_heads(live_response_service):
    """ "Surname, First and Second" names, ordered and searched by the whole string.

    The SQL selection builds the same string as family_heads_name so search and
    the same-surname order agree with what the page and the exports show.
    """
    harness = live_response_service
    data = response_source()
    # Family 1 ("Example") gains a head of another surname; three more Example
    # Families have one head, two heads and none (a child only).
    data.members[4] = data.members[3] | {
        "memberDUID": 4,
        "firstName": "Second",
        "lastName": "Other",
        "emailAddress": "",
    }
    for duid, heads in ((7, ("Zed",)), (8, ("Amy", "Bob")), (9, ())):
        data.families[duid] = data.families[1] | {
            "familyDUID": duid,
            "familyID": duid + 100,
        }
        for index, first in enumerate(heads or ("Child",)):
            data.members[100 * duid + index] = data.members[3] | {
                "memberDUID": 100 * duid + index,
                "familyDUID": duid,
                "firstName": first,
                "memberType": "Head" if heads else "Other",
                "emailAddress": "",
            }
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    report = page(harness)
    assert [row["display_name"] for row in report["rows"]] == [
        "Example",
        "Example, Amy and Bob",
        "Example, Member and Second Other",
        "Example, Zed",
    ]
    assert [row["family_duid"] for row in report["rows"]] == [9, 8, 1, 7]
    assert [row["family_duid"] for row in page(harness, sort="name_desc")["rows"]] == [
        7,
        1,
        8,
        9,
    ]
    # Search matches the whole shown name, so a head's first name finds the Family.
    for text, expected in (
        ("zed", [7]),
        ("Example, Member and Second Other", [1]),
        ("amy and bob", [8]),
        ("Other", [1]),
    ):
        assert [
            row["family_duid"] for row in page(harness, search=text)["rows"]
        ] == expected


def test_contact_details_show_head_emails_in_one_batched_read(
    live_response_service, google
):
    """Heads' emails come from the page's source in one read, heads only (#604).

    Heads sharing an address are listed together once; a head without an
    email reads "No email on file"; a non-head Member's and the Family
    record's own email are never shown. The page's query count does not grow
    with its rows: one contact read covers every shown head.
    """
    from django.test.utils import CaptureQueriesContext

    harness = live_response_service
    data = response_source()
    # Family 1 ("Example"): heads 3 and 6 share valid@example.org (6 in
    # upper case; the source lowercases valid addresses); head 4 has none;
    # Member 5 is not a head and has an email that must never appear.
    data.members[4] = data.members[3] | {
        "memberDUID": 4,
        "firstName": "Second",
        "emailAddress": "",
    }
    data.members[6] = data.members[3] | {
        "memberDUID": 6,
        "firstName": "Third",
        "emailAddress": "VALID@Example.org",
    }
    data.members[5] = data.members[3] | {
        "memberDUID": 5,
        "firstName": "Child",
        "memberType": "Other",
        "emailAddress": "child@example.org",
    }
    # Two more Families: one head with a valid and an invalid entry, and
    # none at all (a child only), so a page has several rows with heads.
    for duid, member, kind, email in (
        (7, "Zed", "Head", "zed@example.org; not-an-address"),
        (9, "Kid", "Other", "kid@example.org"),
    ):
        data.families[duid] = data.families[1] | {
            "familyDUID": duid,
            "familyID": duid + 100,
        }
        data.members[100 * duid] = data.members[3] | {
            "memberDUID": 100 * duid,
            "familyDUID": duid,
            "firstName": member,
            "memberType": kind,
            "emailAddress": email,
        }
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)

    def counted(**filters):
        """One directory page and the SQL statements it ran."""
        with CaptureQueriesContext(connection) as queries:
            report = page(harness, **filters)
        return report, [query["sql"] for query in queries.captured_queries]

    def contact_reads(statements):
        """The Django contact reads (the role setup's GRANT names every table)."""
        return [sql for sql in statements if "JOIN stewardship_snapshot_contact" in sql]

    report, statements = counted()
    heads = {
        row["family_duid"]: [(head["name"], head["emails"]) for head in row["heads"]]
        for row in report["rows"]
    }
    assert heads == {
        1: [
            ("Member Example", [{"value": "valid@example.org", "valid": True}]),
            ("Second Example", []),
            ("Third Example", [{"value": "valid@example.org", "valid": True}]),
        ],
        7: [
            (
                "Zed Example",
                [
                    {"value": "not-an-address", "valid": False},
                    {"value": "zed@example.org", "valid": True},
                ],
            )
        ],
        9: [],
    }
    (example,) = (row for row in report["rows"] if row["family_duid"] == 1)
    assert [(group["names"], group["value"]) for group in example["head_emails"]] == [
        ("Member Example and Third Example", "valid@example.org"),
        ("Second Example", None),
    ]
    assert len(contact_reads(statements)) == 1
    # A one-row page runs exactly as many statements as the three-row page.
    single, fewer = counted(search="zed")
    assert [row["family_duid"] for row in single["rows"]] == [7]
    assert len(fewer) == len(statements)
    # A page whose one Family has no head skips the contact read altogether.
    childless, headless = counted(search="9")
    assert [row["family_duid"] for row in childless["rows"]] == [9]
    assert contact_reads(headless) == []
    assert len(headless) == len(statements) - 1

    browser, _ = signed_in()
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, route)
    assert response.status_code == 200
    for text in (
        b"Member Example and Third Example \xe2\x80\x94 "
        b'<a href="mailto:valid@example.org">valid@example.org</a>',
        b"Second Example \xe2\x80\x94 No email on file",
        b"not-an-address <small>(not a valid address; fix in ParishSoft)</small>",
    ):
        assert text in body
    for private in (b"child@example.org", b"kid@example.org", b"family@example.org"):
        assert private not in body
    # The Family name's timeline link (#590) and the head emails share one row
    # without mixing: the link is the Family's record id, the emails stay in
    # the Contact details pane, and every href other than mailto: is email-free.
    (row,) = (
        item for item in body.split(b"<tr>") if b"mailto:valid@example.org" in item
    )
    name_cell, pane = row.split(b'<td class="contact-details">')
    assert re.search(
        rf'<td><a href="{route}[0-9a-f-]{{36}}/">Example, Member, Second '
        rf"and Third</a></td>".encode(),
        name_cell,
    )
    assert b"@" not in name_cell and b"mailto:valid@example.org" in pane
    for href in re.findall(rb'href="([^"]*)"', body):
        assert href.startswith(b"mailto:") or b"@" not in href
    contexts = list(
        AuditContext.objects.filter(
            event__event_type="family_directory_viewed"
        ).values_list("context", flat=True)
    )
    assert contexts and "example.org" not in json.dumps(contexts)


def test_head_emails_never_read_a_compacted_snapshot(monkeypatch):
    """A compacted source is never read as "No email on file" (#604).

    An unchanged refresh compacts the previous snapshot at once. The page
    refuses it; an export falls back to the current promoted source and is
    told so; with no promoted source at all both fail with their own kind.
    """
    from parishkit.stewardship.reports.directories import (
        HeadEmailsUnavailable,
        _head_contacts,
    )
    from parishkit.stewardship.source.leases import _now
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    from .test_source_compaction_postgresql import cleanup, promote_history

    keys = {"member:1", "member:2"}
    # Before any source is promoted even an export has nothing to read.
    SourceCurrent.objects.get_or_create(singleton=True)
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        transaction.atomic(),
        pytest.raises(HeadEmailsUnavailable, match="no current ParishSoft"),
    ):
        _head_contacts(uuid4(), keys, current=True)
    with transaction.atomic():
        now = _now()
    records = promote_history(
        monkeypatch, [(now - timedelta(minutes=15), "same"), (now, "same")]
    )
    while cleanup().snapshot_count:
        pass
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        contacts, chosen = _head_contacts(records[1].pk, keys)
        assert isinstance(contacts, dict) and chosen[0] == records[1].pk
        for missing in (records[0].pk, uuid4()):
            with pytest.raises(HeadEmailsUnavailable, match="just replaced"):
                _head_contacts(missing, keys)
        # No heads, no read: nothing can be shown wrongly.
        assert _head_contacts(records[0].pk, set()) == ({}, None)
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        transaction.atomic(),
    ):
        # An export of the compacted source reads the current one instead,
        # and learns which (its promotion time dates the file's emails).
        _, chosen = _head_contacts(records[0].pk, keys, current=True)
        assert chosen[:2] == (records[1].pk, records[1].promoted_at)
        # Its own source, while still there, is preferred.
        _, chosen = _head_contacts(records[1].pk, keys, current=True)
        assert chosen[0] == records[1].pk


def test_find_a_family_runs_the_directory_search_privately(
    live_response_service, google
):
    """The header box's route (#561): directory matches, POST only, no codes.

    Results come from the directory's own selection, open the Family
    timeline, carry no Family code, and are never cached. The route accepts
    only a CSRF POST of the search text, at least 2 characters, and the
    audit records that a search was used, never its text.
    """
    harness = live_response_service
    browser, _ = signed_in()
    find = f"/admin/reports/{harness.campaign.pk}/families/find"
    timeline = f"/admin/reports/{harness.campaign.pk}/families/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = read(browser, "/admin/")[1]
        assert f'<form method="post" action="{find}" role="search"'.encode() in home
        for text in ("examp", " MEMBER ", "example street"):
            response, body = search(browser, find, {"search": text})
            assert response.status_code == 200, text
            assert response["Cache-Control"] == "no-store"
            assert b"1 Family found" in body, text
            assert re.search(
                rf'<a href="{timeline}[0-9a-f-]{{36}}/" data-find-family-result>'
                rf'<span class="find-family-name">Example, Member</span>'.encode(),
                body,
            )
            assert harness.code.encode() not in body and b"See all" not in body
        response, body = search(browser, find, {"search": "nobody here"})
        assert response.status_code == 200 and b"No Family matches." in body
        # Too short, another filter, a query string, or no CSRF token.
        for values in (
            {"search": "e"},
            {"search": " e "},
            {"search": "ex", "sort": "duid"},
        ):
            response, body = search(browser, find, values)
            assert response.status_code == 400 and body == b"", values
        assert (
            search(browser, find + "?search=ex", {"search": "ex"})[0].status_code == 400
        )
        assert read(browser, find)[0].status_code == 405
        assert browser.post(find, {"search": "examp"}).status_code == 403
        # Another campaign's address (not the current one) and a signed-out
        # browser both get the bare refusal the box's script words itself.
        other = f"/admin/reports/{uuid4()}/families/find"
        response, body = search(browser, other, {"search": "examp"})
        assert response.status_code == 403 and body == b""
        anonymous = Client()
        response = anonymous.post(find, {"search": "examp"})
        assert response.status_code == 403 and response.content == b""
    contexts = [
        context
        for context in AuditContext.objects.filter(
            event__event_type="family_directory_viewed"
        ).values_list("context", flat=True)
        if context["search_used"]
    ]
    assert contexts and "xamp" not in json.dumps(contexts).lower()
    assert "Example" not in json.dumps(contexts)
    assert any(context["matching_count"] == 1 for context in contexts)


def test_search_finds_active_members_and_envelope_numbers(live_response_service):
    """Any active Member's name and the envelope number find the Family (#664).

    The match never changes the row: a Family found by a Member's name is
    the same row it is when found by its head, and the stripped matching
    column never reaches the page or an export capture.
    """
    harness = live_response_service
    data = response_source()
    data.families[7] = data.families[1] | {
        "familyDUID": 7,
        "familyID": 107,
        "envelopeNumber": 4321,
    }
    for duid, first, extra in (
        (700, "Zed", {"memberType": "Head"}),
        (701, "Penelope", {"memberType": "Other", "nickName": "Nell"}),
        (702, "Ghostly", {"memberType": "Other", "memberStatus": "Inactive"}),
    ):
        data.members[duid] = (
            data.members[3]
            | {
                "memberDUID": duid,
                "familyDUID": 7,
                "firstName": first,
                "emailAddress": "",
            }
            | extra
        )
    # A Family that is not portal-eligible (registered elsewhere), with an
    # active Member, stays out of the directory however it is searched.
    data.families[8] = data.families[1] | {
        "familyDUID": 8,
        "familyID": 108,
        "registeredOrganizationID": 999,
    }
    data.members[800] = data.members[3] | {
        "memberDUID": 800,
        "familyDUID": 8,
        "firstName": "Quillon",
        "emailAddress": "",
    }
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    for text, expected in (
        # A child's first name, first and last name, and nickname.
        ("penel", [7]),
        ("Penelope Example", [7]),
        ("nell example", [7]),
        ("NELL", [7]),
        # The envelope number, whole or in part, like the DUID.
        ("4321", [7]),
        ("432", [7]),
        # An inactive Member does not find the Family.
        ("Ghostly", []),
        # A not-portal-eligible Family's active Member finds nothing.
        ("Quillon", []),
        # LIKE wildcards are plain characters: they match nothing.
        ("%", []),
        ("_", []),
        # Unchanged: the head's name, the surname, nothing.
        ("zed", [7]),
        ("Nobody", []),
    ):
        report = page(harness, search=text)
        assert [row["family_duid"] for row in report["rows"]] == expected, text
    # A Member match returns exactly the row any other match returns.
    row = page(harness, search="penelope")["rows"][0]
    assert row == page(harness, search="zed")["rows"][0]
    assert row["envelope"] == "4321" and "envelope_number" not in row
    assert [head["name"] for head in row["heads"]] == ["Zed Example"]
    # The empty search still lists every Family, matching nothing extra.
    assert page(harness)["total"] == 2
