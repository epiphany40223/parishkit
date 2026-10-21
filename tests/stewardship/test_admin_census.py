"""``census resolve``, ``review``, ``edit`` and ``resolve-conflict``.

Their documents, options and vocabulary. All are Census changes row writes
(#463, #528, #1434). The commands' behavior against a database is in
``database/test_admin_census_resolve_cli_postgresql.py``,
``database/test_admin_census_review_cli_postgresql.py`` and
``database/test_admin_census_edit_cli_postgresql.py``.
"""

import io
import json
from argparse import Namespace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from django.db import DatabaseError, IntegrityError

from parishkit.stewardship import admin_census, admin_changes, admin_cli
from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.responses.models import RESOLUTION_ACTIONS, REVIEW_DECISIONS

from .test_admin_reads import FORBIDDEN, members

PROPOSAL = UUID("00000000-0000-4000-8000-000000000001")
RESOLUTION = UUID("00000000-0000-4000-8000-000000000002")
KEY = UUID("00000000-0000-4000-8000-000000000003")
CREATED = datetime(2026, 10, 12, 18, 30, tzinfo=UTC)


def test_the_actions_are_the_services():
    """The parser's choices, spelled out before Django, are the service's."""
    assert admin_cli.CENSUS_ACTIONS == RESOLUTION_ACTIONS
    # In the pipeline's words (decision 7, #1755): accepted, stored approved.
    assert (
        tuple(map(admin_census.decision_word, REVIEW_DECISIONS))
        == admin_cli.CENSUS_DECISIONS
    )
    assert admin_cli.CENSUS_DECISIONS[0] == "accepted"
    assert tuple(map(admin_census.stored_decision, admin_cli.CENSUS_DECISIONS)) == (
        REVIEW_DECISIONS
    )


def test_only_the_words_change_not_the_stored_values():
    """Decision 7 (#1755): the command line says accepted for the stored
    approved, both ways, and leaves every other decision and record as is."""
    assert admin_census.decision_word("approved") == "accepted"
    assert admin_census.stored_decision("accepted") == "approved"
    for decision in ("ignored", "unreviewed"):
        assert admin_census.decision_word(decision) == decision
        assert admin_census.stored_decision(decision) == decision
    assert admin_census._worded({"id": 1, "decision": "approved"}) == {
        "id": 1,
        "decision": "accepted",
    }
    assert admin_census._worded({"id": 1}) == {"id": 1}
    assert admin_census._worded(None) is None


def test_the_review_document_names_ids_versions_and_states_only():
    """Golden document: no note, Family, Member or value, nor a guarded word."""
    model = admin_census.CensusReview(
        created=True,
        request_key=KEY,
        review={
            "id": RESOLUTION,
            "proposal_id": PROPOSAL,
            "decision": "approved",
            "expected_version": 3,
            "created_at": CREATED,
        },
        proposal={
            "id": PROPOSAL,
            "version": 4,
            "decision": "approved",
            "execution": "pending",
        },
    )
    document = model.to_document()
    assert document == {
        "created": True,
        "request_key": str(KEY),
        "review": {
            "id": str(RESOLUTION),
            "proposal_id": str(PROPOSAL),
            "decision": "approved",
            "expected_version": 3,
            "created_at": "2026-10-12T18:30:00+00:00",
        },
        "proposal": {
            "id": str(PROPOSAL),
            "version": 4,
            "decision": "approved",
            "execution": "pending",
        },
    }
    for name in members(document):
        assert FORBIDDEN.search(name) is None, name
    assert "note" not in set(members(document))


def test_review_checks_the_administrator_s_role_first(monkeypatch):
    """``census review`` is an Administrator's (``PUBLISH_CENSUS``): its
    first step is that read-only check (#1341), before it reads the note,
    makes a key or looks anything up."""
    calls = []

    def denied(caller, service, *, read_only=False, capability=None):
        """Refuse as the admission does for a user who lacks the capability."""
        calls.append((read_only, capability))
        raise PermissionError("denied")

    def unexpected(*arguments, **options):
        """Nothing after the role check may run."""
        raise AssertionError("read or looked up before the role check")

    monkeypatch.setattr(admin_changes, "_admit_change", denied)
    monkeypatch.setattr(admin_cli, "_input", unexpected)
    monkeypatch.setattr(admin_cli, "_request_key", unexpected)
    with pytest.raises(PermissionError):
        admin_cli.census_review(Namespace(), None, None, {"caller": None})
    assert calls == [(True, Capability.PUBLISH_CENSUS)]


def test_the_document_names_ids_versions_and_states_only():
    """Golden document: no note, Family, Member or value, nor a guarded word."""
    model = admin_census.CensusResolve(
        created=True,
        request_key=KEY,
        resolution={
            "id": RESOLUTION,
            "proposal_id": PROPOSAL,
            "action": "entered",
            "expected_version": 3,
            "created_at": CREATED,
        },
        proposal={
            "id": PROPOSAL,
            "version": 4,
            "decision": "unreviewed",
            "execution": "resolved_external",
        },
    )
    document = model.to_document()
    assert document == {
        "created": True,
        "request_key": str(KEY),
        "resolution": {
            "id": str(RESOLUTION),
            "proposal_id": str(PROPOSAL),
            "action": "entered",
            "expected_version": 3,
            "created_at": "2026-10-12T18:30:00+00:00",
        },
        "proposal": {
            "id": str(PROPOSAL),
            "version": 4,
            "decision": "unreviewed",
            "execution": "resolved_external",
        },
    }
    for name in members(document):
        assert FORBIDDEN.search(name) is None, name
    assert "note" not in set(members(document))


@pytest.mark.parametrize(
    "argv",
    [
        # Each required piece missing.
        ["census", "resolve", "--action", "entered", "--expected-version", "1"],
        ["census", "resolve", str(PROPOSAL), "--expected-version", "1"],
        ["census", "resolve", str(PROPOSAL), "--action", "entered"],
        # Outside the page's vocabulary.
        [
            *("census", "resolve", str(PROPOSAL)),
            *("--action", "published", "--expected-version", "1"),
        ],
        [
            *("census", "resolve", "not-a-uuid"),
            *("--action", "entered", "--expected-version", "1"),
        ],
        [
            *("census", "resolve", str(PROPOSAL)),
            *("--action", "entered", "--expected-version", "0"),
        ],
        [
            *("census", "resolve", str(PROPOSAL)),
            *("--action", "ignored", "--expected-version", "1"),
            *("--request-key", "nine"),
        ],
        # Standard input cannot carry both the note and the answer.
        [
            *("census", "resolve", str(PROPOSAL)),
            *("--action", "entered", "--expected-version", "1", "--note", "-"),
        ],
        # census review: each required piece missing, and its vocabulary.
        ["census", "review", "--decision", "accepted", "--expected-version", "1"],
        ["census", "review", str(PROPOSAL), "--expected-version", "1"],
        ["census", "review", str(PROPOSAL), "--decision", "accepted"],
        # Decision 7 (#1755): no alias for the old word.
        [
            *("census", "review", str(PROPOSAL)),
            *("--decision", "approved", "--expected-version", "1"),
        ],
        [
            *("census", "review", str(PROPOSAL)),
            *("--decision", "approve", "--expected-version", "1"),
        ],
        [
            *("census", "review", str(PROPOSAL)),
            *("--decision", "entered", "--expected-version", "1"),
        ],
        [
            *("census", "review", str(PROPOSAL)),
            *("--decision", "accepted", "--expected-version", "0"),
        ],
        # census edit: a value or --clear, never both nor neither.
        ["census", "edit", str(PROPOSAL), "--expected-version", "1"],
        [
            *("census", "edit", str(PROPOSAL), "--expected-version", "1"),
            *("--value", "Pat", "--clear"),
        ],
        ["census", "edit", "--value", "Pat", "--expected-version", "1"],
        ["census", "edit", str(PROPOSAL), "--value", "Pat"],
        # census resolve-conflict: each required piece missing.
        ["census", "resolve-conflict", "--expected-version", "1"],
        ["census", "resolve-conflict", str(PROPOSAL)],
        ["census", "resolve-conflict", str(PROPOSAL), "--expected-version", "0"],
    ],
)
def test_options_outside_the_pages_vocabulary_are_usage_errors(argv):
    """A missing or malformed option is a usage error before admission."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2
    assert json.loads(out.getvalue())["error"]["code"] == "usage"


class Driver(Exception):
    """The driver error a database error carries, with its SQLSTATE."""

    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def raised(error, sqlstate):
    """``error`` as Django raises it: chained from the driver's error."""
    try:
        try:
            raise Driver(sqlstate)
        except Driver as cause:
            raise error from cause
    except DatabaseError as chained:
        return chained


def test_only_a_unique_violation_is_the_racing_write():
    """A unique violation (23505) is the page's racing second write, so
    stale; any other refusal, or a unique violation not raised as an
    integrity error, is the database error it is."""
    assert admin_census.racing_write(raised(IntegrityError("duplicate"), "23505"))
    for error in (
        raised(IntegrityError("guard"), "23514"),
        raised(IntegrityError("guard"), "P0001"),
        raised(DatabaseError("other"), "23505"),
        IntegrityError("no cause"),
    ):
        assert not admin_census.racing_write(error), error


def test_the_edit_document_names_ids_versions_and_states_only():
    """Golden document (#1434): whether an edit is set, never its value, the
    note, the Family or the Member; an unchanged edit has no review."""
    proposal = {
        "id": PROPOSAL,
        "version": 4,
        "decision": "approved",
        "execution": "pending",
        "edited": True,
    }
    model = admin_census.CensusEdit(
        created=True,
        unchanged=False,
        request_key=KEY,
        review={
            "id": RESOLUTION,
            "proposal_id": PROPOSAL,
            "edited": True,
            "expected_version": 3,
            "created_at": CREATED,
        },
        proposal=proposal,
    )
    document = model.to_document()
    assert document == {
        "created": True,
        "unchanged": False,
        "request_key": str(KEY),
        "review": {
            "id": str(RESOLUTION),
            "proposal_id": str(PROPOSAL),
            "edited": True,
            "expected_version": 3,
            "created_at": "2026-10-12T18:30:00+00:00",
        },
        "proposal": {
            "id": str(PROPOSAL),
            "version": 4,
            "decision": "approved",
            "execution": "pending",
            "edited": True,
        },
    }
    for name in members(document):
        assert FORBIDDEN.search(name) is None, name
    assert not {"note", "value"} & set(members(document))
    unchanged = admin_census.CensusEdit(
        created=False,
        unchanged=True,
        request_key=KEY,
        review=None,
        proposal=proposal,
    ).to_document()
    assert unchanged["review"] is None and unchanged["unchanged"]


@pytest.mark.parametrize("handler", ["census_edit", "census_resolve_conflict"])
def test_edit_and_resolve_conflict_check_the_role_first(monkeypatch, handler):
    """Both are an Administrator's (``PUBLISH_CENSUS``): the read-only role
    check comes before any input, key or lookup (#1341)."""
    calls = []

    def denied(caller, service, *, read_only=False, capability=None):
        """Refuse as the admission does for a user who lacks the capability."""
        calls.append((read_only, capability))
        raise PermissionError("denied")

    def unexpected(*arguments, **options):
        """Nothing after the role check may run."""
        raise AssertionError("read or looked up before the role check")

    monkeypatch.setattr(admin_changes, "_admit_change", denied)
    monkeypatch.setattr(admin_cli, "_input", unexpected)
    monkeypatch.setattr(admin_cli, "_request_key", unexpected)
    args = Namespace(value="Pat", note="", clear=False)
    with pytest.raises(PermissionError):
        getattr(admin_cli, handler)(args, None, None, {"caller": None})
    assert calls == [(True, Capability.PUBLISH_CENSUS)]


def test_edit_reads_one_input_from_standard_input():
    """The value and the note cannot both come from standard input."""
    args = Namespace(value="-", note="-", clear=False)
    with pytest.raises(admin_cli.UsageError):
        admin_cli.census_edit(args, None, None, {"caller": None})


def test_edit_passes_the_value_the_clear_and_the_note(monkeypatch):
    """``--value`` is passed as typed (``-`` reads standard input), ``--clear``
    as None, and the note stripped, as the page posts them."""
    from parishkit.stewardship import admin_census as module

    seen = []
    monkeypatch.setattr(admin_cli, "_role_first", lambda *arguments: None)
    monkeypatch.setattr(
        module, "edit_census", lambda *arguments, **options: seen.append(options)
    )
    context = {
        "caller": None,
        "stdin": io.BytesIO(b"  Pat  \n"),
        "stderr": io.StringIO(),
    }
    common = {"proposal_id": PROPOSAL, "expected_version": 2, "request_key": KEY}
    admin_cli.census_edit(
        Namespace(value=" Pat ", note=" why ", clear=False, **common),
        None,
        None,
        context,
    )
    admin_cli.census_edit(
        Namespace(value="-", note="", clear=False, **common), None, None, context
    )
    admin_cli.census_edit(
        Namespace(value=None, note="", clear=True, **common), None, None, context
    )
    assert [(options["value"], options["note"]) for options in seen] == [
        (" Pat ", "why"),
        ("Pat", ""),
        (None, ""),
    ]


@pytest.mark.parametrize(
    ("value", "stdin"),
    [("x" * 1001, b""), ("-", "é".encode() * 1001), ("-", b"\xff")],
)
def test_an_edit_the_service_cannot_read_is_refused_with_a_reason(
    monkeypatch, value, stdin
):
    """More than 1,000 characters (as an option or on standard input), or
    input that is not UTF-8, is ``invalid`` with a fixed reason for the
    field ``value``, before the service is called."""
    from parishkit.stewardship import admin_census as module

    monkeypatch.setattr(admin_cli, "_role_first", lambda *arguments: None)
    monkeypatch.setattr(module, "edit_census", pytest.fail)
    context = {"caller": None, "stdin": io.BytesIO(stdin), "stderr": io.StringIO()}
    args = Namespace(
        proposal_id=PROPOSAL,
        expected_version=2,
        request_key=KEY,
        value=value,
        note="",
        clear=False,
    )
    with pytest.raises(admin_changes.InvalidChange) as raised:
        admin_cli.census_edit(args, None, None, context)
    assert raised.value.fields == [
        {"field": "value", "code": "invalid", "message": admin_cli.TOO_LONG_EDIT}
    ]
    # Exactly 1,000 characters is the service's to judge.
    seen = []
    monkeypatch.setattr(
        module, "edit_census", lambda *arguments, **options: seen.append(options)
    )
    admin_cli.census_edit(
        Namespace(**{**vars(args), "value": "x" * 1000}), None, None, context
    )
    assert len(seen[0]["value"]) == 1000
