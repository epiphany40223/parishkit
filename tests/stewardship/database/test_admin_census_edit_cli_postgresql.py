"""``census edit`` and ``census resolve-conflict`` (#1434).

An Automatic row's Edit value and Clear edit (ADM-09.04) and its Use the
Family's value anyway (ADM-09.03). Process admission is replaced by the
test's own Django assembly; everything after it is the real command line on
the restricted web login, writing through the page's own admission and
services (``edit_census_change``, ``resolve_census_conflict``) inside the
page's command scope, so their guards (in Python and in 0107's and 0108's
SQL) are the page's. Each records the service's ``census_change_updated``
and its own ``admin_cmd_census_edit`` or
``admin_cmd_census_resolve_conflict``, once per request key, and prints ids,
versions and states only.
"""

import json
from uuid import uuid4

import pytest

from parishkit.stewardship import admin_cli
from parishkit.stewardship.responses.models import ProposalReview

from .auth_builders import signed_in
from .automation_builders import as_staff
from .test_admin_census_cli_postgresql import submitted
from .test_admin_census_resolve_cli_postgresql import count, rows
from .test_admin_census_review_cli_postgresql import review
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_admin_task_retry_cli_postgresql import approved_by_browser, runner
from .test_publication_plans_postgresql import owner

pytestmark = pytest.mark.django_db(transaction=True)

EDIT = "admin_cmd_census_edit"
CONFLICT = "admin_cmd_census_resolve_conflict"
UPDATED = "census_change_updated"


def edit(run, proposal, *extra, version=None, key=None):
    """``census edit`` for ``proposal`` at its current (or given) version."""
    proposal.refresh_from_db()
    return run(
        *("census", "edit", str(proposal.pk)),
        *("--expected-version", str(proposal.version if version is None else version)),
        *("--request-key", str(key or uuid4())),
        *extra,
    )


def resolve(run, proposal, *extra, version=None, key=None):
    """``census resolve-conflict`` for ``proposal`` at its current version."""
    proposal.refresh_from_db()
    return run(
        *("census", "resolve-conflict", str(proposal.pk)),
        *("--expected-version", str(proposal.version if version is None else version)),
        *("--request-key", str(key or uuid4())),
        *extra,
    )


def conflicted(row, *, known=True):
    """Put the change in conflict, ParishSoft now holding another value."""
    owner(
        "UPDATE stewardship_proposed_change SET execution='conflict',"
        " current_available=%s, current_value=%s::jsonb WHERE id=%s",
        [known, json.dumps("Parish value") if known else None, row.pk],
    )


def test_an_administrator_edits_and_clears_a_value(
    response_service,
    google,
    cli,  # noqa: F811
):
    """Edit value and Clear edit through the page's service, with both
    events, a document that never holds the value or the note, and an
    unchanged edit that records nothing."""
    submitted(response_service)
    signed_in()
    run = cli()
    _, name = rows()

    key = uuid4()
    code, document = edit(run, name, "--value", "Edited", "--note", "Why", key=key)
    assert code == 0, document
    result = document["result"]
    name.refresh_from_db()
    assert result["created"] and not result["unchanged"]
    assert result["review"]["proposal_id"] == str(name.pk)
    assert result["review"]["edited"] is True
    assert result["review"]["expected_version"] == name.version - 1
    assert result["proposal"] == {
        "id": str(name.pk),
        "version": name.version,
        "decision": "unreviewed",
        "execution": "pending",
        "edited": True,
    }
    assert "Edited" not in str(document) and "Why" not in str(document)
    assert (name.admin_value_set, name.admin_value) == (True, "Edited")
    assert (count(EDIT), count(UPDATED)) == (1, 1)

    # A repeat returns the first edit; the key for another value is invalid.
    first = name.version - 1
    code, document = edit(
        run, name, "--value", "Edited", "--note", "Why", key=key, version=first
    )
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["review"] == result["review"]
    code, document = edit(
        run, name, "--value", "Other", "--note", "Why", key=key, version=first
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert (count(EDIT), count(UPDATED)) == (1, 1)

    # The same value again changes nothing and records nothing.
    code, document = edit(run, name, "--value", "Edited")
    assert code == 0, document
    assert document["result"]["unchanged"] and not document["result"]["created"]
    assert document["result"]["review"] is None
    assert (count(EDIT), count(UPDATED)) == (1, 1)

    # Clear edit; clearing again, or the Family's own answer, is unchanged.
    code, document = edit(run, name, "--clear")
    assert code == 0 and document["result"]["review"]["edited"] is False, document
    assert document["result"]["proposal"]["edited"] is False
    for extra in (("--clear",), ("--value", name.submitted_value)):
        code, document = edit(run, name, *extra)
        assert code == 0 and document["result"]["unchanged"], (extra, document)
    assert (count(EDIT), count(UPDATED)) == (2, 2)
    assert ProposalReview.objects.count() == 2


def test_a_value_the_change_cannot_take_is_invalid_with_its_reason(
    response_service,
    google,
    cli,  # noqa: F811
):
    """A blank or too-long value is invalid with the service's static
    reason for the field ``value``, never the value; nothing is recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    _, name = rows()
    for value in ("   ", "x" * 200):
        code, document = edit(run, name, "--value", value)
        assert code == 1 and document["error"]["code"] == "invalid", document
        (field,) = document["error"]["fields"]
        assert field["field"] == "value" and field["code"] == "invalid"
        assert field["message"] and "xxxx" not in str(document)
    assert (count(EDIT), count(UPDATED)) == (0, 0)


def test_edit_refusals(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """By hand and queued changes are invalid, a changed version stale, an
    unknown change not available, and Staff denied; nothing is recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    home, name = rows()
    code, document = edit(run, home, "--value", "1 Main St")
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = edit(run, name, "--value", "Pat", version=name.version + 1)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    code, document = run(
        *("census", "edit", str(uuid4()), "--value", "Pat"),
        *("--expected-version", "1", "--request-key", str(uuid4())),
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    with monkeypatch.context() as patch:
        as_staff(patch)
        code, document = edit(run, name, "--value", "Pat")
        assert code == 1 and document["error"]["code"] == "denied", document
    owner(
        "UPDATE stewardship_proposed_change SET execution='queued',"
        " decision='approved' WHERE id=%s",
        [name.pk],
    )
    code, document = edit(run, name, "--value", "Pat")
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert (count(EDIT), count(UPDATED)) == (0, 0)
    assert not ProposalReview.objects.exists()


def test_an_administrator_resolves_a_conflict_and_review_undoes_it(
    response_service,
    google,
    cli,  # noqa: F811
):
    """Use the Family's value anyway approves the Conflict and makes it
    pending, with both events, once per key; census review then puts it back
    to Conflict."""
    submitted(response_service)
    signed_in()
    run = cli()
    _, name = rows()
    conflicted(name)

    key = uuid4()
    code, document = resolve(run, name, "--note", "Checked", key=key)
    assert code == 0, document
    result = document["result"]
    name.refresh_from_db()
    assert result["created"] and result["review"]["decision"] == "approved"
    assert result["review"]["proposal_id"] == str(name.pk)
    assert result["proposal"] == {
        "id": str(name.pk),
        "version": name.version,
        "decision": "approved",
        "execution": "pending",
    }
    assert "Checked" not in str(document) and "Parish value" not in str(document)
    assert ProposalReview.objects.get().accepted_base
    assert (count(CONFLICT), count(UPDATED)) == (1, 1)

    # A repeat returns the first resolution; the key with another note is
    # invalid.
    first = name.version - 1
    code, document = resolve(run, name, "--note", "Checked", key=key, version=first)
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["review"] == result["review"]
    code, document = resolve(run, name, "--note", "Other", key=key, version=first)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert (count(CONFLICT), count(UPDATED)) == (1, 1)

    # Nothing is left to resolve; Back to To review brings the Conflict back.
    code, document = resolve(run, name)
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = review(run, name, "unreviewed")
    assert code == 0, document
    assert document["result"]["proposal"]["execution"] == "conflict"
    code, document = resolve(run, name)
    assert code == 0, document
    assert (count(CONFLICT), count(UPDATED)) == (2, 3)


def test_resolve_conflict_refusals(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """No conflict, an unknown ParishSoft value and By hand are invalid, a
    changed version stale, and Staff denied; nothing is recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    home, name = rows()
    code, document = resolve(run, name)
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = resolve(run, home)
    assert code == 1 and document["error"]["code"] == "invalid", document
    conflicted(name, known=False)
    code, document = resolve(run, name)
    assert code == 1 and document["error"]["code"] == "invalid", document
    conflicted(name)
    code, document = resolve(run, name, version=name.version + 1)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    with monkeypatch.context() as patch:
        as_staff(patch)
        code, document = resolve(run, name)
        assert code == 1 and document["error"]["code"] == "denied", document
    assert (count(CONFLICT), count(UPDATED)) == (0, 0)
    assert not ProposalReview.objects.exists()


def test_a_value_from_standard_input_is_stored_trimmed(
    response_service,
    google,
    settings,
    monkeypatch,
):
    """``--value -`` reads the rest of standard input, trimmed as the Family
    form trims an answer; one over 1,000 characters is invalid with a fixed
    reason, before the service is reached."""
    submitted(response_service)
    signed_in()
    commands = runner(settings.STEWARDSHIP_AUTH_RUNTIME, monkeypatch)
    secret = approved_by_browser(commands.service)
    _, name = rows()

    def from_stdin(answer):
        """``census edit --value -`` with ``answer`` on standard input."""
        name.refresh_from_db()
        code, document, _ = commands(
            *("census", "edit", str(name.pk), "--value", "-"),
            *("--expected-version", str(name.version)),
            *("--request-key", str(uuid4())),
            secret=secret,
            answer=answer,
        )
        return code, document

    code, document = from_stdin(b"  From input  \n")
    assert code == 0 and document["result"]["review"]["edited"], document
    name.refresh_from_db()
    assert (name.admin_value_set, name.admin_value) == (True, "From input")
    code, document = from_stdin(b"y" * 1001)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert document["error"]["fields"] == [
        {"field": "value", "code": "invalid", "message": admin_cli.TOO_LONG_EDIT}
    ]
    assert "yyyy" not in str(document)
    assert (count(EDIT), count(UPDATED)) == (1, 1)
