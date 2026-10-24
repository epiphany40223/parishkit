"""The fake ParishSoft's multi-organization mode (#1554), in process on loopback.

The fixed key then also reaches two other organizations, and the searches and
lists answer with some of their records. A full load must store exactly what
it stores for a key that reaches only the synthetic organization, and record
how many records it left out.
"""

import json
import logging
import threading
from datetime import date
from uuid import uuid4

import pytest
import requests

from parishkit.stewardship.local import LOCAL_ORGANIZATION_ID
from parishkit.stewardship.local.fake_parishsoft import (
    FakeParishSoft,
    base_url,
    start_server,
)
from parishkit.stewardship.observability import SafeJsonFormatter
from parishkit.stewardship.source.canonical import SourceReferenceSkew
from parishkit.stewardship.source.loading import (
    ORGANIZATIONS_KEY,
    load_full_source,
    organization_evidence,
    recorded_organizations,
)
from parishkit.stewardship.source.windows import GivingPeriod, RefreshWindow
from parishkit.stewardship.source.workgroups import load_reminder_workgroup

from .test_fake_parishsoft import ANCHOR, KEY, client, configuration


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
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
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
    assert recorded_organizations({"load": several.evidence}) == recorded
    # One log line with counts only; the other Members' Family is not loaded,
    # so no loaded Family is left without Members.
    logged = [
        json.loads(SafeJsonFormatter().format(record))["extra"]
        for record in caplog.records
        if record.name == "parishkit.stewardship"
    ]
    assert {
        "reached": 3,
        "skipped": sum(skipped.values()),
        "families_without_members": 0,
    } in [entry.get("source_organizations") for entry in logged]
    # Every scoped request still names the synthetic organization alone.
    assert several_client.organizations_reached == 3
    others = {LOCAL_ORGANIZATION_ID + 1, LOCAL_ORGANIZATION_ID + 2}
    assert not any(
        row.get("organizationID") in others for row in several.corpus["member"].values()
    )


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
    assert organization_evidence(coherent)[ORGANIZATIONS_KEY]["skipped"] == {
        "family_workgroups": 4
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
