"""The fake ParishSoft's multi-organization mode (#1554), in process on loopback.

The fixed key then also reaches two other organizations, and the searches and
lists answer with some of their records. A full load must store exactly what
it stores for a key that reaches only the synthetic organization, and record
how many records it left out. Each other organization also answers its own
requests: the first as a small parish of its own, which a full load reads
whole, the second with nothing at all.
"""

import json
import logging
import threading
from datetime import date
from uuid import uuid4

import pytest
import requests

from parishkit.parishsoft import ParishSoftClient, ParishSoftConfig
from parishkit.parishsoft_source import organization_name
from parishkit.stewardship.local import LOCAL_ORGANIZATION_ID
from parishkit.stewardship.local.fake_parishsoft import (
    FakeParishSoft,
    base_url,
    start_server,
)
from parishkit.stewardship.observability import SafeJsonFormatter
from parishkit.stewardship.provider_check_worker import _holds_families
from parishkit.stewardship.source.canonical import SourceReferenceSkew
from parishkit.stewardship.source.loading import (
    ORGANIZATION_KEY,
    ORGANIZATIONS_KEY,
    DestructiveSourceChange,
    load_full_source,
    log_organizations,
    organization_evidence,
    recorded_organizations,
)
from parishkit.stewardship.source.windows import GivingPeriod, RefreshWindow
from parishkit.stewardship.source.workgroups import load_reminder_workgroup

from .test_fake_parishsoft import ANCHOR, KEY, client, configuration, get, post

# The other organizations: the first holds Families, the second nothing.
HOLDING = LOCAL_ORGANIZATION_ID + 1
EMPTY = LOCAL_ORGANIZATION_ID + 2


def serve(fake):
    """Serve ``fake`` on loopback; returns (server, stop)."""
    server = start_server(fake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        """Shut the server down and wait for its thread."""
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return server, stop


@pytest.fixture(scope="module")
def servers():
    """The same synthetic parish behind a one- and a three-organization key."""
    single, stop_single = serve(FakeParishSoft(configuration()))
    multi, stop_multi = serve(FakeParishSoft(configuration(other_organizations=2)))
    try:
        yield single, multi
    finally:
        stop_single()
        stop_multi()


def window(fake):
    """A giving window over every pledge fund, so giving is read too."""
    funds = tuple(
        sorted(f["fundId"] for f in fake.parish.funds if f["requiresPledges"])
    )
    return RefreshWindow(
        uuid4(), (GivingPeriod(date(2026, 1, 1), date(2026, 12, 31), funds),)
    )


def test_the_key_reaches_three_organizations(servers):
    """organizations/search lists the synthetic organization and two others."""
    _, multi = servers
    rows = requests.post(
        base_url(multi) + "/organizations/search", json={}, headers=KEY, timeout=5
    ).json()
    assert [row["organizationID"] for row in rows] == [
        LOCAL_ORGANIZATION_ID,
        LOCAL_ORGANIZATION_ID + 1,
        LOCAL_ORGANIZATION_ID + 2,
    ]


def test_a_full_load_stores_only_the_configured_organization(servers, tmp_path, caplog):
    """Same corpus as a one-organization key; the left-out records are counted."""
    single, multi = servers
    fake = multi.fake
    one = load_full_source(client(single, tmp_path), window=window(fake), as_of=ANCHOR)
    several_client = client(multi, tmp_path)
    several = load_full_source(several_client, window=window(fake), as_of=ANCHOR)
    assert several.corpus == one.corpus
    assert several.counts == one.counts
    assert ORGANIZATIONS_KEY not in one.evidence
    recorded = several.evidence[ORGANIZATIONS_KEY]
    assert recorded["reached"] == 3
    skipped = recorded["skipped"]
    assert skipped["members"] == 4
    assert skipped["member_contactinfos"] == 4
    assert skipped["ministry_types"] == 2
    assert skipped["pledges"] > 0 and skipped["contributions"] > 0
    # The other Members' Family is not loaded, so none is left without Members.
    assert recorded["families_without_members"] == 0
    assert recorded_organizations({"load": several.evidence}) == recorded
    # The loader logs exactly what it records, per collection, with its task.
    task = uuid4()
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        log_organizations(several.evidence, task_id=task)
        log_organizations(one.evidence, task_id=task)
    logged = [
        json.loads(SafeJsonFormatter().format(record))["extra"]
        for record in caplog.records
        if record.name == "parishkit.stewardship"
    ]
    assert [entry.get("source_organizations") for entry in logged] == [recorded]
    assert logged[0]["task_id"] == str(task)
    # Every scoped request still names the synthetic organization alone.
    assert several_client.organizations_reached == 3
    others = {LOCAL_ORGANIZATION_ID + 1, LOCAL_ORGANIZATION_ID + 2}
    assert not any(
        row.get("organizationID") in others for row in several.corpus["member"].values()
    )


def test_a_full_load_records_the_organizations_name(servers, tmp_path):
    """The configured organization's cleaned name, whatever else it reaches."""
    single, multi = servers
    fake = multi.fake
    name = organization_name(fake.parish.organization["organizationReportName"])
    assert name
    for server in (single, multi):
        loaded = load_full_source(
            client(server, tmp_path), window=window(fake), as_of=ANCHOR
        )
        assert loaded.evidence[ORGANIZATION_KEY] == {"name": name}


def test_other_organizations_answer_the_lookups_family_search(servers, tmp_path):
    """The first other organization holds Families, the second none."""
    single, multi = servers
    plain = ParishSoftClient(
        ParishSoftConfig(
            KEY["x-api-key"],
            tmp_path,
            cache_enabled=False,
            api_base_url=base_url(multi),
            timeout=5,
        )
    )
    assert _holds_families(plain, LOCAL_ORGANIZATION_ID) is True
    assert _holds_families(plain, LOCAL_ORGANIZATION_ID + 1) is True
    assert _holds_families(plain, LOCAL_ORGANIZATION_ID + 2) is False
    # A one-organization key still answers 404 for any other organization.
    response = requests.post(
        base_url(single) + "/families/search",
        json={
            "organizationIDs": [LOCAL_ORGANIZATION_ID + 1],
            "pageSize": 1,
            "pageNumber": 1,
        },
        headers=KEY,
        timeout=5,
    )
    assert response.status_code == 404


def test_the_reminder_workgroup_list_keeps_only_the_parishs(servers, tmp_path):
    """Another organization's WorkGroup is never found, the parish's still is."""
    _, multi = servers
    fake = multi.fake
    coherent = client(multi, tmp_path)
    coherent.validate_organization()
    other = fake.others.family_workgroups[0]["workgroupName"]
    assert load_reminder_workgroup(coherent, other)["found"] is False
    ours = fake.parish.family_workgroups[1]
    families = {str(row["familyDUID"]) for row in fake.parish.families}
    found = load_reminder_workgroup(coherent, ours["workgroupName"], families=families)
    assert found["found"] is True and found["family_duids"]
    # Two reads of the list, each leaving out every other organization's
    # WorkGroups: the leaked rows and the first other organization's own.
    others = len(fake.others.family_workgroups) + len(
        fake.others.tenants[HOLDING].family_workgroups
    )
    assert organization_evidence(coherent)[ORGANIZATIONS_KEY]["skipped"] == {
        "family_workgroups": 2 * others
    }
    # A membership Family the full load does not hold fails the refresh.
    with pytest.raises(SourceReferenceSkew):
        load_reminder_workgroup(coherent, ours["workgroupName"], families=set())


def test_the_one_organization_mode_is_the_default(servers, tmp_path):
    """Without the setting the fake answers for the synthetic organization only."""
    single, _ = servers
    coherent = client(single, tmp_path)
    coherent.validate_organization()
    assert coherent.organizations_reached == 1
    assert organization_evidence(coherent) == {}
    # The WorkGroup cross-check is for a multi-organization key only: one
    # organization's refresh is read exactly as before, whatever it holds.
    fake = single.fake
    ours = fake.parish.family_workgroups[1]["workgroupName"]
    assert load_reminder_workgroup(coherent, ours, families=set())["found"] is True


@pytest.mark.parametrize("value", [-1, 3, "2", True])
def test_other_organizations_setting_is_bounded(value):
    """Only 0, 1 or 2 other organizations; anything else is a bad file."""
    with pytest.raises(ValueError):
        configuration(other_organizations=value)


def test_the_organizations_command_sets_the_mode_and_nothing_else():
    """``fake-parishsoft organizations N`` changes only the setting (#1554)."""
    from parishkit.stewardship.local.fake_edits import EditError, edit_document

    generated = {
        "seed": 1,
        "families": 100,
        "anchor_date": ANCHOR.isoformat(),
        "release_at": None,
    }
    document = edit_document(["family", "100003", "no_mail=true"], generated)
    several = edit_document(["organizations", "2"], document)
    assert several == document | {"other_organizations": 2}
    # undo and clear are about the parish's edits, never this setting.
    assert edit_document(["clear"], several) == {
        key: value for key, value in several.items() if key != "edits"
    }
    assert edit_document(["organizations", "0"], several) == document
    for words in (["organizations"], ["organizations", "3"], ["organizations", "x"]):
        with pytest.raises(EditError):
            edit_document(words, document)


def every_endpoint(server, organization):
    """Each scoped collection the loads read for ``organization``, by name."""
    scoped = {"organizationIDs": [organization]}
    return {
        "families": post(server, "families/search", scoped),
        "members": post(server, "members/search", scoped),
        "contacts": post(server, "members/contact/list", scoped),
        "ministries": get(server, "ministry/type/list", organizationId=organization),
        "member_workgroups": get(
            server, "members/workgroup/lookup/list", organizationId=organization
        ),
        "funds": get(server, f"offering/{organization}/funds"),
        "pledges": get(server, "offering/pledge/list", OrganizationID=organization),
        "contributions": get(
            server, "offering/contributiondetail/list", OrganizationId=organization
        ),
    }


def rows(response):
    """A 200 answer's rows, bare or in the published envelope."""
    assert response.status_code == 200
    body = response.json()
    return body["data"] if isinstance(body, dict) else body


def test_the_first_other_organization_answers_as_a_parish_of_its_own(servers):
    """Every endpoint a load reads answers for it, consistently: its Members'
    Families are its own, its rosters and by-ID reads resolve, every row
    names it, and no identity is the synthetic parish's (pk-local R51)."""
    _, multi = servers
    fake = multi.fake
    answers = {
        name: rows(item) for name, item in every_endpoint(multi, HOLDING).items()
    }
    assert all(answers[name] for name in answers), answers
    families = {row["familyDUID"] for row in answers["families"]}
    assert families == {
        row["familyDUID"] for row in fake.others.tenants[HOLDING].families
    }
    members = answers["members"]
    assert {row["familyDUID"] for row in members} <= families
    assert {row["organizationID"] for row in members} == {HOLDING}
    assert {row["ownerOrganizationID"] for row in answers["contacts"]} == {HOLDING}
    assert {row["memberDUID"] for row in answers["contacts"]} <= {
        row["memberDUID"] for row in members
    }
    assert {row["organizationId"] for row in answers["ministries"]} == {HOLDING}
    funds = {row["fundId"] for row in answers["funds"]}
    assert {row["fundID"] for row in answers["pledges"]} <= funds
    assert {row["familyID"] for row in answers["pledges"]} <= families
    assert {row["organizationID"] for row in answers["pledges"]} == {HOLDING}
    assert {row["organizationId"] for row in answers["contributions"]} == {HOLDING}
    # Rosters and Members are read by identity, with no organization.
    ministry = answers["ministries"][0]["id"]
    roster = rows(get(multi, f"ministry/{ministry}/minister/list"))
    assert {row["memberId"] for row in roster} <= {row["memberDUID"] for row in members}
    workgroup = answers["member_workgroups"][0]["id"]
    assert rows(get(multi, f"members/workgroup/{workgroup}/list")) is not None
    # The Family WorkGroup list names no organization: it carries the
    # organization's own WorkGroups, whose rosters hold its Families.
    own = fake.others.tenants[HOLDING].family_workgroups[0]
    listed = rows(get(multi, "families/workgroup/list"))
    assert own["workgroupDUID"] in {row["workgroupDUID"] for row in listed}
    roster = rows(get(multi, f"families/workgroup/{own['workgroupDUID']}/list"))
    assert roster and {row["familyId"] for row in roster} <= families
    member = get(multi, f"members/{members[0]['memberDUID']}")
    assert member.status_code == 200 and member.json()["familyDUID"] in families
    # No identity is shared with the synthetic parish.
    parish = fake.parish
    assert not families & {row["familyDUID"] for row in parish.families}
    assert not {row["memberDUID"] for row in members} & {
        row["memberDUID"] for row in parish.members
    }
    assert not funds & {row["fundId"] for row in parish.funds}
    # The synthetic organization's own answers are as before.
    assert rows(post(multi, "families/search", {"organizationIDs": [1001]})) == rows(
        post(servers[0], "families/search", {"organizationIDs": [1001]})
    )


def test_the_second_other_organization_holds_nothing_anywhere(servers):
    """Every scoped endpoint answers it with an empty collection, never a 404."""
    _, multi = servers
    for name, response in every_endpoint(multi, EMPTY).items():
        assert rows(response) == [], name


def test_a_full_load_of_the_first_other_organization_succeeds(servers, tmp_path):
    """Chosen in setup, it loads whole, named, without the parish's records."""
    _, multi = servers
    tenant = multi.fake.others.tenants[HOLDING]
    funds = tuple(sorted(f["fundId"] for f in tenant.funds if f["requiresPledges"]))
    loaded = load_full_source(
        client(multi, tmp_path, organization=HOLDING),
        window=RefreshWindow(
            uuid4(), (GivingPeriod(date(2026, 1, 1), date(2026, 12, 31), funds),)
        ),
        as_of=ANCHOR,
    )
    assert loaded.counts["family"] == len(tenant.families)
    assert loaded.counts["member"] == len(tenant.members)
    assert loaded.counts["ministry"] == len(tenant.ministries)
    assert loaded.counts["fund"] == len(tenant.funds)
    assert loaded.counts["pledge"] > 0 and loaded.counts["contribution"] > 0
    assert loaded.evidence[ORGANIZATION_KEY] == {"name": "Neighboring Parish 1"}
    assert {row["familyDUID"] for row in tenant.families} >= {
        int(key) for key in loaded.corpus["family"]
    }


def test_a_full_load_of_the_empty_organization_is_refused(servers, tmp_path):
    """An organization holding nothing fails the first load's empty check."""
    _, multi = servers
    with pytest.raises(DestructiveSourceChange):
        load_full_source(
            client(multi, tmp_path, organization=EMPTY),
            window=RefreshWindow(None, ()),
            as_of=ANCHOR,
        )
