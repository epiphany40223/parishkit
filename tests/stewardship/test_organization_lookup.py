"""The setup wizard's ParishSoft organization lookup (#1554), without a network.

The helper's lookup runs against a stubbed transport, so every answer it can
give (a list with each organization's Families result, a refused key, an
unreadable or empty list, an unreachable ParishSoft) and every way a Family
check can leave an organization unknown (an error, a timeout, a malformed
row, the overall time limit) is pinned without contacting ParishSoft.
"""

import base64
import io
import json
from unittest.mock import Mock

import pytest
import requests

from parishkit.parishsoft import DEFAULT_API_BASE_URL
from parishkit.stewardship import provider_check_worker as worker
from parishkit.stewardship import provider_checks as parent
from parishkit.stewardship.accounts import setup_lookups
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.source.organization_names import organization_label

KEY = b"synthetic-private"


class Response:
    """One finite fake streamed response with a JSON body."""

    def __init__(self, body, status=200):
        self.status_code = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"content-type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        """The whole body in one chunk."""
        yield self.body


def transport(monkeypatch, organizations, families):
    """Answer the organization search and each Family search from tables.

    ``families`` maps an organization ID to its Family search answer (a
    ``Response`` or an exception to raise). Returns the requests made.
    """
    calls = []

    def request(session, method, url, **kwargs):
        """Route by endpoint; never open a socket."""
        calls.append((url.rsplit("/", 1)[-1], kwargs.get("json"), kwargs["timeout"]))
        if url.endswith("/organizations/search"):
            return organizations
        answer = families[kwargs["json"]["organizationIDs"][0]]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests.Session, "request", request)
    return calls


def lookup(seconds=120):
    """Run the helper's lookup in-process."""
    return worker.lookup_organizations(
        KEY,
        profile=DeploymentProfile.PRODUCTION,
        base_url=DEFAULT_API_BASE_URL,
        seconds=seconds,
    )


def row(total=3):
    """A one-row Family search answer."""
    return Response([{"familyDUID": 5, "rowNumber": 1, "totalResults": total}])


def test_lists_every_organization_with_its_families_result(monkeypatch, tmp_path):
    """Holds Families, holds none, and every way a check stays unknown."""
    monkeypatch.chdir(tmp_path)
    organizations = Response(
        [
            {"organizationID": 1, "organizationReportName": "  St. A\tParish "},
            {"organizationID": 2, "organizationReportName": "School"},
            {"organizationID": 3, "organizationReportName": "Error"},
            {"organizationID": 4, "organizationReportName": "Timeout"},
            {"organizationID": 5, "organizationReportName": "Malformed"},
            {"organizationID": 6},
        ]
    )
    calls = transport(
        monkeypatch,
        organizations,
        {
            1: row(),
            2: Response([]),
            3: Response(b"", status=404),
            4: requests.Timeout("synthetic"),
            5: Response([{"familyDUID": 5, "rowNumber": 2, "totalResults": 3}]),
            6: row(),
        },
    )
    answer = lookup()
    assert answer == {
        "outcome": "ok",
        "organizations": [
            {"id": 1, "name": "St. A Parish", "families": True},
            {"id": 2, "name": "School", "families": False},
            {"id": 3, "name": "Error", "families": None},
            {"id": 4, "name": "Timeout", "families": None},
            {"id": 5, "name": "Malformed", "families": None},
            {"id": 6, "name": None, "families": True},
        ],
        "unchecked": 0,
        "elapsed": None,
    }
    # One uncached organization search, then one one-row Family search each.
    assert calls[0][0] == "search" and calls[0][1] == {}
    assert [call[1] for call in calls[1:]] == [
        {"organizationIDs": [n], "pageSize": 1, "pageNumber": 1} for n in range(1, 7)
    ]


@pytest.mark.parametrize(
    "organizations,expected",
    [
        (Response(b"", status=401), "refused"),
        (Response(b"", status=403), "refused"),
        (Response(b"", status=500), "unavailable"),
        (Response(b"", status=404), "unavailable"),
        (Response(b"not json"), "unreadable"),
        (Response({"organizationID": 1}), "unreadable"),
        (Response([{"organizationID": 1}, {"organizationID": 1}]), "unreadable"),
        (Response([{"organizationID": "1"}]), "unreadable"),
        (Response(b" " * (worker.MAX_ORGANIZATION_RESPONSE + 1)), "unreadable"),
    ],
)
def test_organization_list_failures_are_closed(
    monkeypatch, tmp_path, organizations, expected
):
    """Each failure of the list itself is one closed outcome, nothing more."""
    monkeypatch.chdir(tmp_path)
    transport(monkeypatch, organizations, {})
    assert lookup() == {"outcome": expected}


def test_an_unreachable_parishsoft_is_unavailable(monkeypatch, tmp_path):
    """A connection failure or timeout of the list is not a verdict on the key."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        requests.Session, "request", Mock(side_effect=requests.ConnectionError("x"))
    )
    assert lookup() == {"outcome": "unavailable"}


def test_an_empty_list_reaches_no_organization(monkeypatch, tmp_path):
    """The lookup reports an empty list itself; the load would refuse it."""
    monkeypatch.chdir(tmp_path)
    transport(monkeypatch, Response([]), {})
    assert lookup() == {
        "outcome": "ok",
        "organizations": [],
        "unchecked": 0,
        "elapsed": None,
    }


def test_a_refused_key_on_a_family_check_ends_the_lookup(monkeypatch, tmp_path):
    """HTTP 401 or 403 on any check is the refused-key error."""
    monkeypatch.chdir(tmp_path)
    transport(
        monkeypatch,
        Response([{"organizationID": 1}, {"organizationID": 2}]),
        {1: row(), 2: Response(b"", status=403)},
    )
    assert lookup() == {"outcome": "refused"}


class Clock:
    """A monotonic clock the fake transport moves on."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_the_time_limit_leaves_the_rest_unknown(monkeypatch, tmp_path):
    """Checks stop before the limit; what was not checked stays unknown."""
    monkeypatch.chdir(tmp_path)
    clock = Clock()
    monkeypatch.setattr(worker.time, "monotonic", clock)

    class Slow(Response):
        """The first Family check takes past the limit."""

        def iter_content(self, chunk_size):
            clock.now = 200.0
            yield self.body

    transport(
        monkeypatch,
        Response([{"organizationID": 1}, {"organizationID": 2}, {"organizationID": 3}]),
        {1: Slow([{"familyDUID": 5, "rowNumber": 1, "totalResults": 3}]), 2: row()},
    )
    answer = lookup()
    assert answer["organizations"] == [
        {"id": 1, "name": None, "families": True},
        {"id": 2, "name": None, "families": None},
        {"id": 3, "name": None, "families": None},
    ]
    assert answer["unchecked"] == 2 and answer["elapsed"] == 200.0


def test_a_check_near_the_limit_asks_for_less_time(monkeypatch, tmp_path):
    """Each request is capped at 10 seconds and at what the limit leaves."""
    monkeypatch.chdir(tmp_path)
    clock = Clock()
    monkeypatch.setattr(worker.time, "monotonic", clock)

    class Slow(Response):
        """The organization search takes most of the limit."""

        def iter_content(self, chunk_size):
            clock.now = 109.0
            yield self.body

    calls = transport(monkeypatch, Slow([{"organizationID": 1}]), {1: row()})
    lookup()
    assert calls[0][2] == 10
    # The limit less the stop margin (115 s) less the 109 s already used.
    assert calls[1][2] == pytest.approx(6.0)


def test_the_family_search_is_admitted_only_for_the_lookup():
    """A provider check's session cannot send a Family search."""
    check = worker.CheckSession(DeploymentProfile.PRODUCTION, DEFAULT_API_BASE_URL)
    with pytest.raises(ValueError, match="Unsupported"):
        check.request("POST", DEFAULT_API_BASE_URL + "/families/search", json={})
    look = worker.CheckSession(
        DeploymentProfile.PRODUCTION, DEFAULT_API_BASE_URL, families=True
    )
    assert ("POST", DEFAULT_API_BASE_URL + "/families/search") in look.endpoints
    with pytest.raises(ValueError):
        worker.CheckSession(families=True)


def request(**changes):
    """A well-formed lookup request, as the parent writes it."""
    return {
        "operation": "organizations",
        "candidate": base64.b64encode(KEY).decode(),
        "profile": "production",
        "base_url": DEFAULT_API_BASE_URL,
        "seconds": 120,
    } | changes


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "check"},
        {"seconds": 0},
        {"seconds": 121},
        {"seconds": "120"},
        {"profile": "nowhere"},
        {"candidate": "not base64!"},
        {"extra": 1},
    ],
)
def test_a_malformed_lookup_request_contacts_nothing(monkeypatch, changes):
    """The helper refuses an ill-formed request before any provider IO."""
    network = Mock()
    monkeypatch.setattr(worker, "CheckSession", network)
    assert worker.lookup_request(request(**changes)) == {"outcome": "unavailable"}
    network.assert_not_called()


def test_worker_main_answers_a_lookup_with_one_json_line(monkeypatch, capsys):
    """The helper's main tells a lookup from a check by its operation."""
    monkeypatch.setattr(
        worker, "lookup_organizations", Mock(return_value={"outcome": "refused"})
    )
    stdin = io.BytesIO(json.dumps(request()).encode())
    monkeypatch.setattr(worker.sys, "stdin", Mock(buffer=stdin))
    assert worker.main() == 0
    assert capsys.readouterr().out == '{"outcome": "refused"}\n'


class Process:
    """A finished helper with a fixed output."""

    def __init__(self, output, returncode=0):
        self.stdout, self.stdin = io.BytesIO(), io.BytesIO()
        self.output, self.returncode = output, returncode
        self.inputs = []

    def communicate(self, *, input, timeout):
        """Record the request; answer the output."""
        self.inputs.append(input)
        return self.output, None

    def poll(self):
        return self.returncode

    def kill(self):
        pass

    def wait(self, *, timeout):
        pass


OK = {
    "outcome": "ok",
    "organizations": [{"id": 1, "name": "A", "families": True}],
    "unchecked": 0,
    "elapsed": None,
}


@pytest.mark.parametrize(
    "output,returncode,expected",
    [
        (json.dumps(OK).encode() + b"\n", 0, OK),
        (b'{"outcome": "refused"}\n', 0, {"outcome": "refused"}),
        (b'{"outcome": "refused", "extra": 1}\n', 0, {"outcome": "unavailable"}),
        (json.dumps(OK).encode(), 1, {"outcome": "unavailable"}),
        (b"valid\n", 0, {"outcome": "unavailable"}),
        (
            json.dumps(
                OK | {"organizations": [{"id": 0, "name": None, "families": True}]}
            ).encode(),
            0,
            {"outcome": "unavailable"},
        ),
        (
            json.dumps(
                OK | {"organizations": [{"id": 1, "name": None, "families": "yes"}]}
            ).encode(),
            0,
            {"outcome": "unavailable"},
        ),
        (
            json.dumps(OK | {"organizations": OK["organizations"] * 2}).encode(),
            0,
            {"outcome": "unavailable"},
        ),
        (json.dumps(OK | {"unchecked": 2}).encode(), 0, {"outcome": "unavailable"}),
    ],
)
def test_parent_accepts_only_a_well_formed_answer(
    monkeypatch, output, returncode, expected
):
    """Anything but the helper's exact answer shape is unavailable."""
    process = Process(output, returncode)
    monkeypatch.setattr(parent.subprocess, "Popen", Mock(return_value=process))
    answer = parent.lookup_organizations(
        KEY, check=lambda: None, profile=DeploymentProfile.PRODUCTION
    )
    assert answer == expected
    sent = json.loads(process.inputs[0])
    assert sent["operation"] == "organizations" and sent["seconds"] == 120
    assert base64.b64decode(sent["candidate"]) == KEY


def test_parent_refuses_an_invalid_invocation():
    """No helper starts without a key, an owner check and a profile."""
    with pytest.raises(ValueError):
        parent.lookup_organizations(b"", check=lambda: None, profile=None)


@pytest.mark.parametrize(
    "answer,expected",
    [
        ({"outcome": "refused"}, ("parishsoft_key_refused", None)),
        ({"outcome": "unavailable"}, ("parishsoft_unavailable", None)),
        ({"outcome": "unreadable"}, ("parishsoft_organizations_unreadable", None)),
        (OK | {"organizations": []}, ("parishsoft_no_organizations", None)),
        (
            OK | {"organizations": [{"id": 1, "name": "A", "families": False}]},
            ("parishsoft_no_family_organizations", None),
        ),
        (
            OK
            | {
                "organizations": [
                    {"id": 9, "name": None, "families": None},
                    {"id": 7, "name": "b", "families": True},
                    {"id": 3, "name": "B", "families": False},
                    {"id": 2, "name": "a", "families": True},
                ]
            },
            (
                None,
                [
                    {"id": 2, "name": "a", "families": True},
                    {"id": 3, "name": "B", "families": False},
                    {"id": 7, "name": "b", "families": True},
                    {"id": 9, "name": None, "families": None},
                ],
            ),
        ),
    ],
)
def test_outcome_names_each_error_and_orders_the_list(answer, expected):
    """By name (any case) and then ID; an unnamed organization comes last."""
    assert setup_lookups.outcome(answer) == expected


def test_status_marks_unknown_and_unselectable_organizations():
    """The page offers Check again for unknowns and greys no-Families ones."""
    row = Mock(
        pk="lookup",
        state="ready",
        error_code=None,
        credential_version=2,
        organizations=[
            {"id": 1, "name": "A", "families": True},
            {"id": 2, "name": "B", "families": False},
            {"id": 3, "name": None, "families": None},
        ],
    )
    status = setup_lookups._status(row)
    assert status.unknown and not status.pending
    assert [item.selectable for item in status.organizations] == [True, False, True]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("St. Example Parish", "St. Example Parish (1234)"),
        (None, "ParishSoft organization 1234"),
        ("", "ParishSoft organization 1234"),
    ],
)
def test_organization_label(name, expected):
    """Name (ID), or the ID alone while no name is recorded."""
    assert organization_label(1234, name) == expected
