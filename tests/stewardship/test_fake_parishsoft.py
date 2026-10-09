"""The fake ParishSoft service satisfies the strict loaders, in process on loopback."""

import json
import threading
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests
import yaml

from parishkit.parishsoft import ParishSoftAPIError, ParishSoftConfig
from parishkit.parishsoft_changes import load_family_changes
from parishkit.parishsoft_households import load_family_slice
from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.parishsoft_transport import ExactSourceResponse
from parishkit.stewardship.cli import main
from parishkit.stewardship.local import LOCAL_ORGANIZATION_ID, LOCAL_PARISHSOFT_KEY
from parishkit.stewardship.local import fake_parishsoft as module
from parishkit.stewardship.local.fake_parishsoft import (
    BASE_PATH,
    FakeConfiguration,
    FakeParishSoft,
    base_url,
    start_server,
)
from parishkit.stewardship.local.synthetic_parish import generate, is_child
from parishkit.stewardship.source.loading import load_full_source
from parishkit.stewardship.source.windows import GivingPeriod, RefreshWindow

ANCHOR = date(2026, 9, 16)
RELEASE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
KEY = {"x-api-key": LOCAL_PARISHSOFT_KEY}


def configuration(**changes):
    """The file ``up`` writes, as a parsed configuration."""
    return FakeConfiguration.parse(
        {
            "seed": 1,
            "families": 100,
            "anchor_date": ANCHOR.isoformat(),
            "release_at": None,
        }
        | changes
    )


class ExactSession(requests.Session):
    """A real HTTP Session whose responses decode like the bounded transport's.

    The application's transport parses numbers as Decimal; ordinary requests
    responses would turn the fake's amounts into floats and fail the giving
    loader's exactness check for the wrong reason.
    """

    def request(self, method, url, **kwargs):
        upstream = super().request(method, url, **kwargs)
        response = ExactSourceResponse()
        response.status_code, response.url, response.encoding = (
            upstream.status_code,
            url,
            "utf-8",
        )
        response._content = upstream.content
        response._content_consumed = True
        return response


@pytest.fixture(scope="module")
def served():
    """One fake, released-Family clock injectable, served on 127.0.0.1:0."""
    clock = [RELEASE - timedelta(days=1)]
    fake = FakeParishSoft(
        configuration(release_at=RELEASE.isoformat(), change_feed="synthetic"),
        now=lambda: clock[0],
    )
    server = start_server(fake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, fake, clock
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def before_release(served):
    """Each test starts with the late-added Family still held back."""
    server, fake, clock = served
    clock[0] = RELEASE - timedelta(days=1)
    return server, fake, clock


def client(
    server, tmp_path, *, key=LOCAL_PARISHSOFT_KEY, organization=LOCAL_ORGANIZATION_ID
):
    """The strict coherent client over a real HTTP Session to the fake."""
    return CoherentParishSoftClient(
        ParishSoftConfig(
            key,
            tmp_path / "unused-cache",
            api_base_url=base_url(server),
            cache_enabled=False,
        ),
        organization_id=organization,
        session=ExactSession(),
    )


def get(server, endpoint, *, headers=KEY, **params):
    return requests.get(
        base_url(server) + "/" + endpoint, params=params, headers=headers, timeout=5
    )


def post(server, endpoint, body, *, headers=KEY):
    return requests.post(
        base_url(server) + "/" + endpoint, json=body, headers=headers, timeout=5
    )


def test_full_load_passes_the_strict_loaders(before_release, tmp_path):
    """Setup loading's exact pipeline reads the default parish with no giving window."""
    server, fake, _ = before_release
    parish = fake.parish
    result = load_full_source(
        client(server, tmp_path), window=RefreshWindow(None, ()), as_of=ANCHOR
    )
    late = parish.late_family_id
    members = [m for m in parish.members if m["familyDUID"] != late]
    rosters = sum(len(rows) for rows in parish.ministry_rosters.values())
    # Every Member and Family row carries contact keys, so each has a contact.
    assert result.counts == {
        "family": 100,
        "member": len(members),
        "contact": len(members) + 100,
        "address": 100,
        "ministry": 25,
        "roster": rosters,
        "fund": 4,
        "pledge": 0,
        "contribution": 0,
    }
    assert str(late) not in result.corpus["family"]
    families = result.corpus["family"].values()
    eligible = sum(1 for row in families if row["email_eligible"])
    assert (
        70 <= eligible <= 90
    )  # 8 inactive/elsewhere, 10 without head email, a few more
    assert sum(1 for row in families if row["portal_eligible"]) == 92
    assert all(
        row["current"] in (True, False) for row in result.corpus["roster"].values()
    )
    assert sum(
        1 for row in result.corpus["roster"].values() if not row["current"]
    ) == sum(
        1 for rows in parish.ministry_rosters.values() for row in rows if row["endDate"]
    )
    children = {str(m["memberDUID"]) for m in members if is_child(m, ANCHOR)}
    assert not any(
        row["member_key"] in children for row in result.corpus["roster"].values()
    )
    assert result.evidence["requests"] > 30


def test_full_load_reads_scoped_giving(before_release, tmp_path):
    """The giving loader's fund, tenant and date queries select exact records."""
    server, fake, _ = before_release
    parish = fake.parish
    funds = tuple(sorted(f["fundId"] for f in parish.funds if f["requiresPledges"]))
    period = GivingPeriod(date(2026, 1, 1), date(2026, 12, 31), funds)
    window = RefreshWindow(__import__("uuid").uuid4(), (period,))
    result = load_full_source(client(server, tmp_path), window=window, as_of=ANCHOR)
    pledges = [
        p
        for p in parish.pledges
        if p["fundID"] in funds and p["pledgeStartDate"][:4] == "2026"
    ]
    contributions = [
        c
        for c in parish.contributions
        if c["fundId"] in funds
        and "2026-01-01" <= c["contributionDate"][:10] <= ANCHOR.isoformat()
    ]
    assert result.counts["pledge"] == len(pledges) > 0
    anonymous = [c for c in contributions if c["familyId"] == 0]
    assert result.counts["contribution"] == len(contributions) - len(anonymous) > 0
    assert result.evidence["anonymous_contributions"] == len(anonymous)
    sample = result.corpus["contribution"][str(contributions[0]["contributionID"])]
    assert sample["amount"] == f"{contributions[0]['contributionAmount']:.2f}"
    assert sample["family_key"] == str(contributions[0]["familyId"])


def test_change_feed_is_empty_by_default_and_validates_the_window(tmp_path):
    """The real feed has returned nothing in Production (#465); so does the fake.

    Dates are still validated as the client sends them, and the fake applies
    no window limit of its own beyond refusing an inverted window.
    """
    fake = FakeParishSoft(
        configuration(release_at=RELEASE.isoformat()), now=lambda: RELEASE
    )
    assert fake.configuration.change_feed == "empty" and fake.released()
    server = start_server(fake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = client(server, tmp_path)
        source.validate_organization()
        for start, end in [
            (ANCHOR - timedelta(days=31), ANCHOR),
            (date(2026, 9, 30), date(2026, 10, 2)),
            (date(2020, 1, 1), date(2020, 1, 31)),
        ]:
            result = load_family_changes(
                source,
                organization_id=LOCAL_ORGANIZATION_ID,
                start_date=start,
                end_date=end,
            )
            assert result.family_ids == () and result.indication_count == 0
        wide = get(
            server, "families/change/list", StartDate="2000-01-01", EndDate="2099-12-31"
        )
        assert wide.status_code == 200 and wide.json() == []
        assert get(server, "families/change/list").status_code == 400
        assert (
            get(server, "families/change/list", StartDate="2026-09-01").status_code
            == 400
        )
        assert (
            get(server, "families/change/list", StartDate="x", EndDate="y").status_code
            == 400
        )
        assert (
            get(
                server,
                "families/change/list",
                StartDate="2026-09-02",
                EndDate="2026-09-01",
            ).status_code
            == 400
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_synthetic_change_feed_serves_only_the_release(before_release):
    """In synthetic mode the feed holds exactly the late Family's release row."""
    server, fake, clock = before_release
    assert fake.configuration.change_feed == "synthetic"
    wide = {"StartDate": "2000-01-01", "EndDate": "2099-12-31"}
    assert get(server, "families/change/list", **wide).json() == []
    clock[0] = RELEASE
    rows = get(server, "families/change/list", **wide).json()
    assert [row["family_DUID"] for row in rows] == [fake.parish.late_family_id]
    assert (
        get(
            server, "families/change/list", StartDate="2026-09-01", EndDate="2026-09-30"
        ).json()
        == []
    )


def test_family_slice_reads_the_household(before_release, tmp_path):
    """Detail reads return the Family, its Members and contact fallbacks."""
    server, fake, _ = before_release
    source = client(server, tmp_path)
    source.validate_organization()
    family = fake.parish.families[0]
    expected = [
        m for m in fake.parish.members if m["familyDUID"] == family["familyDUID"]
    ]
    result = load_family_slice(source, family_id=family["familyDUID"])
    assert result.family["familyDUID"] == family["familyDUID"]
    assert sorted(result.members) == sorted(m["memberDUID"] for m in expected)
    for identifier, contact in result.contacts.items():
        member = result.members[identifier]
        assert contact["dateOfBirth"] == member["birthdate"]
        assert contact["gender"] == member["sex"]
        assert contact["emailAddress"] == member["emailAddress"]


def test_late_added_family_appears_only_from_release_at(before_release, tmp_path):
    """Held back before the instant, served after it, with its change-feed row."""
    server, fake, clock = before_release
    late = fake.parish.late_family_id
    assert get(server, f"families/{late}").status_code == 404
    assert get(server, f"families/{late}/member/list").status_code == 404
    late_members = [
        m["memberDUID"] for m in fake.parish.members if m["familyDUID"] == late
    ]
    assert get(server, f"members/{late_members[0]}").status_code == 404
    page = post(
        server, "families/search", {"organizationIDs": [LOCAL_ORGANIZATION_ID]}
    ).json()
    assert page[0]["totalResults"] == 100
    window = {"StartDate": "2026-10-01", "EndDate": "2026-10-02"}
    assert get(server, "families/change/list", **window).json() == []

    clock[0] = RELEASE
    assert get(server, f"families/{late}").json()["familyDUID"] == late
    assert len(get(server, f"families/{late}/member/list").json()) == 3
    assert get(server, f"members/{late_members[0]}").json()["familyDUID"] == late
    page = post(
        server, "families/search", {"organizationIDs": [LOCAL_ORGANIZATION_ID]}
    ).json()
    assert page[0]["totalResults"] == 101 and page[-1]["familyDUID"] == late
    rows = get(server, "families/change/list", **window).json()
    assert rows == [
        {
            "family_DUID": late,
            "currentParishID": LOCAL_ORGANIZATION_ID,
            "previousParishID": None,
            "logDate": RELEASE.isoformat(),
        }
    ]
    source = client(server, tmp_path)
    source.validate_organization()
    assert load_family_changes(
        source,
        organization_id=LOCAL_ORGANIZATION_ID,
        start_date=date(2026, 9, 30),
        end_date=date(2026, 10, 2),
    ).family_ids == (late,)
    result = load_full_source(source, window=RefreshWindow(None, ()), as_of=ANCHOR)
    assert result.counts["family"] == 101

    clock[0] = RELEASE - timedelta(seconds=1)
    assert get(server, f"families/{late}").status_code == 404


def test_release_at_null_never_releases():
    """Without a release instant the late Family is held back indefinitely."""
    fake = FakeParishSoft(configuration(), now=lambda: datetime(2999, 1, 1, tzinfo=UTC))
    assert not fake.released()
    status, _ = fake.handle(
        "GET", f"{BASE_PATH}families/{fake.parish.late_family_id}", headers=KEY
    )
    assert status == 404


@pytest.mark.parametrize("headers", [{}, {"x-api-key": "other-key"}, {"X-API-KEY": ""}])
def test_wrong_or_missing_key_gets_401_with_an_empty_body(
    before_release, tmp_path, headers
):
    """Authentication precedes routing, and the body says nothing."""
    server, _, _ = before_release
    response = get(server, "families/group/lookup/list", headers=headers)
    assert (response.status_code, response.content) == (401, b"")
    response = post(server, "organizations/search", {}, headers=headers)
    assert (response.status_code, response.content) == (401, b"")
    assert get(server, "no/such/path", headers=headers).status_code == 401
    with pytest.raises(ParishSoftAPIError) as failure:
        client(server, tmp_path, key="other-key").validate_organization()
    assert failure.value.status_code == 401


def test_unknown_paths_methods_and_tenants(before_release):
    """No write surface: 405 for other methods, 404 for other paths and tenants."""
    server, _, _ = before_release
    url = base_url(server)
    assert get(server, "families/search").status_code == 405
    assert post(server, "ministry/type/list", {}).status_code == 405
    for method in ("PUT", "PATCH", "DELETE"):
        response = requests.request(
            method, url + "/families/search", json={}, headers=KEY, timeout=5
        )
        assert (response.status_code, response.content) == (405, b"")
    assert (
        requests.head(
            url + "/families/group/lookup/list", headers=KEY, timeout=5
        ).status_code
        == 405
    )
    assert get(server, "families/change").status_code == 404
    assert get(server, "nothing").status_code == 404
    assert (
        requests.get(
            url.replace("/api/v2", "/api/v1") + "/families/group/lookup/list",
            headers=KEY,
            timeout=5,
        ).status_code
        == 404
    )
    assert (
        requests.get(url.rsplit("/", 2)[0] + "/", headers=KEY, timeout=5).status_code
        == 404
    )
    other = LOCAL_ORGANIZATION_ID + 1
    assert (
        post(server, "families/search", {"organizationIDs": [other]}).status_code == 404
    )
    assert post(server, "families/search", {}).status_code == 404
    assert (
        post(
            server,
            "members/search",
            {"organizationIDs": [LOCAL_ORGANIZATION_ID, other]},
        ).status_code
        == 404
    )
    assert get(server, f"offering/{other}/funds").status_code == 404
    assert get(server, "ministry/type/list", organizationId=other).status_code == 404
    assert (
        get(server, "members/workgroup/lookup/list", organizationId=other).status_code
        == 404
    )
    assert get(server, "offering/pledge/list", OrganizationID=other).status_code == 404
    assert (
        get(
            server, "offering/contributiondetail/list", OrganizationId=other
        ).status_code
        == 404
    )
    assert get(server, "families/999999").status_code == 404
    assert get(server, "members/999999").status_code == 404
    assert get(server, "ministry/999999/minister/list").status_code == 404
    assert get(server, "members/workgroup/999999/list").status_code == 404
    assert get(server, "families/workgroup/999999/list").status_code == 404
    assert (
        requests.post(
            url + "/families/search",
            data=b"not json",
            headers={**KEY, "Content-Type": "application/json"},
            timeout=5,
        ).status_code
        == 400
    )


def test_paging_fields_are_bounded_consistent_and_stable(before_release):
    """Page limits, envelope metadata, zero/one probes and ordinals per the contract."""
    server, fake, _ = before_release
    organization = {"organizationIDs": [LOCAL_ORGANIZATION_ID]}
    assert (
        post(server, "families/search", organization | {"pageSize": 501}).status_code
        == 400
    )
    assert (
        post(server, "families/search", organization | {"pageNumber": 0}).status_code
        == 400
    )
    assert (
        post(server, "families/search", organization | {"pageSize": "7"}).status_code
        == 200
    )
    assert (
        get(
            server,
            "ministry/type/list",
            organizationId=LOCAL_ORGANIZATION_ID,
            PageSize="x",
        ).status_code
        == 400
    )
    assert (
        get(
            server,
            "ministry/type/list",
            organizationId=LOCAL_ORGANIZATION_ID,
            PageNumber=0,
        ).status_code
        == 400
    )
    # Bare array with embedded total and contiguous ordinals across pages.
    rows = []
    for number in range(1, 16):
        page = post(
            server,
            "families/search",
            organization | {"pageSize": 7, "pageNumber": number},
        ).json()
        rows.extend(page)
    assert [row["rowNumber"] for row in rows] == list(range(1, 101))
    assert {row["totalResults"] for row in rows} == {100}
    assert (
        post(
            server, "families/search", organization | {"pageSize": 7, "pageNumber": 16}
        ).json()
        == []
    )
    assert [row["familyDUID"] for row in rows] == [
        f["familyDUID"] for f in fake.parish.families[:100]
    ]
    # Zero and one both answer the first page of the zero-origin searches.
    first = post(
        server, "members/search", organization | {"maximumRows": 10, "startRowIndex": 0}
    ).json()
    assert (
        first
        == post(
            server,
            "members/search",
            organization | {"maximumRows": 10, "startRowIndex": 1},
        ).json()
    )
    assert [row["rowNum"] for row in first] == list(range(1, 11))
    second = post(
        server, "members/search", organization | {"maximumRows": 10, "startRowIndex": 2}
    ).json()
    assert [row["rowNum"] for row in second] == list(range(11, 21))
    assert (
        post(
            server, "members/contact/list", organization | {"limit": 5, "offset": 0}
        ).json()
        == post(
            server, "members/contact/list", organization | {"limit": 5, "offset": 1}
        ).json()
    )
    # Envelope metadata is internally consistent on every page.
    total = len(fake.parish.contributions)
    for number in (1, 2, 500):
        envelope = get(
            server,
            "offering/contributiondetail/list",
            OrganizationId=LOCAL_ORGANIZATION_ID,
            PageSize=3,
            PageNumber=number,
        ).json()
        assert set(envelope) == {"data", "pagingInfo"}
        assert envelope["pagingInfo"] == {
            "totalRecords": total,
            "totalPages": -(-total // 3),
            "pageSize": 3,
            "pageNumber": number,
        }
        assert len(envelope["data"]) == (3 if number < 454 else 0)
    empty = get(
        server,
        "ministry/type/list",
        organizationId=LOCAL_ORGANIZATION_ID,
        PageSize=500,
        PageNumber=1,
    ).json()
    assert empty["pagingInfo"]["totalRecords"] == 25 and len(empty["data"]) == 25
    # Date and fund filters on contributions, fund filter on pledges.
    fund = fake.parish.funds[1]["fundId"]
    filtered = get(
        server,
        "offering/contributiondetail/list",
        OrganizationId=LOCAL_ORGANIZATION_ID,
        FundId=fund,
        StartDate="2026-06-01",
        EndDate="2026-06-30",
    ).json()["data"]
    assert filtered and all(
        row["fundId"] == fund
        and "2026-06-01" <= row["contributionDate"][:10] <= "2026-06-30"
        for row in filtered
    )
    pledges = get(
        server,
        "offering/pledge/list",
        OrganizationID=LOCAL_ORGANIZATION_ID,
        FundID=fund,
    ).json()["data"]
    assert pledges and all(row["fundID"] == fund for row in pledges)
    # The same request twice is byte-identical.
    assert (
        get(server, "families/workgroup/list").content
        == get(server, "families/workgroup/list").content
    )
    assert (
        get(server, "families/workgroup/list", PageSize=2).json()[0]["recordCount"] == 4
    )


def test_handle_serves_without_http():
    """The request handler is usable in process, as the database tests need."""
    fake = FakeParishSoft(configuration(families=5))
    status, body = fake.handle("POST", BASE_PATH + "organizations/search", {}, KEY)
    assert status == 200 and json.loads(body) == [fake.parish.organization]
    status, body = fake.handle(
        "GET", BASE_PATH + "families/group/lookup/list?PageSize=1", None, KEY
    )
    assert status == 200 and len(json.loads(body)) == 6
    assert (
        fake.handle("GET", BASE_PATH + "families/group/lookup/list", None, {})[0] == 401
    )
    assert fake.handle("POST", BASE_PATH + "families/search", [], KEY)[0] == 400
    with pytest.raises(TypeError):
        FakeParishSoft({"seed": 1})
    with pytest.raises(TypeError):
        start_server(object())


@pytest.mark.parametrize(
    "document",
    [
        {"seed": "1"},
        {"seed": -1},
        {"families": 0},
        {"families": "100"},
        {"anchor_date": "2026-9-16"},
        {"anchor_date": None},
        {"release_at": "2026-10-01T12:00:00"},
        {"release_at": 5},
        {"extra": True},
        {"change_feed": "history"},
        {"change_feed": None},
    ],
)
def test_configuration_rejects_malformed_files(document):
    """Only the four documented fields, with a zoned release instant, are accepted."""
    base = {"seed": 1, "families": 100, "anchor_date": "2026-09-16", "release_at": None}
    with pytest.raises(ValueError):
        FakeConfiguration.parse(base | document)
    with pytest.raises(ValueError):
        FakeConfiguration.parse([base])


def test_configuration_reads_the_file_once(tmp_path):
    """The file's values, including the release instant, are parsed exactly."""
    path = tmp_path / "fake-parishsoft.json"
    path.write_text(
        json.dumps(
            {
                "seed": 42,
                "families": 20,
                "anchor_date": "2026-09-16",
                "release_at": "2026-10-01T08:00:00-04:00",
            }
        )
    )
    value = FakeConfiguration.read(path)
    assert value == FakeConfiguration(42, 20, ANCHOR, RELEASE, "empty")
    assert configuration(change_feed="synthetic").change_feed == "synthetic"
    fake = FakeParishSoft(value)
    assert fake.parish == generate(42, 20, ANCHOR)
    assert fake.parish.family_count == 20


def fake_file(tmp_path, **changes):
    """Write a fake configuration file for the command tests."""
    path = tmp_path / "fake-parishsoft.json"
    path.write_text(
        json.dumps(
            {"seed": 1, "families": 5, "anchor_date": "2026-09-16", "release_at": None}
            | changes
        )
    )
    return str(path)


def deployment_file(tmp_path, profile):
    """A minimal deployment YAML naming one profile."""
    path = tmp_path / f"{profile}.yaml"
    path.write_text(yaml.safe_dump({"deployment": {"profile": profile}}))
    return str(path)


@pytest.fixture
def quiet(monkeypatch):
    """CLI tests must not install the runtime's stderr logging handler."""
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda *a, **k: None
    )
    monkeypatch.delenv("PARISHKIT_STEWARDSHIP_PROFILE", raising=False)


def test_command_refuses_outside_local_before_binding(
    quiet, monkeypatch, tmp_path, capsys
):
    """Only an admitted LOCAL deployment configuration binds a socket."""
    path = fake_file(tmp_path)
    bind = Mock()
    monkeypatch.setattr(module, "start_server", bind)
    for profile in ("production", "development", "test"):
        assert (
            main(["fake-parishsoft", "--profile", profile, "--fake-config", path]) == 2
        )
        monkeypatch.setenv("PARISHKIT_STEWARDSHIP_PROFILE", profile)
        assert main(["fake-parishsoft", "--fake-config", path]) == 2
        monkeypatch.delenv("PARISHKIT_STEWARDSHIP_PROFILE")
    # No profile anywhere resolves to development, which is refused.
    assert main(["fake-parishsoft", "--fake-config", path]) == 2
    # A deployment file for another profile is refused even with a local flag.
    development = deployment_file(tmp_path, "development")
    assert (
        main(["fake-parishsoft", "--config", development, "--fake-config", path]) == 2
    )
    assert (
        main(
            [
                "fake-parishsoft",
                "--config",
                str(tmp_path / "missing.yaml"),
                "--profile",
                "local",
                "--fake-config",
                path,
            ]
        )
        == 2
    )
    # LOCAL without a usable fake configuration is refused too.
    assert main(["fake-parishsoft", "--profile", "local"]) == 2
    assert (
        main(
            [
                "fake-parishsoft",
                "--profile",
                "local",
                "--fake-config",
                str(tmp_path / "missing.json"),
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "fake-parishsoft",
                "--profile",
                "local",
                "--fake-config",
                path,
                "--port",
                "0",
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "fake-parishsoft",
                "--profile",
                "local",
                "--fake-config",
                fake_file(tmp_path, change_feed="history"),
            ]
        )
        == 2
    )
    bind.assert_not_called()
    captured = capsys.readouterr()
    assert path not in captured.err and str(tmp_path) not in captured.err


def test_command_serves_in_local(quiet, monkeypatch, tmp_path):
    """In LOCAL the command reads the file, binds the documented port and serves."""
    path = fake_file(tmp_path)
    server = Mock()
    bind = Mock(return_value=server)
    monkeypatch.setattr(module, "start_server", bind)
    assert main(["fake-parishsoft", "--profile", "local", "--fake-config", path]) == 0
    fake = bind.call_args.args[0]
    assert isinstance(fake, FakeParishSoft) and fake.configuration.families == 5
    assert bind.call_args.kwargs == {"host": "0.0.0.0", "port": 8080}
    server.serve_forever.assert_called_once()
    server.server_close.assert_called_once()
    monkeypatch.setenv("PARISHKIT_STEWARDSHIP_PROFILE", "local")
    assert main(["fake-parishsoft", "--fake-config", path, "--port", "9090"]) == 0
    assert bind.call_args.kwargs == {"host": "0.0.0.0", "port": 9090}
    monkeypatch.delenv("PARISHKIT_STEWARDSHIP_PROFILE")
    local = deployment_file(tmp_path, "local")
    assert main(["fake-parishsoft", "--config", local, "--fake-config", path]) == 0
    assert bind.call_count == 3


def test_reminder_workgroup_reads_only_the_named_family_workgroup(
    before_release, tmp_path
):
    """The Reminder WorkGroup read (#861) lists the WorkGroups, then fetches
    the members of only the one the campaign names, matched ignoring case."""
    from parishkit.stewardship.source.workgroups import load_reminder_workgroup

    server, fake, _ = before_release
    workgroup = fake.parish.family_workgroups[1]
    members = sorted(
        row["familyId"]
        for row in fake.parish.family_workgroup_rosters[workgroup["workgroupDUID"]]
    )
    coherent = client(server, tmp_path)
    # A refresh has validated the organization before it reads the WorkGroup.
    coherent.validate_organization()
    before = coherent.request_count
    found = load_reminder_workgroup(coherent, workgroup["workgroupName"].upper())
    assert found == {
        "name": workgroup["workgroupName"].upper(),
        "found": True,
        "family_duids": members,
    }
    # One list request and one membership request (each a single page here),
    # never the other WorkGroups' rosters.
    assert coherent.request_count - before <= 4
    other = client(server, tmp_path)
    other.validate_organization()
    missing = load_reminder_workgroup(other, "No such group")
    assert missing == {"name": "No such group", "found": False, "family_duids": []}
    assert load_reminder_workgroup(client(server, tmp_path), None) is None
