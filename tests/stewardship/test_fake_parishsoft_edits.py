"""Operator edits to the fake ParishSoft's synthetic parish (#1780).

The edits live in the fake's configuration file and are replayed when it
starts; ``fake-parishsoft-edit`` checks each one against the generated parish
before it is recorded. These tests read the edited data back through the
fake's own request handler, as the source loaders see it.
"""

import io
import json
from datetime import date

import pytest

from parishkit.stewardship.cli import main
from parishkit.stewardship.local import (
    LOCAL_ORGANIZATION_ID,
    LOCAL_PARISHSOFT_KEY,
    fake_edits,
)
from parishkit.stewardship.local.fake_edits import EditError, edit_document, execute
from parishkit.stewardship.local.fake_parishsoft import (
    BASE_PATH,
    FakeConfiguration,
    FakeParishSoft,
)
from parishkit.stewardship.local.synthetic_parish import digest, generate

ANCHOR = date(2026, 9, 16)
DOCUMENT = {
    "seed": 1,
    "families": 20,
    "anchor_date": ANCHOR.isoformat(),
    "release_at": None,
}
KEY = {"x-api-key": LOCAL_PARISHSOFT_KEY}


def edited(*commands, document=None):
    """The configuration document after each command's edit, in order."""
    document = dict(document or DOCUMENT)
    for words in commands:
        document = edit_document(list(words), document)
    return document


def fake_for(document):
    """The fake the edited document starts."""
    return FakeParishSoft(FakeConfiguration.parse(document))


def answer(fake, method, endpoint, body=None):
    """One request through the fake's handler; the decoded 200 answer."""
    status, content = fake.handle(method, BASE_PATH + endpoint, body, KEY)
    assert status == 200, (endpoint, status)
    return json.loads(content)


def members(fake):
    """Every served Member search row."""
    return answer(
        fake,
        "POST",
        "members/search",
        {"organizationIDs": [LOCAL_ORGANIZATION_ID], "maximumRows": 500},
    )


def contacts(fake):
    """Every served contact-list row."""
    return answer(
        fake,
        "POST",
        "members/contact/list",
        {"organizationIDs": [LOCAL_ORGANIZATION_ID], "limit": 500},
    )


def roster(fake, ministry):
    """One Ministry's served roster rows."""
    return answer(fake, "GET", f"ministry/{ministry}/minister/list")["data"]


def pledges(fake, fund):
    """One fund's served pledge rows."""
    query = f"OrganizationID={LOCAL_ORGANIZATION_ID}&FundID={fund}"
    return answer(fake, "GET", f"offering/pledge/list?{query}")["data"]


@pytest.fixture(scope="module")
def parish():
    """The unedited parish the documents above generate."""
    return generate(1, 20, ANCHOR)


def adult(parish, index=0):
    """An adult head of a Family with at least two Members."""
    sizes = {f["familyDUID"]: f["hasMembers"] for f in parish.families}
    heads = [
        m
        for m in parish.members
        if m["memberType"] in ("Husband", "Wife", "Head")
        and sizes[m["familyDUID"]] >= 2
        and m["familyDUID"] != parish.late_family_id
    ]
    return heads[index]


def test_no_edits_serve_the_generated_parish_unchanged(parish):
    """An unedited file, or an empty list, changes nothing the fake serves."""
    plain = fake_for(DOCUMENT)
    empty = fake_for({**DOCUMENT, "edits": []})
    assert digest(plain.parish) == digest(empty.parish) == digest(parish)
    # Re-deriving an unedited parish reproduces the generator's copies.
    again = generate(1, 20, ANCHOR)
    fake_edits.rederive(again)
    assert digest(again) == digest(parish)


def test_a_member_edit_reaches_the_search_contact_and_household_reads(parish):
    """Email, mobile phone and suffix change on every read that carries them."""
    member = adult(parish)
    duid = member["memberDUID"]
    fake = fake_for(
        edited(
            (
                "member",
                str(duid),
                "email=new@example.test",
                "mobile=555-000-1234",
                "suffix=Jr.",
                "middle=",
            )
        )
    )
    row = next(m for m in members(fake) if m["memberDUID"] == duid)
    assert row["emailAddress"] == "new@example.test"
    assert row["mobilePhone"] == "555-000-1234"
    assert row["suffix"] == "Jr." and row["middleName"] is None
    contact = next(c for c in contacts(fake) if c["memberDUID"] == duid)
    assert contact["emailAddress"] == "new@example.test"
    assert contact["cellPhone"] == "555-000-1234"
    household = answer(fake, "GET", f"families/{member['familyDUID']}/member/list")
    served = next(m for m in household if m["memberDUID"] == duid)
    assert served["cellPhone"] == "555-000-1234" and served["suffix"] == "Jr."
    assert answer(fake, "GET", f"members/{duid}")["emailAddress"] == "new@example.test"


def test_a_death_date_is_a_parishsoft_timestamp(parish):
    """Dates are given as YYYY-MM-DD and served as ParishSoft's midnight form."""
    duid = adult(parish)["memberDUID"]
    fake = fake_for(
        edited(("member", str(duid), "death=2026-09-01", "status=Deceased"))
    )
    row = answer(fake, "GET", f"members/{duid}")
    assert row["dateOfDeath"] == "2026-09-01T00:00:00"
    assert row["memberStatus"] == "Deceased"
    contact = next(c for c in contacts(fake) if c["memberDUID"] == duid)
    assert contact["dateOfDeath"] == "2026-09-01T00:00:00"


def test_moving_a_member_updates_both_families_and_the_rosters(parish):
    """family=ID moves the Member; counts, household lists and rosters follow."""
    member = adult(parish)
    old = member["familyDUID"]
    new = next(
        f["familyDUID"]
        for f in parish.families
        if f["familyDUID"] not in (old, parish.late_family_id)
    )
    ministry = parish.ministries[0]["id"]
    fake = fake_for(
        edited(
            ("roster", "add", str(ministry), str(member["memberDUID"])),
            ("member", str(member["memberDUID"]), f"family={new}"),
        )
    )
    sizes = {f["familyDUID"]: f["hasMembers"] for f in parish.families}
    assert answer(fake, "GET", f"families/{old}")["hasMembers"] == sizes[old] - 1
    assert answer(fake, "GET", f"families/{new}")["hasMembers"] == sizes[new] + 1
    moved = [
        m["memberDUID"] for m in answer(fake, "GET", f"families/{new}/member/list")
    ]
    assert member["memberDUID"] in moved
    rows = [r for r in roster(fake, ministry) if r["memberId"] == member["memberDUID"]]
    assert [r["familyId"] for r in rows] == [new]


def test_roster_add_and_remove(parish):
    """add puts an active entry on the Ministry; remove takes every entry off."""
    ministry = parish.ministries[1]["id"]
    on = {r["memberId"] for r in parish.ministry_rosters[ministry]}
    member = next(
        m["memberDUID"]
        for m in parish.members
        if m["memberDUID"] not in on and m["familyDUID"] != parish.late_family_id
    )
    added = edited(
        ("roster", "add", str(ministry), str(member), "role=Staff", "start=2026-09-01")
    )
    rows = [r for r in roster(fake_for(added), ministry) if r["memberId"] == member]
    assert len(rows) == 1
    assert rows[0]["ministryRoleName"] == "Staff" and rows[0]["ministryRoleId"] == 3
    assert rows[0]["startDate"] == "2026-09-01T00:00:00" and rows[0]["endDate"] is None
    served = [r["memberId"] for r in roster(fake_for(added), ministry)]
    assert served == sorted(served)
    with pytest.raises(EditError, match="already on Ministry"):
        edited(("roster", "add", str(ministry), str(member)), document=added)
    removed = edited(("roster", "remove", str(ministry), str(member)), document=added)
    assert member not in {r["memberId"] for r in roster(fake_for(removed), ministry)}
    with pytest.raises(EditError, match="is not on Ministry"):
        edited(("roster", "remove", str(ministry), str(member)), document=removed)


def test_a_pledge_is_set_replaced_and_removed(parish):
    """The default fund and period; a second set keeps the pledge's identity."""
    family = parish.families[0]["familyDUID"]
    fund = fake_edits.default_fund(parish)
    start = f"{ANCHOR.year + 1}-01-01T00:00:00"
    first = edited(("pledge", str(family), "1200"))
    rows = [
        p
        for p in pledges(fake_for(first), fund)
        if p["familyID"] == family and p["pledgeStartDate"] == start
    ]
    assert [p["currentPledgeAmount"] for p in rows] == [1200]
    identity = rows[0]["pledgeID"]
    assert identity > max(p["pledgeID"] for p in parish.pledges)
    second = edited(("pledge", str(family), "1500.50"), document=first)
    rows = [
        p
        for p in pledges(fake_for(second), fund)
        if p["familyID"] == family and p["pledgeStartDate"] == start
    ]
    assert [(p["pledgeID"], p["currentPledgeAmount"]) for p in rows] == [
        (identity, 1500.5)
    ]
    gone = edited(("pledge", str(family), "none"), document=second)
    assert not [
        p
        for p in pledges(fake_for(gone), fund)
        if p["familyID"] == family and p["pledgeStartDate"] == start
    ]
    other = parish.funds[1]["fundId"]
    elsewhere = edited(
        ("pledge", str(family), "300", f"fund={other}", "start=2026-01-01")
    )
    assert any(
        p["familyID"] == family and p["currentPledgeAmount"] == 300
        for p in pledges(fake_for(elsewhere), other)
    )


@pytest.mark.parametrize(
    ("words", "message"),
    [
        (("member", "1", "email=x@example.test"), "no Member 1"),
        (("member", "500001", "bogus=1"), "no editable field 'bogus'"),
        (("member", "500001", "memberDUID=7"), "no editable field 'memberDUID'"),
        (("member", "500001", "dateModified=x"), "no editable field"),
        (("family", "100001", "hasMembers=9"), "no editable field"),
        (("family", "100001", "group=7"), "one of the parish's groups"),
        (("family", "100001", "envelope=-1"), "whole number"),
        (("family", "100001", "status="), "cannot be empty"),
        (("member", "500001"), "at least one field=value"),
        (("member", "500001", "death=1 Oct"), "YYYY-MM-DD"),
        (("member", "500001", "family="), "familyDUID cannot be empty"),
        (("member", "500001", "family=1"), "no Family 1"),
        (("family", "100001", "status=maybe"), "true or false"),
        (("family", "100001", "familyDUID=100002"), "no editable field"),
        (("roster", "add", "1", "500001"), "no Ministry 1"),
        (("roster", "add", "9000", "500001", "role=Boss"), "role must be one of"),
        (("pledge", "100001", "-5"), "above zero"),
        (("pledge", "100001", "lots"), "a number, or none"),
        (("pledge", "100001", "5", "fund=1"), "no fund 1"),
        (("pledge", "100001", "none"), "has no pledge"),
        (("member", "500001", "email=" + "x" * 201), "printable"),
        (("frobnicate", "1"), "usage"),
    ],
)
def test_bad_edits_are_refused_before_they_are_recorded(words, message):
    """Each refusal is plain language; nothing is added to the document."""
    with pytest.raises(EditError, match=message):
        edit_document(list(words), dict(DOCUMENT))


def test_a_bad_recorded_edit_stops_the_fake_from_starting():
    """A hand-edited file the parish cannot take is a ValueError at start."""
    for edits in (
        [{"kind": "member", "id": 1, "set": {"emailAddress": "x"}}],
        [{"kind": "member", "id": 500001}],
        [{"kind": "nonsense"}],
        "not a list",
    ):
        with pytest.raises(ValueError):
            fake_for({**DOCUMENT, "edits": edits})


def run(words, document):
    """The command line with a document on stdin; (exit, stdout, stderr)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    status = execute(words, io.StringIO(json.dumps(document)), stdout, stderr)
    return status, stdout.getvalue(), stderr.getvalue()


def test_the_command_prints_the_new_document_and_shows_records(parish):
    """Mutations print the new document; show reports and changes nothing."""
    duid = adult(parish)["memberDUID"]
    status, out, err = run(["member", str(duid), "email=new@example.test"], DOCUMENT)
    assert status == 0 and "1 fake ParishSoft edit(s) recorded." in err
    document = json.loads(out)
    assert document["edits"] == [
        {"kind": "member", "id": duid, "set": {"emailAddress": "new@example.test"}}
    ]
    status, out, _ = run(["show"], document)
    assert status == 0 and out.startswith("1. ") and "new@example.test" in out
    status, out, _ = run(["show", "member", str(duid)], document)
    assert status == 0 and json.loads(out)["emailAddress"] == "new@example.test"
    family = parish.families[0]["familyDUID"]
    status, out, _ = run(["show", "family", str(family)], document)
    assert status == 0 and json.loads(out)["members"]
    ministry = parish.ministries[0]["id"]
    status, out, _ = run(["show", "ministry", str(ministry)], document)
    assert status == 0 and json.loads(out)["roster"]
    status, out, _ = run(["show"], DOCUMENT)
    assert status == 0 and "No fake ParishSoft edits" in out
    # undo and clear drop edits; an empty list leaves the file as `up` wrote it.
    status, out, _ = run(["undo"], document)
    assert status == 0 and json.loads(out) == DOCUMENT
    status, out, _ = run(["clear"], document)
    assert status == 0 and json.loads(out) == DOCUMENT
    status, _, err = run(["undo"], DOCUMENT)
    assert status == 2 and "no edit to undo" in err


def test_the_command_refuses_with_exit_two_and_prints_no_document():
    """A refused edit or unreadable input prints only the message."""
    status, out, err = run(["member", "1", "email=x@example.test"], DOCUMENT)
    assert (status, out) == (2, "") and err.startswith("ERROR: There is no Member 1")
    stdout, stderr = io.StringIO(), io.StringIO()
    assert execute(["show"], io.StringIO("not json"), stdout, stderr) == 2
    assert "unreadable or invalid" in stderr.getvalue() and stdout.getvalue() == ""
    status, _, err = run(["show"], {"seed": 1})
    assert status == 2 and "unreadable or invalid" in err
    assert execute([], io.StringIO(""), io.StringIO(), stderr) == 2


def test_pk_stewardship_hands_the_words_to_the_edit_command(monkeypatch, capsys):
    """``pk-stewardship fake-parishsoft-edit`` bypasses the flat option parser."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(DOCUMENT)))
    assert main(["fake-parishsoft-edit", "show"]) == 0
    assert "No fake ParishSoft edits" in capsys.readouterr().out


def test_the_refresh_step_refuses_a_deployment_reading_the_real_parishsoft(
    monkeypatch,
):
    """``fake-parishsoft refresh`` never reaches the real API: refused first."""
    from types import SimpleNamespace

    from parishkit.stewardship.local import seeder

    configured = []
    monkeypatch.setattr(seeder, "configure_web_process", configured.append)
    real = SimpleNamespace(
        parishsoft_base_url="https://ps-fs-external-api-prod.azurewebsites.net/api/v2"
    )
    with pytest.raises(seeder.SeedRefused, match="does not read the fake ParishSoft"):
        seeder.refresh_step(None, real)
    assert configured == []


def late_family(parish):
    """The held-back late-added Family and one of its Members."""
    member = next(m for m in parish.members if m["familyDUID"] == parish.late_family_id)
    return parish.late_family_id, member["memberDUID"]


def test_edits_naming_the_late_added_family_are_refused(parish):
    """The fake hides that Family until seed, so nothing may hang rows on it."""
    family, member = late_family(parish)
    ministry = parish.ministries[0]["id"]
    other = adult(parish)["memberDUID"]
    for words in (
        ("pledge", str(family), "500"),
        ("family", str(family), "city=Elsewhere"),
        ("member", str(member), "suffix=Jr."),
        ("roster", "add", str(ministry), str(member)),
        ("member", str(other), f"family={family}"),
    ):
        with pytest.raises(EditError, match="late-added Family"):
            edit_document(list(words), dict(DOCUMENT))


def test_a_roster_add_cannot_repeat_an_ended_entrys_identity(parish):
    """Same Member, Ministry, role and start as an existing entry is refused."""
    ministry, row = next(
        (identifier, row)
        for identifier, rows in parish.ministry_rosters.items()
        for row in rows
        if row["endDate"] is not None
    )
    with pytest.raises(EditError, match="give another start="):
        edit_document(
            [
                "roster",
                "add",
                str(ministry),
                str(row["memberId"]),
                f"role={row['ministryRoleName']}",
                f"start={row['startDate'][:10]}",
            ],
            dict(DOCUMENT),
        )


def test_a_hand_edited_value_of_the_wrong_type_stops_the_fake(parish):
    """Replay re-checks every stored value, as the command checked it."""
    duid = adult(parish)["memberDUID"]
    family = parish.families[0]["familyDUID"]
    for edit in (
        {"kind": "member", "id": duid, "set": {"emailAddress": ["x"]}},
        {"kind": "member", "id": duid, "set": {"birthdate": "2020-01-01"}},
        {"kind": "family", "id": family, "set": {"famGroupID": 99}},
        {"kind": "family", "id": family, "set": {"registeredOrganizationID": "5"}},
        {"kind": "family", "id": family, "set": {"sendNoMail": None}},
    ):
        with pytest.raises(ValueError):
            fake_for({**DOCUMENT, "edits": [edit]})


def test_the_default_pledge_fund_is_the_one_create_the_campaign_offers_first(parish):
    """The first active fund by name, as the campaign form sorts them."""
    by_name = sorted((f["name"], f["fundId"]) for f in parish.funds if f["active"])
    assert fake_edits.default_fund(parish) == by_name[0][1]
    family = parish.families[0]["familyDUID"]
    (edit,) = edited(("pledge", str(family), "100"))["edits"]
    assert "fund" not in edit  # resolved at replay, from the same parish


def test_every_editable_field_passes_the_strict_loaders(tmp_path, parish):
    """An edit of each editable field, and a pledge in the campaign's period,
    load through the real full-source loaders against the served fake."""
    import threading
    import uuid

    from parishkit.stewardship.local.fake_parishsoft import start_server
    from parishkit.stewardship.source.loading import load_full_source
    from parishkit.stewardship.source.windows import GivingPeriod, RefreshWindow

    from .test_fake_parishsoft import client

    member = adult(parish)
    family = next(
        f["familyDUID"]
        for f in parish.families
        if f["familyDUID"] not in (member["familyDUID"], parish.late_family_id)
    )
    values = {
        fake_edits.TEXT: "Edited",
        fake_edits.DATE: "2026-09-01",
        fake_edits.WHOLE: "3",
        fake_edits.COUNT: "0",
        fake_edits.FLAG: "true",
        fake_edits.GROUP: "2",
        fake_edits.FAMILY: str(family),
    }
    words = [
        f"{field}={values[kind]}"
        for field, kind in fake_edits.MEMBER_FIELDS.items()
        if field != "emailAddress"
    ]
    family_words = [
        f"{field}={values[kind]}"
        for field, kind in fake_edits.FAMILY_FIELDS.items()
        if field not in ("eMailAddress", "registeredOrganizationID")
    ]
    fund = fake_edits.default_fund(parish)
    document = edited(
        ("member", str(member["memberDUID"]), *words, "email=e@example.test"),
        (
            "family",
            str(family),
            *family_words,
            "email=f@example.test",
            f"registeredOrganizationID={LOCAL_ORGANIZATION_ID}",
        ),
        ("pledge", str(family), "1234.5"),
    )
    fake = fake_for(document)
    server = start_server(fake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        year = ANCHOR.year + 1
        period = GivingPeriod(date(year, 1, 1), date(year, 12, 31), (fund,))
        result = load_full_source(
            client(server, tmp_path),
            window=RefreshWindow(uuid.uuid4(), (period,)),
            as_of=ANCHOR,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    key = str(member["memberDUID"])
    assert result.corpus["member"][key]["family_key"] == str(family)
    assert result.corpus["member"][key]["suffix"] == "Edited"
    pledged = [
        row
        for row in result.corpus["pledge"].values()
        if row["family_key"] == str(family)
    ]
    assert [row["amount"] for row in pledged] == ["1234.50"]


def test_the_refresh_step_refuses_when_the_settings_name_another_parishsoft(
    monkeypatch,
):
    """The second check: the configured process must read the fake too."""
    from types import SimpleNamespace

    from parishkit.stewardship import deployment
    from parishkit.stewardship.local import seeder

    monkeypatch.setattr(seeder, "configure_web_process", lambda configuration: None)
    monkeypatch.setattr(
        deployment, "recorded_parishsoft_base_url", lambda: "https://elsewhere"
    )
    monkeypatch.setattr(seeder, "admin", lambda request: pytest.fail("signed in"))
    with pytest.raises(seeder.SeedRefused, match="does not read the fake ParishSoft"):
        seeder.refresh_step(None, SimpleNamespace(parishsoft_base_url=None))
