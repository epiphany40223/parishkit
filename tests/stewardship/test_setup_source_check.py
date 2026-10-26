"""The setup wizard's quick parish data check (#2201), without a network.

The helper's check runs against a stubbed transport, so every answer it can
give (the organization's name and totals, a refused key, an organization the
key does not reach, an unreadable or unreachable ParishSoft) is pinned
without contacting ParishSoft, as are the parent's answer checks, the error
codes and the Finishing setup page's final-load failure wording.
"""

import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from parishkit.parishsoft import DEFAULT_API_BASE_URL
from parishkit.stewardship import provider_check_worker as worker
from parishkit.stewardship import provider_checks as parent
from parishkit.stewardship.accounts import setup_checks
from parishkit.stewardship.accounts.setup_check_models import CHECK_ERRORS
from parishkit.stewardship.deployment import DeploymentProfile

from .test_organization_lookup import KEY, Process, Response, request

ORGANIZATIONS = Response(
    [
        {"organizationID": 7, "organizationReportName": "School"},
        {"organizationID": 1234, "organizationReportName": " St. Example\tParish "},
    ]
)
FAMILIES = Response([{"familyDUID": 5, "rowNumber": 1, "totalResults": 812}])
MEMBERS = Response([{"memberDUID": 9, "rowNum": 1, "recordCount": 2034}])


def transport(monkeypatch, answers):
    """Answer each endpoint from ``answers`` (a Response or an exception).

    Returns the requests made, as (endpoint, body) pairs.
    """
    calls = []

    def request(session, method, url, **kwargs):
        """Route by endpoint; never open a socket."""
        endpoint = url.rsplit("/", 2)[-2]
        calls.append((endpoint, kwargs.get("json")))
        answer = answers[endpoint]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests.Session, "request", request)
    return calls


def check(organization=1234):
    """Run the helper's check in-process."""
    return worker.check_source(
        KEY,
        organization=organization,
        profile=DeploymentProfile.PRODUCTION,
        base_url=DEFAULT_API_BASE_URL,
        seconds=120,
    )


def test_reads_the_name_and_both_totals_with_three_requests(monkeypatch, tmp_path):
    """One organization search, then one-row Family and Member searches."""
    monkeypatch.chdir(tmp_path)
    calls = transport(
        monkeypatch,
        {"organizations": ORGANIZATIONS, "families": FAMILIES, "members": MEMBERS},
    )
    assert check() == {
        "outcome": "ok",
        "name": "St. Example Parish",
        "families": 812,
        "members": 2034,
    }
    assert calls == [
        ("organizations", {}),
        ("families", {"organizationIDs": [1234], "pageSize": 1, "pageNumber": 1}),
        ("members", {"organizationIDs": [1234], "maximumRows": 1, "startRowIndex": 0}),
    ]


def test_empty_searches_are_zero_totals(monkeypatch, tmp_path):
    """No Families or Members is an answer, not an error (the parent decides)."""
    monkeypatch.chdir(tmp_path)
    transport(
        monkeypatch,
        {
            "organizations": Response([{"organizationID": 1234}]),
            "families": Response([]),
            "members": Response([]),
        },
    )
    assert check() == {"outcome": "ok", "name": None, "families": 0, "members": 0}


@pytest.mark.parametrize(
    "answers,expected",
    [
        ({"organizations": Response(b"", status=401)}, "refused"),
        ({"organizations": Response(b"", status=500)}, "unavailable"),
        ({"organizations": Response(b"not json")}, "unreadable"),
        ({"organizations": Response({"organizationID": 1234})}, "unreadable"),
        ({"organizations": requests.ConnectionError("synthetic")}, "unavailable"),
        ({"organizations": Response([{"organizationID": 7}])}, "not_reached"),
        ({"organizations": Response([])}, "not_reached"),
        (
            {"organizations": ORGANIZATIONS, "families": Response(b"", status=403)},
            "refused",
        ),
        (
            {"organizations": ORGANIZATIONS, "families": requests.Timeout("x")},
            "unavailable",
        ),
        (
            {
                "organizations": ORGANIZATIONS,
                "families": Response([{"rowNumber": 2, "totalResults": 3}]),
            },
            "unreadable",
        ),
        (
            {
                "organizations": ORGANIZATIONS,
                "families": Response([{"rowNumber": 1, "totalResults": "3"}]),
            },
            "unreadable",
        ),
        (
            {
                "organizations": ORGANIZATIONS,
                "families": FAMILIES,
                "members": Response([{"rowNum": 1, "recordCount": 0}]),
            },
            "unreadable",
        ),
        (
            {
                "organizations": ORGANIZATIONS,
                "families": FAMILIES,
                "members": Response([{"rowNum": 1, "recordCount": 2**31}]),
            },
            "unreadable",
        ),
        (
            {
                "organizations": ORGANIZATIONS,
                "families": FAMILIES,
                "members": Response(b"", status=404),
            },
            "unavailable",
        ),
    ],
)
def test_every_failure_is_one_closed_outcome(monkeypatch, tmp_path, answers, expected):
    """A refused key on any request, an unreached organization, or the rest."""
    monkeypatch.chdir(tmp_path)
    transport(monkeypatch, answers)
    assert check() == {"outcome": expected}


def test_the_check_session_admits_only_its_three_searches():
    """The Member search is admitted only with members=True."""
    with worker.CheckSession(
        DeploymentProfile.PRODUCTION, DEFAULT_API_BASE_URL, families=True
    ) as session:
        assert ("POST", DEFAULT_API_BASE_URL + "/members/search") not in (
            session.endpoints
        )
    with worker.CheckSession(
        DeploymentProfile.PRODUCTION,
        DEFAULT_API_BASE_URL,
        families=True,
        members=True,
    ) as session:
        assert {
            ("POST", DEFAULT_API_BASE_URL + path)
            for path in ("/organizations/search", "/families/search", "/members/search")
        } <= session.endpoints
    with pytest.raises(ValueError):
        worker.CheckSession(members=True)


def check_request(**changes):
    """A well-formed check request, with ``changes`` applied."""
    return request(operation="source_check", organization=1234) | changes


@pytest.mark.parametrize(
    "changes",
    [
        {"organization": 0},
        {"organization": "1234"},
        {"organization": 2**31},
        {"operation": "other"},
        {"extra": 1},
    ],
)
def test_worker_refuses_an_ill_formed_check_request(monkeypatch, changes):
    """The helper refuses an ill-formed request before any provider IO."""
    network = Mock()
    monkeypatch.setattr(worker, "CheckSession", network)
    assert worker.lookup_request(check_request(**changes)) == {"outcome": "unavailable"}
    network.assert_not_called()


def test_a_lookup_request_naming_an_organization_is_refused(monkeypatch):
    """Only the check names an organization; a lookup with one is ill-formed."""
    network = Mock()
    monkeypatch.setattr(worker, "CheckSession", network)
    assert worker.lookup_request(request(organization=1234)) == {
        "outcome": "unavailable"
    }
    network.assert_not_called()


def test_worker_main_answers_a_check_with_one_json_line(monkeypatch, capsys):
    """The helper's main sends a source_check request to the check."""
    answer = {"outcome": "ok", "name": "A", "families": 1, "members": 2}
    checked = Mock(return_value=answer)
    monkeypatch.setattr(worker, "check_source", checked)
    stdin = io.BytesIO(json.dumps(check_request()).encode())
    monkeypatch.setattr(worker.sys, "stdin", Mock(buffer=stdin))
    try:
        assert worker.main() == 0
    finally:
        # main() silences all logging for the isolated helper process.
        logging.disable(logging.NOTSET)
    assert json.loads(capsys.readouterr().out) == answer
    assert checked.call_args.kwargs["organization"] == 1234


OK = {"outcome": "ok", "name": "St. Example Parish", "families": 3, "members": 5}


@pytest.mark.parametrize(
    "output,returncode,expected",
    [
        (json.dumps(OK).encode() + b"\n", 0, OK),
        (json.dumps(OK | {"name": None}).encode(), 0, OK | {"name": None}),
        (b'{"outcome": "not_reached"}\n', 0, {"outcome": "not_reached"}),
        (b'{"outcome": "refused", "extra": 1}\n', 0, {"outcome": "unavailable"}),
        (json.dumps(OK).encode(), 1, {"outcome": "unavailable"}),
        (b"valid\n", 0, {"outcome": "unavailable"}),
        (json.dumps(OK | {"families": -1}).encode(), 0, {"outcome": "unavailable"}),
        (json.dumps(OK | {"members": "5"}).encode(), 0, {"outcome": "unavailable"}),
        (json.dumps(OK | {"name": ""}).encode(), 0, {"outcome": "unavailable"}),
        (json.dumps(OK | {"extra": 1}).encode(), 0, {"outcome": "unavailable"}),
        (b'{"outcome": "organizations"}\n', 0, {"outcome": "unavailable"}),
    ],
)
def test_parent_accepts_only_a_well_formed_check_answer(
    monkeypatch, output, returncode, expected
):
    """Anything but the helper's exact answer shape is unavailable."""
    process = Process(output, returncode)
    monkeypatch.setattr(parent.subprocess, "Popen", Mock(return_value=process))
    answer = parent.check_source(
        KEY, organization=1234, check=lambda: None, profile=DeploymentProfile.LOCAL
    )
    assert answer == expected
    sent = json.loads(process.inputs[0])
    assert sent["operation"] == "source_check" and sent["organization"] == 1234
    assert sent["seconds"] == 120 and sent["profile"] == "local"


@pytest.mark.parametrize("organization", [0, "1234", 2**31, None])
def test_parent_refuses_an_invalid_check_invocation(organization):
    """No helper starts without a key, an owner check and an organization."""
    with pytest.raises(ValueError):
        parent.check_source(
            KEY,
            organization=organization,
            check=lambda: None,
            profile=DeploymentProfile.PRODUCTION,
        )


@pytest.mark.parametrize(
    "answer,expected",
    [
        ({"outcome": "refused"}, ("parishsoft_key_refused", None)),
        ({"outcome": "not_reached"}, ("parishsoft_organization_not_reached", None)),
        ({"outcome": "unreadable"}, ("parishsoft_check_unreadable", None)),
        ({"outcome": "unavailable"}, ("parishsoft_unavailable", None)),
        (OK | {"families": 0}, ("parishsoft_no_families", None)),
        (
            OK,
            (
                None,
                {
                    "organization_name": "St. Example Parish",
                    "family_count": 3,
                    "member_count": 5,
                },
            ),
        ),
        (
            OK | {"members": 0, "name": None},
            (
                None,
                {"organization_name": None, "family_count": 3, "member_count": 0},
            ),
        ),
    ],
)
def test_outcome_names_each_error_and_keeps_only_the_answer(answer, expected):
    """Each failure has its stable code; a ready check keeps three values."""
    assert setup_checks.outcome(answer) == expected


def test_every_error_code_has_its_page_message():
    """The page explains each stable code the model admits."""
    from parishkit.stewardship.accounts.setup_views import (
        CHECK_ERRORS as MESSAGES,
    )
    from parishkit.stewardship.accounts.setup_views import (
        CHECK_FIXED_AT_CONNECTION,
    )

    assert set(MESSAGES) == set(CHECK_ERRORS)
    assert set(setup_checks.OUTCOME_ERRORS.values()) | {
        "parishsoft_no_families"
    } == set(CHECK_ERRORS)
    assert set(CHECK_ERRORS) > CHECK_FIXED_AT_CONNECTION
    text = str(MESSAGES["parishsoft_no_families"]) % {
        "organization": "St. Example Parish (1234)"
    }
    assert text.startswith("ParishSoft has no Families for St. Example Parish (1234).")


def view(state, *, current=True, error=None):
    """A page view of one check."""
    return setup_checks.CheckStatus(
        "check",
        state,
        current,
        1234,
        "St. Example Parish" if state == "ready" else None,
        812 if state == "ready" else None,
        2034 if state == "ready" else None,
        error,
    )


def test_only_a_current_ready_check_completes_the_step():
    """A check of an earlier connection, or one not answered, does not count."""
    assert view("ready").checked
    assert not view("ready", current=False).checked
    assert not view("failed", error="parishsoft_unavailable").checked
    assert view("queued").waiting and view("running").waiting
    assert not view("ready").waiting


@pytest.mark.parametrize(
    "credential,expected",
    [
        (
            SimpleNamespace(
                pk=1, version=2, fingerprint="f", settings={"organization_id": 3}
            ),
            True,
        ),
        (
            SimpleNamespace(
                pk=1, version=3, fingerprint="f", settings={"organization_id": 3}
            ),
            False,
        ),
        (
            SimpleNamespace(
                pk=1, version=2, fingerprint="g", settings={"organization_id": 3}
            ),
            False,
        ),
        (
            SimpleNamespace(
                pk=1, version=2, fingerprint="f", settings={"organization_id": 4}
            ),
            False,
        ),
        (
            SimpleNamespace(
                pk=9, version=2, fingerprint="f", settings={"organization_id": 3}
            ),
            False,
        ),
        (None, False),
    ],
)
def test_a_check_is_current_only_for_the_key_and_organization_it_checked(
    credential, expected
):
    """Credential row, version, fingerprint and organization must all match."""
    row = SimpleNamespace(
        credential_id=1, credential_version=2, fingerprint="f", organization_id=3
    )
    assert setup_checks._current(row, credential) is expected


@pytest.mark.parametrize(
    "shown,expected",
    [
        (None, "initial"),
        (view("queued"), "waiting"),
        (view("running"), "waiting"),
        (view("ready"), "ready"),
        (view("ready", current=False), "stale"),
        (view("failed", error="parishsoft_no_families"), "failed"),
        (view("failed", current=False, error="parishsoft_unavailable"), "stale"),
    ],
)
def test_the_page_draws_one_state_over_the_others(shown, expected):
    """One state is drawn; every other is an invisible copy in the same slot."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.setup_views import CHECK_STATES, check_page

    context = check_page(shown, "St. Example Parish (1234)")
    assert context["check_shown"] == expected
    assert context["check_states"] == CHECK_STATES and expected in CHECK_STATES
    assert ("check_error" in context) is (expected == "failed")
    html = render_to_string(
        "stewardship/setup-source.html",
        context
        | {
            "draft": {"status": {"attempt_id": "a", "version": 2}},
            "wizard": None,
            "source_step": None,
        },
    )
    slot = html.split('<div class="setup-check-slot">')[1].split("</form>")[0]
    # Exactly the drawn state is announced; the copies are hidden from all.
    assert slot.count('role="status"') == (0 if expected == "initial" else 1)
    assert slot.count('aria-hidden="true"') >= len(CHECK_STATES) - 1
    assert ("data-reread-every" in html) is (expected == "waiting")
    assert ('id="setup-check-reason"' in html) is (expected == "waiting")
    if expected == "failed":
        assert 'href="/admin/setup/credentials/parishsoft"' in slot
    else:
        assert "/admin/setup/credentials/parishsoft" not in slot
