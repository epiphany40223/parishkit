"""Financial stewardship detail under the real web role, with exact money."""

import json
import logging
from dataclasses import asdict, replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports import financial_views
from parishkit.stewardship.reports.financial import (
    PAGE_SIZE,
    FinancialQuery,
    financial_page,
    giving_proof,
)
from parishkit.stewardship.responses.models import SubmissionReceiptOccurrence
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.snapshots import promote_snapshot

from ..financial_factory import record
from ..test_financial_answers import CHECK, OPTIONS, OTHER
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change, close_campaign, command
from .response_builders import activate_response_service, response_source
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import login as family_login
from .test_family_mail_dispatch_postgresql import claim
from .test_financial_source_postgresql import financial_source
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_responses_postgresql import respond, revisit
from .test_receipt_dispatch_postgresql import begin
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare
from .test_source_snapshots_postgresql import permit
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())


def pledge(harness, form, **financial):
    """Submit one complete live response carrying this financial answer."""
    answers = answers_for(form)
    answers["financial"] = (
        dict(annual_pledge="1,234.5", frequency="monthly", shares={}) | financial
    )
    return respond(harness, form, answers)


PROVE = object()


def report(harness, *, principal=STAFF, proof=PROVE, page_size=PAGE_SIZE, **values):
    """Read through actual restricted SQL grants, not the migration owner."""
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return financial_page(
            campaign.pk,
            FinancialQuery.parse(values),
            principal,
            proof=giving_proof(campaign) if proof is PROVE else proof,
            parish_name="Sample Parish",
            configuration=campaign.active_configuration.values,
            page_size=page_size,
        )


def family_session(harness, duid):
    """Sign another real household in with its own live campaign credential."""
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=duid)
    code = harness.rings.general.decrypt(
        family.code_ciphertext, context=code_context(family.pk)
    ).decode()
    client, response = family_login(code)
    assert response.status_code == 302
    return replace(harness, code=code, client=client, request=response.wsgi_request)


def test_effective_response_exact_money_filters_and_privacy(response_service):
    """One row per effective live response, with only its own source money.

    The exact totals are the proof: another Family's decoy rows, if wrongly
    admitted, would be summed into them rather than shown beside them.
    """
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        first = pledge(
            harness, load_form(harness), shares={CHECK: "", OTHER: "Stock gift"}
        )
    page = report(harness)
    assert page["total"] == 1 and len(page["rows"]) == 1
    row = page["rows"][0]
    assert row["annual"].display == "$1,234.50"
    # 1,234.50 / 12 is 102.875: the exact helper rounds half up, no float drift.
    assert row["installment"].display == "$102.88"
    assert row["frequency_label"] == "Monthly" and row["family_version"] == 1
    assert [share["text"] for share in row["shares"]] == ["", "Stock gift"]
    assert all(share["label"] != "Unavailable share method" for share in row["shares"])
    # The harness source holds 1,200.00 pledged and 100.00 contributed for this
    # Family's mapped comparison funds; the 9,999.00 and 8,888.00 rows belong to
    # another Family and would change these exact totals if they were admitted.
    assert row["source_pledge"].display == "$1,200.00"
    assert row["source_contributions"].display == "$100.00"
    summary = page["summary"]
    assert summary["families"] == 1
    assert summary["annual_total"].display == "$1,234.50"
    assert dict(summary["frequencies"])["Monthly"] == 1
    assert summary["no_share"] == 0 and len(summary["shares"]) == 2
    assert page["metadata"]["giving_through"] is not None

    # Without the completeness proof, source money is unavailable, never zero.
    proven = giving_proof(
        Campaign.objects.select_related("active_configuration").get(
            pk=harness.campaign.pk
        )
    )
    assert proven is not None
    # A proof of any other snapshot or configuration withholds too: the proof is
    # separate statements away from the report, so a change in between must not
    # attribute another snapshot's money to this window.
    for proof in (
        None,
        proven | {"snapshot": str(uuid4())},
        proven | {"configuration": str(uuid4())},
    ):
        withheld = report(harness, proof=proof)
        row = withheld["rows"][0]
        assert not row["source_pledge"].available
        assert row["source_pledge"].display == "Unavailable"
        assert not row["source_contributions"].available
        assert row["annual"].display == "$1,234.50"
        assert withheld["metadata"]["giving_through"] is None

    for values, found in (
        ({"amount": "nonzero"}, 1),
        ({"amount": "zero"}, 0),
        ({"pledge_min": "1234.50", "pledge_max": "1234.50"}, 1),
        ({"pledge_max": "1234.49"}, 0),
        ({"pledge_min": "1234.51"}, 0),
        ({"frequency": "monthly"}, 1),
        ({"frequency": "weekly"}, 0),
        ({"frequency": "none"}, 0),
        ({"share": CHECK}, 1),
        ({"share": "none"}, 0),
        ({"share": str(uuid4())}, 0),
        ({"search": "no such family"}, 0),
        ({"search": str(row["family_duid"])}, 1),
        ({"active": "inactive"}, 0),
        ({"latest_start": "2000-01-01", "latest_end": "2999-12-31"}, 1),
        ({"first_end": "2000-01-01"}, 0),
    ):
        assert report(harness, **values)["total"] == found, values

    # A replacement keeps one row: the latest intent with its first-response date.
    with web_login():
        second = pledge(harness, revisit(harness), annual_pledge="0", frequency="")
    replaced = report(harness)
    assert replaced["total"] == 1
    current = replaced["rows"][0]
    assert current["id"] == str(second.pk) and current["family_version"] == 2
    assert current["first_submitted_at"] == first.submitted_at
    assert current["annual"].display == "$0.00"
    # This zero pledge omitted its frequency, so there is no installment.
    assert not current["installment"].available
    assert current["frequency_label"] == "No frequency" and current["shares"] == []
    assert replaced["summary"]["no_share"] == 1
    assert report(harness, amount="zero", frequency="none", share="none")["total"] == 1

    outsider = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9}))
    with pytest.raises(PermissionError):
        report(harness, principal=outsider)


def test_unproven_giving_and_closed_parameters(response_service):
    """An incomplete giving read withholds money; SQL refuses unknown parameters."""
    harness = response_service
    financial_source(harness, covered=False)
    harness = activate_response_service(harness)
    with web_login():
        pledge(harness, load_form(harness))
    row = report(harness)["rows"][0]
    assert not row["source_pledge"].available
    assert not row["source_contributions"].available
    assert report(harness)["metadata"]["giving_through"] is None

    neutral = {"filters": FinancialQuery().form_values(), "proof": None}
    identity = str(uuid4())
    invalid = (
        # Wrong containers: the closed refusal, never a container operator's own
        # error for a scalar, an array or a JSON null where an object belongs.
        5,
        [],
        None,
        neutral | {"filters": None},
        neutral | {"filters": "any"},
        neutral | {"filters": []},
        neutral | {"proof": []},
        neutral | {"proof": "proof"},
        neutral | {"proof": True},
        neutral | {"proof": {"snapshot": identity}},
        neutral | {"proof": {"snapshot": identity, "configuration": identity, "x": 1}},
        neutral | {"proof": {"snapshot": identity.upper(), "configuration": identity}},
        neutral | {"proof": {"snapshot": identity, "configuration": 7}},
        neutral | {"extra": 1},
        {"filters": neutral["filters"]},
        neutral | {"filters": neutral["filters"] | {"pledge_min": "1,234"}},
        neutral | {"filters": neutral["filters"] | {"pledge_min": "12.5"}},
        neutral
        | {"filters": neutral["filters"] | {"pledge_min": "9", "pledge_max": "1"}},
        neutral | {"filters": neutral["filters"] | {"latest_start": "2026-02-30"}},
        neutral | {"filters": neutral["filters"] | {"sort": "random"}},
        neutral | {"filters": neutral["filters"] | {"amount": "all"}},
        neutral | {"filters": neutral["filters"] | {"share": "no-such-option"}},
        # Both bounds present with one malformed: the closed refusal, never a
        # cast error that would echo the submitted value.
        neutral
        | {"filters": neutral["filters"] | {"pledge_min": "1,234", "pledge_max": "5"}},
        neutral
        | {"filters": neutral["filters"] | {"pledge_min": "5", "pledge_max": "x"}},
        neutral
        | {
            "filters": neutral["filters"]
            | {"first_start": "2026-13-01", "first_end": "2026-01-01"}
        },
        # Well-formed but inverted intervals: SQL is the authority for a later
        # capture that never passes through the application's parser.
        neutral
        | {
            "filters": neutral["filters"]
            | {"first_start": "2026-02-01", "first_end": "2026-01-31"}
        },
        neutral
        | {
            "filters": neutral["filters"]
            | {"latest_start": "2026-02-01", "latest_end": "2026-01-31"}
        },
        neutral | {"filters": {"search": ""}},
        neutral | {"filters": neutral["filters"] | {"unknown": ""}},
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for parameters in invalid:
            with (
                pytest.raises(DatabaseError, match="Invalid financial report"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "SELECT stewardship_financial_report_v1(%s,%s::jsonb,1,50)",
                    [harness.campaign.pk, json.dumps(parameters)],
                )
        # Positive control: the neutral object is accepted, in paged and complete
        # modes, so each refusal above is about its one changed value.
        with connection.cursor() as cursor:
            # A complete result needs no page size; a page must state one.
            for page_number, page_size in ((1, 50), (1, 1), (1, 200), (None, None)):
                cursor.execute(
                    "SELECT stewardship_financial_report_v1(%s,%s::jsonb,%s,%s)"
                    "->'total'",
                    [harness.campaign.pk, json.dumps(neutral), page_number, page_size],
                )
                assert cursor.fetchone()[0] in (1, "1")
            for page_number, page_size in (
                (0, 50),
                (10001, 50),
                (1, 0),
                (1, 201),
                (1, None),
            ):
                with (
                    pytest.raises(DatabaseError, match="Invalid financial report"),
                    transaction.atomic(),
                ):
                    cursor.execute(
                        "SELECT stewardship_financial_report_v1(%s,%s::jsonb,%s,%s)",
                        [
                            harness.campaign.pk,
                            json.dumps(neutral),
                            page_number,
                            page_size,
                        ],
                    )
    for values in (
        {"pledge_min": "1,234"},
        {"pledge_min": "5", "pledge_max": "1"},
        {"latest_start": "2026-02-30"},
        {"first_start": "2026-02-01", "first_end": "2026-01-01"},
        {"share": "bad share!"},
        {"sort": "random"},
        {"unknown": "x"},
    ):
        with pytest.raises(ValueError):
            FinancialQuery.parse(values)


def test_retained_wording_survives_a_later_year_label_edit(response_service):
    """A row keeps the wording its Family saw; summary and filter use today's."""
    harness = response_service
    option = dict(id=CHECK, label="By check in {{ campaign_year }}", free_text=False)
    financial_source(harness, options=[option])
    harness = activate_response_service(harness)
    with web_login():
        pledge(harness, load_form(harness), shares={CHECK: ""})
    seen = report(harness)["rows"][0]["shares"][0]["label"]
    assert seen.startswith("By check in ") and "Jubilee" not in seen
    receipt = change(
        harness.service.store,
        harness.service.store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(harness.campaign.pk),
                "values": {"year_label": "Jubilee"},
            }
        ],
    )
    assert receipt.state == "applied"
    after = report(harness)
    # The real lookup finds the immutable configuration that Family answered,
    # not the campaign's newer active one.
    assert after["rows"][0]["shares"] == [{"label": seen, "text": ""}]
    assert after["share_choices"] == [(CHECK, "By check in Jubilee")]
    assert after["summary"]["shares"] == [("By check in Jubilee", 1)]


def test_archived_campaign_proves_its_own_pinned_source(response_service):
    """An archived report follows its retained source, never a successor's."""
    retained, _ = financial_source(response_service)
    harness = activate_response_service(response_service)
    with web_login():
        submitted = pledge(harness, load_form(harness))
    # Archive may not abandon a live response's confirmation, so deliver it
    # through the real mail-dispatch owner first.
    message = OutboxMessage.objects.get(
        pk=SubmissionReceiptOccurrence.objects.get(submission=submitted).outbox_id
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert begin(message, execution) is not None
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    actor = uuid4()
    close_campaign(harness.campaign, actor)
    for row in TaskRun.objects.filter(state="running"):
        act(_status(row), "permanent_failure")
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        command(harness.campaign, actor, Action.ARCHIVE)
        successor, lease = prepare(response_source())
        try:
            with work_transaction():
                promote_snapshot(successor.pk, lease, admit=permit, reconcile=permit)
        finally:
            release_source(lease)
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=harness.campaign.pk
        )
        assert campaign.state == "archived"
        # The pinned-source read must work under the restricted web role.
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            transaction.atomic(),
        ):
            proof = giving_proof(campaign)
        assert proof is not None
        assert proof["snapshot"] == str(retained.pk) != str(successor.pk)
        row = report(harness)["rows"][0]
        assert row["source_pledge"].display == "$1,200.00"
        assert row["source_contributions"].display == "$100.00"
        # A proof of the newer current snapshot is not a proof of this report.
        current = report(harness, proof=proof | {"snapshot": str(successor.pk)})
        assert not current["rows"][0]["source_pledge"].available
        assert current["metadata"]["giving_through"] is None


def test_campaign_without_the_module_is_denied_and_unlinked(response_service, google):
    """No financial module means no entry, no report and no empty financial page."""
    harness = response_service
    assert "financial" not in harness.campaign.active_configuration.values["modules"]
    route = f"/admin/reports/{harness.campaign.pk}/financial/"
    with pytest.raises(PermissionError, match="not enabled"):
        report(harness)
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert get(browser, route)[0].status_code == 403
        response, body = get(browser, route.replace("financial", "participation"))
        assert response.status_code == 200 and route.encode() not in body


def head(member, family):
    """One active head makes a synthetic household eligible for the campaign."""
    return dict(
        memberDUID=member,
        familyDUID=family,
        firstName="Head",
        lastName=f"Of{family}",
        memberType="Head",
        memberStatus="Active",
        emailAddress=f"head{family}@example.org",
    )


def test_source_totals_respect_fund_window_and_giving_cutoff(response_service):
    """This Family's own rows outside the mapped fund, window or cutoff never count."""
    harness = response_service
    own = dict(family_key="1")
    snapshot, _ = financial_source(
        harness,
        # A fixed past cutoff keeps this case independent of today's date.
        giving_as_of="2026-03-01",
        extra_funds={4: dict(fundId=4, name="Unmapped", active=True)},
        extra_pledges={
            "203": record("7654.32", effective_date="2026-01-01", fund_key="4", **own),
            "204": record("4321.09", effective_date="2025-12-31", **own),
            "205": record("6543.21", effective_date="2027-01-01", **own),
            # The window's last day counts; a pledge is not cut off by giving.
            "206": record("11.00", effective_date="2026-12-31", **own),
        },
        extra_contributions={
            "304": record("333.33", effective_date="2025-12-31", **own),
            "305": record("444.44", effective_date="2026-03-02", **own),
            "306": record("777.77", effective_date="2026-01-01", fund_key="4", **own),
            # The cutoff day itself is part of the complete giving read.
            "307": record("5.00", effective_date="2026-03-01", **own),
        },
    )
    harness = activate_response_service(harness)
    with web_login():
        pledge(harness, load_form(harness))
    campaign = Campaign.objects.get(pk=harness.campaign.pk)
    # The application's own rule refuses a backdated cutoff, so this exercises
    # the SQL predicates directly with a proof of exactly what SQL selects.
    assert giving_proof(campaign) is None
    proof = {
        "snapshot": str(snapshot.pk),
        "configuration": str(campaign.active_configuration_id),
    }
    page = report(harness, proof=proof)
    row = page["rows"][0]
    assert row["source_pledge"].display == "$1,211.00"
    assert row["source_contributions"].display == "$105.00"
    assert page["metadata"]["giving_through"].isoformat() == "2026-03-01"
    # Those exact totals are the proof: a wrongly admitted row would be summed
    # into them, not displayed beside them, so no text scan could catch it.


def test_several_families_summary_order_and_pages(response_service):
    """The summary covers every match, never the page; money stays with its Family."""
    harness = response_service
    financial_source(
        harness,
        options=map(asdict, OPTIONS),
        extra_families={
            6: dict(familyDUID=6, registeredOrganizationID=5, lastName="Zeta")
        },
        extra_members={21: head(21, 2), 22: head(22, 6)},
    )
    harness = activate_response_service(harness)
    # The harness freezes the campaign clock, so advance it between submissions
    # to give the two date sorts distinct times rather than a three-way tie.
    opened = harness.campaign.active_configuration.starts_at
    with campaign_clock(opened + timedelta(hours=1)), web_login():
        pledge(harness, load_form(harness), shares={CHECK: ""})
    with campaign_clock(opened + timedelta(hours=2)), web_login():
        second = family_session(harness, 2)
        pledge(second, load_form(second), annual_pledge="500", frequency="weekly")
    with campaign_clock(opened + timedelta(hours=3)), web_login():
        third = family_session(harness, 6)
        pledge(third, load_form(third), annual_pledge="0", frequency="")
    page = report(harness)
    rows = {row["family_duid"]: row for row in page["rows"]}
    assert page["total"] == 3 and set(rows) == {1, 2, 6}
    # Each Family receives only its own source rows; a proven complete read with
    # no matching row is a real zero, not an unavailable amount.
    assert rows[1]["source_pledge"].display == "$1,200.00"
    assert rows[1]["source_contributions"].display == "$100.00"
    assert rows[2]["source_pledge"].display == "$9,999.00"
    assert rows[2]["source_contributions"].display == "$8,888.00"
    assert rows[6]["source_pledge"].available
    assert rows[6]["source_pledge"].display == "$0.00"
    assert rows[6]["source_contributions"].display == "$0.00"
    assert rows[2]["installment"].display == "$9.62"  # 500 / 52, half up.
    summary = page["summary"]
    assert summary["families"] == 3
    assert summary["annual_total"].display == "$1,734.50"
    assert dict(summary["frequencies"]) == {
        "Weekly": 1,
        "Monthly": 1,
        "Quarterly": 0,
        "Annual": 0,
        "No frequency": 1,
    }
    assert summary["no_share"] == 2 and sum(dict(summary["shares"]).values()) == 1

    names = [row["family_name"] for row in page["rows"]]
    assert names == sorted(names, key=str.lower)
    by_name = [row["family_duid"] for row in page["rows"]]
    for sort, expected in (
        ("name", by_name),
        ("name_desc", by_name[::-1]),
        ("pledge", [6, 2, 1]),
        ("pledge_desc", [1, 2, 6]),
        ("oldest", [1, 2, 6]),
        ("newest", [6, 2, 1]),
    ):
        found = [row["family_duid"] for row in report(harness, sort=sort)["rows"]]
        times = {key: row["submitted_at"].isoformat() for key, row in rows.items()}
        assert found == expected, (sort, times)
    for values, expected in (
        ({"amount": "zero"}, {6}),
        ({"amount": "nonzero"}, {1, 2}),
        ({"frequency": "weekly"}, {2}),
        ({"frequency": "none"}, {6}),
        ({"share": CHECK}, {1}),
        ({"share": "none"}, {2, 6}),
        ({"pledge_min": "500", "pledge_max": "500.00"}, {2}),
        ({"search": "zETa"}, {6}),
        ({"search": "6"}, {6}),
        # Dates are campaign-local days: every response is on the opening day.
        ({"first_start": "2026-10-01", "first_end": "2026-10-01"}, {1, 2, 6}),
        ({"latest_start": "2026-10-01", "latest_end": "2026-10-01"}, {1, 2, 6}),
        ({"first_start": "2026-10-02"}, set()),
        ({"latest_end": "2026-09-30"}, set()),
    ):
        filtered = report(harness, **values)
        assert {row["family_duid"] for row in filtered["rows"]} == expected, values
        # A filtered summary describes exactly the filtered Families.
        assert filtered["summary"]["families"] == filtered["total"] == len(expected)
    statuses = [
        report(harness, active=value)["total"]
        for value in ("active", "inactive", "unavailable")
    ]
    assert sum(statuses) == 3

    # Real page boundaries: pages are disjoint, in order and together complete,
    # while every page carries the same summary of all three Families.
    pages = [report(harness, page=str(n), page_size=2) for n in (1, 2, 3)]
    assert [[row["family_duid"] for row in page["rows"]] for page in pages] == [
        by_name[:2],
        by_name[2:],
        [],
    ]
    for paged in pages:
        assert paged["total"] == 3 and paged["summary"] == summary
    # The second page's Family still receives its own source money.
    last = pages[1]["rows"][0]
    assert (
        last["source_pledge"].display
        == rows[last["family_duid"]]["source_pledge"].display
    )
    # A page past the end has no rows, but the same whole-result summary.
    beyond = report(harness, page="2")
    assert beyond["rows"] == [] and beyond["total"] == 3
    assert beyond["summary"] == summary
    # The complete, unpaged mode that a later capture depends on.
    neutral = {"filters": FinancialQuery().form_values(), "proof": None}
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_financial_report_v1(%s,%s::jsonb,NULL,NULL)::text",
            [harness.campaign.pk, json.dumps(neutral)],
        )
        complete = json.loads(cursor.fetchone()[0])
    assert [row["family_duid"] for row in complete["rows"]] == by_name
    assert all(row["pledge_total"] is None for row in complete["rows"])


def test_native_page_filters_privately_and_denies_leaders(
    response_service, google, monkeypatch, caplog
):
    """An Admin's real session filters by CSRF POST; a leader never reaches money."""
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        pledge(harness, load_form(harness), shares={CHECK: ""})
    name = report(harness)["rows"][0]["family_name"].encode()
    route = f"/admin/reports/{harness.campaign.pk}/financial/"
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        assert name in body and b"$1,234.50" in body and b"$102.88" in body
        assert b"$1,200.00" in body and b"$100.00" in body
        assert b'datetime=""' not in body and b"?search=" not in body
        # Identifying filters are private POST state, never a URL.
        assert get(browser, route + "?search=Private")[0].status_code == 400
        assert browser.post(route, {"amount": "zero"}).status_code == 403  # No CSRF.
        response, body = search(browser, route, {"amount": "zero"})
        assert response.status_code == 200 and name not in body
        assert b"No matching pledges." in body and b"$1,234.50" not in body
        _, body = search(browser, route, {"pledge_min": "1234.50", "share": CHECK})
        assert name in body and b'value="1234.50"' in body
        for invalid in ({"pledge_min": "1,234"}, {"sort": "random"}, {"extra": "x"}):
            response, body = search(browser, route, invalid)
            assert response.status_code == 400
            # An ordinary typo gets an explanation and a way back, never an echo.
            assert b"without commas" in body and route.encode() in body
            assert b"1,234" not in body and response["Cache-Control"] == "no-store"
        # A stale Next click past the end keeps the count and says what happened.
        _, body = search(browser, route, {"page": "9"})
        assert b"past the last matching pledge" in body
        assert b"No matching pledges." not in body
        assert b'<button name="page" value="1">' in body
        # Another campaign is indistinguishable from none.
        wrong = f"/admin/reports/{uuid4()}/financial/"
        assert get(browser, wrong)[0].status_code == 403
        # Denied even with malformed filters: never a filter error whose link
        # back could only lead to a denial.
        assert search(browser, wrong, {"sort": "random"})[0].status_code == 403
        # The campaign reports page offers the entry only with the module enabled.
        _, body = get(browser, route.replace("financial", "participation"))
        assert route.encode() in body

    # Two different routes to the same recovery page. The shared guard answers
    # unavailable inputs itself and the view substitutes its page; a ValueError
    # while shaping data reaches the view's own handler and must not be blamed on
    # the requester's filters. Patches are scoped so the Google fixture's own
    # patch survives for the leader below.
    for failure in (ReadUnavailable("Inputs are unavailable."), ValueError(name)):

        def fail(*args, error=failure, **kwargs):
            """Stand in for the read model failing after admission."""
            raise error

        with (
            monkeypatch.context() as patch,
            caplog.at_level(logging.ERROR, logger="parishkit.stewardship"),
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
        ):
            patch.setattr(financial_views, "financial_page", fail)
            response, body = get(browser, route)
            assert response.status_code == 503 and response["Retry-After"] == "5"
            assert b"temporarily unavailable" in body and route.encode() in body
            assert name not in body and response["Cache-Control"] == "no-store"
    # A value the read model could not shape is a persistent defect: recorded as
    # an operational failure once, naming nothing; the transient route is not.
    failures = [
        record
        for record in caplog.records
        if record.getMessage() == "report_shaping_failed"
    ]
    assert len(failures) == 1 and failures[0].levelno == logging.ERROR
    assert name.decode() not in str(vars(failures[0]))
    # Signing the leader in changes the login policy, so this comes last.
    other, *_ = leader(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert get(other, route)[0].status_code == 403
        assert search(other, route, {"amount": "any"})[0].status_code == 403
    contexts = list(
        AuditContext.objects.filter(
            event__event_type="financial_report_viewed"
        ).values_list("context", flat=True)
    )
    assert {"succeeded", "started", "failed"} <= {
        context["outcome"] for context in contexts
    }
    assert name.decode() not in str(contexts) and "1234" not in str(contexts)
