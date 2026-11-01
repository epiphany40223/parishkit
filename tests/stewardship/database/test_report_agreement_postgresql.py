"""The Reports and queries pages agree on one population's figures (#2324).

ui-conventions "Population of report figures": for one Families counted
population and one day, the Response dashboard, Financial stewardship and
Campaign comparison give the same Families responded, annual pledge total
and campaign Families, and each page names the population it counts. One
financial campaign holds every kind of Family that has made the pages
disagree:

- 1, a responder still in the campaign;
- 2, a responder who left the campaign, with a pledge above zero;
- 6, a responder gone from ParishSoft;
- 7, a Family that left and never responded;
- 8, a Family with only a Testing submission;
- 9, a response Staff entered through Open form;
- 10, a Family that cannot contribute financially;
- 11, a Family with a pledge Edit in force;
- 12, a Family whose pledge Edit, made on its first response, a
  resubmission with a new amount then replaced (no Edit in force);
- 13, a responder that moved its registration to another parish but is
  still active in ParishSoft (#2335).

The campaign runs once on the legacy Families rule, the path Production's
campaign uses, which made Family 13 disagree (Financial stewardship read
ParishSoft's active flag alone, the dashboard the legacy rule itself), and
once on a configured rule (Active parishioners). Each rule leaves Families
2, 7 and 13 out of today's campaign Families, so the figures are the same.
Active parishioners selects the same Families as the legacy rule, so the
configured case cannot catch a rule that differs from it; a rule that does
is #2338's.

A response without a financial answer cannot exist in a financial
campaign: the form refuses a blank pledge unless the Family says it cannot
contribute (Family 10), so every response the dashboard counts as
Submitted is a pledge row on Financial stewardship.

Each page is read over HTTP as the restricted web login, the way an
Administrator opens it; Campaign comparison, which counts only the
historical population (its archives hold no other), is read on the fact
set's last day.
"""

import json
import re
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import Max
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns import campaign_families
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import response_dashboard
from parishkit.stewardship.reports.facts import review_watermark
from parishkit.stewardship.reports.materialization import materialize_fact_set
from parishkit.stewardship.reports.participation import SCOPE_LABELS
from parishkit.stewardship.responses.financial_inputs import financial_definition
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.leases import acquire_source
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    stage_entities,
)

from ..campaign_factory import campaign as campaign_record
from ..test_campaign_families import ACTIVE
from ..test_financial_answers import CHECK, OPTIONS
from ..test_source_corpus import TODAY
from . import response_builders
from .auth_builders import signed_in, unguarded
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service, response_source
from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_directories_postgresql import page
from .test_fact_materialization_postgresql import allocation
from .test_financial_exports_postgresql import staff_entered
from .test_financial_report_postgresql import family_session, head, pledge
from .test_financial_source_postgresql import financial_source
from .test_pledge_review_postgresql import admin
from .test_pledge_share_methods_postgresql import edit
from .test_report_workspace_postgresql import read
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import promote
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)

# The Families beside the harness's own Family 1, each with one head.
EXTRA = (6, 7, 8, 9, 10, 11, 12, 13)
HEADS = {40 + duid: head(40 + duid, duid) for duid in (2, *EXTRA)}
# Each live answer, by Family DUID: whole dollars, so Campaign comparison's
# whole-dollar Annual pledges reads the same total. Family 11's own 200 is
# replaced by its Edit's 400. Family 12's own 100 is edited to 600, which its
# resubmission of 700 then replaces.
ANSWERS = {
    1: "1000",
    2: "500",
    6: "250",
    9: "300",
    10: None,
    11: "200",
    12: "100",
    13: "50",
}
EDITED = Decimal("400")
REPLACED_EDIT = Decimal("600")
RESUBMITTED = "700"
LEFT = (2, 7)
GONE = 6
# Active in ParishSoft, but registered at another parish since the reload.
MOVED = 13
ELSEWHERE = 54321
# The expected figures: every responder under historical; under current,
# only those still in this campaign (Families 2, 6 and 13 are not).
EXPECTED = {
    "historical": (8, Decimal("3200")),
    "current": (5, Decimal("2400")),
}
# Under historical, the "No longer in this campaign" refinement: the
# responders still in ParishSoft that left (2, and 13 under every rule).
LEFT_RESPONDERS = (2, Decimal("550"))
# The two rules the campaign runs on: the legacy one, and Active
# parishioners.
RULES = pytest.mark.parametrize(
    "response_service",
    [None, ACTIVE],
    ids=["legacy-rule", "configured-rule"],
    indirect=True,
)
# A Family group the legacy Families rule reads as inactive.
INACTIVE_GROUP = 99


def families():
    """Every extra Family's ParishSoft row, in the organization's records."""
    return {
        duid: dict(familyDUID=duid, registeredOrganizationID=5, lastName=f"Zeta{duid}")
        for duid in EXTRA
    }


def reload(harness):
    """Promote a newer full load in which Families 2 and 7 left the campaign
    (their Family group is Inactive), Family 13 registered at another parish
    and Family 6 is gone from ParishSoft, under the campaign's own Families
    rule (a live campaign keeps its configuration)."""
    rule = campaign_families.stored_rule(harness.campaign.active_configuration.values)
    data = replace(response_source(), organization_id=12345)
    kept = {duid: row for duid, row in families().items() if duid != GONE}
    data.families.update(kept)
    data.members.update(
        {key: row for key, row in HEADS.items() if row["familyDUID"] != GONE}
    )
    data.family_groups[INACTIVE_GROUP] = "Inactive"
    for family in data.families.values():
        duid = family["familyDUID"]
        if duid in LEFT:
            family["famGroupID"] = INACTIVE_GROUP
        elif duid != 1:
            family.setdefault("famGroupID", 7)
    data.families[MOVED]["registeredOrganizationID"] = ELSEWHERE
    promoted, rows = load(harness, data, rule)
    assert rows["1"]["portal_eligible"] and not rows["2"]["portal_eligible"]
    assert not rows["7"]["portal_eligible"] and str(GONE) not in rows
    # Family 13 is still active in ParishSoft, which the legacy branch of
    # Financial stewardship's selection read before 0197 (#2335).
    assert rows[str(MOVED)]["active"] and not rows[str(MOVED)]["portal_eligible"]
    assert all(
        (campaign_families.KEY in row) is (rule is not None) for row in rows.values()
    )
    return promoted


def load(harness, data, rule=None):
    """Promote ``data`` as a full load normalized under the Families ``rule``
    (None: the legacy rule), its load recording the rule.

    As ``financial_source`` loads one, with its giving, but without changing
    the campaign's configuration, which a live campaign keeps. Returns the
    promoted snapshot and its Family rows.
    """
    values = harness.campaign.active_configuration.values
    definition = financial_definition(values, campaign_id=harness.campaign.pk)
    for family in data.families.values():
        if family.get("registeredOrganizationID") == 5:
            family["registeredOrganizationID"] = 12345
    corpus = normalize_core(data, as_of=TODAY, families=rule)
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=12345, admit=permit)
    for kind, entities in corpus.items():
        stage_entities(snapshot.pk, claim, kind=kind, entities=entities, admit=permit)
    as_of = snapshot.started_at.date().isoformat()
    evidence = {
        "schema": "source-load-v1",
        "window_digest": definition.window.digest,
        "as_of_date": as_of,
        "giving_as_of_date": as_of,
    }
    if rule is not None:
        evidence[campaign_families.KEY] = campaign_families.evidence(
            rule, data.family_groups
        )
    finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor=refresh_cursor(
            snapshot_id=snapshot.pk,
            started_at=snapshot.started_at,
            window_digest=definition.window.digest,
            evidence=evidence,
        ),
        admit=permit,
    )
    promoted = promote(snapshot, claim, harness.campaign, harness.rings)
    return promoted, corpus["family"]


def rehearsal_only(harness, duid, template):
    """Give Family ``duid`` a Testing submission and nothing else.

    A copy of the live ``template`` row in Testing mode, as the Financial
    export test makes one (guards bypassed for this fixture row only): a
    live campaign takes no Testing response through the form.
    """
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=duid)
    with work_transaction(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM stewardship_submission WHERE id = %s", [template.pk]
        )
        columns = [column.name for column in cursor.description]
        values = dict(zip(columns, cursor.fetchone(), strict=True))
        cursor.execute(
            "SELECT max(campaign_sequence) + 1 FROM stewardship_submission "
            "WHERE campaign_id = %s",
            [template.campaign_id],
        )
        values.update(
            id=uuid4(),
            family_id=family.pk,
            mode="test",
            rehearsal_epoch_id=uuid4(),
            campaign_sequence=cursor.fetchone()[0],
            baseline_id=uuid4(),
            prior_submission_id=None,
        )
        values = {
            key: json.dumps(value) if isinstance(value, dict | list) else value
            for key, value in values.items()
        }
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            f"INSERT INTO stewardship_submission ({', '.join(values)}) "
            f"VALUES ({', '.join(['%s'] * len(values))})",
            list(values.values()),
        )


@pytest.fixture
def agreement(response_service, google, monkeypatch):
    """The campaign above, its facts built through day 2, signed in.

    Returns (signed-in browser, the fact set's last campaign day, the
    instant the pages are read at).
    """
    harness = response_service
    financial_source(
        harness,
        modules=["financial"],
        options=map(asdict, OPTIONS),
        extra_families=families(),
        extra_members=HEADS,
    )
    harness = activate_response_service(harness)
    opened = harness.campaign.active_configuration.starts_at
    submissions = {}
    for hour, (duid, amount) in enumerate(ANSWERS.items(), start=1):
        with campaign_clock(opened + timedelta(hours=hour)), web_login():
            family = harness if duid == 1 else family_session(harness, duid)
            answer = (
                dict(annual_pledge="", frequency="", shares={}, cannot_give=True)
                if amount is None
                else dict(annual_pledge=amount, frequency="annual", shares={CHECK: ""})
            )
            pledge(family, load_form(family), **answer)
        # The response just made: ``pledge`` returns the highest Family
        # version of any Family, which need not be this one's.
        submissions[duid] = Submission.objects.filter(
            campaign=harness.campaign, mode="live"
        ).latest("campaign_sequence")
        assert submissions[duid].family.family_duid == duid
    actor = admin()
    edit(
        harness,
        actor,
        submissions[11],
        amount=str(EDITED),
        frequency="annual",
        **{f"value_share_{CHECK}": "on"},
    )
    # Family 12's Edit, on its first response, then a resubmission with a
    # new amount: the Family's newer answer leaves the Edit not in force.
    edit(
        harness,
        actor,
        submissions[12],
        amount=str(REPLACED_EDIT),
        frequency="annual",
        **{f"value_share_{CHECK}": "on"},
    )
    with (
        campaign_clock(opened + timedelta(hours=len(ANSWERS) + 1)),
        web_login(),
    ):
        family = family_session(harness, 12)
        pledge(
            family,
            load_form(family),
            annual_pledge=RESUBMITTED,
            frequency="annual",
            shares={CHECK: ""},
        )
    assert (
        Submission.objects.filter(campaign=harness.campaign, mode="live")
        .latest("campaign_sequence")
        .family.family_duid
        == 12
    )
    staff_entered(submissions[9].pk, actor)
    rehearsal_only(harness, 8, submissions[1])
    newest = reload(harness)
    # The comparison's facts, through day 2, at the live submission and
    # pledge review watermarks, as the worker builds them.
    watermark = Submission.objects.filter(
        campaign=harness.campaign, mode="live"
    ).aggregate(Max("campaign_sequence"))["campaign_sequence__max"]
    record, claim = allocation(
        harness,
        watermark=watermark,
        source=newest,
        reviews=review_watermark(harness.campaign.pk),
    )
    materialize_fact_set(record.pk, claim, admit=permit, interactive=True)
    assert SourceSnapshot.objects.filter(state="promoted").latest("generation") == (
        newest
    )
    # The pages show what the site's mode says: Production here.
    with unguarded():
        SystemConfiguration.objects.update(mode="production")
    browser, login = signed_in()
    assert login.status_code == 302
    now = opened + timedelta(days=2, hours=12)
    # Every page reads at the campaign clock's instant. The funnel alone
    # counts the responses made by the database's own clock
    # (statement_timestamp), which the campaign clock does not move; the
    # responses are dated in the fixture calendar's Oct 2054, so the funnel
    # is read at that same instant too.
    monkeypatch.setattr(response_dashboard, "database_now", lambda: now)
    return browser, 2, now


def tile(body, key):
    """The text of the page's stat tile ``key``."""
    found = re.search(
        rb'data-stat="' + key.encode() + rb'"><span class="stat-label">[^<]*</span> '
        rb'<span class="stat-value[^"]*">([^<]*)<',
        body,
    )
    assert found, key
    return found.group(1).decode().strip()


def count(text):
    """A tile's whole count, "1,234" read as 1234."""
    return int(text.replace(",", ""))


def money(text):
    """A tile's money, "$1,234" or "$1,234.50", exactly."""
    return Decimal(text.removeprefix("$").replace(",", ""))


def chosen(body, choice):
    """The words of the segment the binary choice ``choice`` marks chosen."""
    start = body.index(f'id="{choice}"'.encode())
    found = re.search(rb'aria-current="page">([^<]*)<', body[start:])
    return found.group(1).decode()


def dashboard(browser, scope):
    """The Response dashboard's figures for ``scope`` ("" for none):
    (responded, pledges, Families, label)."""
    route = reverse("admin:response_dashboard") + (f"?scope={scope}" if scope else "")
    response, body = read(browser, route)
    assert response.status_code == 200, body
    return (
        count(tile(body, "submitted")),
        money(tile(body, "annual_pledges")),
        count(tile(body, "active_families")),
        chosen(body, "participation-scope"),
    )


def financial(browser, scope, refinement=""):
    """Financial stewardship's figures: (responded, pledges, label).

    ``refinement`` is the Family status refinement of the historical
    population ("" for none).
    """
    query = "&".join(
        part
        for part in (
            f"scope={scope}" if scope else "",
            f"active={refinement}" if refinement else "",
        )
        if part
    )
    route = reverse("admin:financial_report") + (f"?{query}" if query else "")
    response, body = read(browser, route)
    assert response.status_code == 200, body
    return (
        count(tile(body, "families")),
        money(tile(body, "annual-total")),
        chosen(body, "financial-scope-choice"),
    )


def comparison(browser, day):
    """Campaign comparison's figures on ``day``: (responded, pledges, percent)."""
    route = reverse("admin:campaign_comparison") + f"?day={day}"
    response, body = read(browser, route)
    assert response.status_code == 200, body
    assert f"Day {day} of".encode() in body
    return (
        count(tile(body, "families")),
        money(tile(body, "pledged")),
        tile(body, "percent"),
    )


@RULES
def test_every_page_counts_the_same_families_for_each_population(agreement):
    """The dashboard and Financial stewardship agree under each population,
    and Campaign comparison agrees with them under historical, on the fact
    set's last day; each page names the population it counts. So do they
    under the legacy Families rule and a configured one, and Financial
    stewardship's "No longer in this campaign" lists the responders the
    dashboard's current population leaves out (but the one gone)."""
    browser, last_day, now = agreement
    figures = {}
    with campaign_clock(now), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for scope, (responded, pledges) in EXPECTED.items():
            on_dashboard = dashboard(browser, scope)
            on_financial = financial(browser, scope)
            label = str(SCOPE_LABELS[scope])
            assert on_dashboard[:2] == on_financial[:2] == (responded, pledges), scope
            assert on_dashboard[3] == on_financial[2] == label, scope
            figures[scope] = on_dashboard
        # Campaign Families: under historical every Family ever in this
        # campaign, so the four that left or are gone (2, 6, 7, 13) too, and
        # the Testing-only Family 8 either way.
        assert figures["historical"][2] - figures["current"][2] == len(
            (*LEFT, GONE, MOVED)
        )
        # Without a scope both pages show the shared default, today's
        # campaign Families (the Administrator's decision, #2324).
        assert dashboard(browser, "")[:2] == financial(browser, "")[:2]
        assert financial(browser, "")[:2] == EXPECTED["current"]
        assert financial(browser, "")[2] == str(SCOPE_LABELS["current"])
        # The refinement reads the same eligibility (#2335): the
        # historical responders less the current ones, less the one gone
        # from ParishSoft (Status unavailable).
        assert financial(browser, "historical", "inactive")[:2] == LEFT_RESPONDERS
        assert (
            EXPECTED["historical"][0] - EXPECTED["current"][0] - 1 == LEFT_RESPONDERS[0]
        )
        responded, pledges, percent = comparison(browser, last_day)
        historical = figures["historical"]
        assert (responded, pledges) == EXPECTED["historical"]
        # Its share of campaign Families has the dashboard's denominator.
        share = Decimal(100 * historical[0]) / historical[2]
        assert percent == f"{share.quantize(Decimal('0.1')).normalize():f}%", (
            percent,
            historical,
        )


# A configured Families rule that is not the legacy rule (#2338): the
# "Active parishioners" preset leaves out the Staff Family group, which the
# legacy rule selects as active and registered.
RULE = campaign_families.DEFAULT
STAFF_GROUP = 8
# A Family that responds, then moves to the Staff group: under the rule it
# is still active and registered in ParishSoft but out of the campaign.
RULED = 13
RULED_ANSWERS = {1: "1000", RULED: "500"}


@pytest.fixture
def families_rule(monkeypatch):
    """Create ``response_service``'s campaign with the Families ``RULE``,
    which can be set only when a campaign is created."""
    create = response_builders.add_draft

    def add_draft(store, version, actor, row=None):
        """The fixture's own campaign, with the rule added."""
        row = campaign_record(campaign_families=RULE) if row is None else row
        return create(store, version, actor, row)

    monkeypatch.setattr(response_builders, "add_draft", add_draft)


@pytest.fixture
def ruled(families_rule, response_service, google, monkeypatch):
    """A financial campaign with the Families ``RULE``: Families 1 and
    ``RULED`` respond, then a load under the rule moves ``RULED`` to the
    Staff group, out of the campaign.

    Returns (signed-in browser, harness, the instant the pages are read at).
    """
    harness = response_service
    assert harness.campaign.active_configuration.values[campaign_families.KEY] == RULE
    extra = {RULED: dict(familyDUID=RULED, registeredOrganizationID=5, lastName="Q")}
    heads = {40 + RULED: head(40 + RULED, RULED)}
    financial_source(
        harness,
        modules=["financial"],
        options=map(asdict, OPTIONS),
        extra_families=extra,
        extra_members=heads,
    )
    harness = activate_response_service(harness)
    opened = harness.campaign.active_configuration.starts_at
    for hour, (duid, amount) in enumerate(RULED_ANSWERS.items(), start=1):
        with campaign_clock(opened + timedelta(hours=hour)), web_login():
            family = harness if duid == 1 else family_session(harness, duid)
            pledge(
                family,
                load_form(family),
                annual_pledge=amount,
                frequency="annual",
                shares={CHECK: ""},
            )
    data = replace(response_source(), organization_id=12345)
    data.families.update(extra)
    data.members.update(heads)
    data.family_groups[STAFF_GROUP] = "Staff"
    data.families[RULED]["famGroupID"] = STAFF_GROUP
    _, rows = load(harness, data, RULE)
    # The issue's case: active and registered, yet not in the campaign.
    row = rows[str(RULED)]
    assert row["active"] and row["parishioner"] and not row["portal_eligible"]
    assert rows["1"]["portal_eligible"]
    assert not FamilyCampaign.objects.get(
        campaign=harness.campaign, family_duid=RULED
    ).portal_eligible
    with unguarded():
        SystemConfiguration.objects.update(mode="production")
    browser, login = signed_in()
    assert login.status_code == 302
    now = opened + timedelta(days=2, hours=12)
    monkeypatch.setattr(response_dashboard, "database_now", lambda: now)
    return browser, harness, now


def test_pages_agree_under_a_families_rule_unlike_the_legacy_rule(ruled):
    """Under a configured Families rule the dashboard has its statistics
    (#2338), and it, Financial stewardship and the directory agree on the
    Family the rule left out though it is active and registered: only the
    historical population counts its response, and the directory, which
    lists today's campaign Families, does not list it."""
    browser, harness, now = ruled
    expected = {"historical": (2, Decimal("1500")), "current": (1, Decimal("1000"))}
    figures = {}
    with campaign_clock(now), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for scope, (responded, pledges) in expected.items():
            on_dashboard = dashboard(browser, scope)
            on_financial = financial(browser, scope)
            assert on_dashboard[:2] == on_financial[:2] == (responded, pledges), scope
            figures[scope] = on_dashboard
    assert figures["historical"][2] - figures["current"][2] == 1
    with campaign_clock(now):
        listed = page(harness)
    assert {row["family_duid"] for row in listed["rows"]} == {1}
    assert listed["total"] == figures["current"][2] == 1
