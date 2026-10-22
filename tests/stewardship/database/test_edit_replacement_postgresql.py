"""A non-reviewed response after a reviewed one that showed an Edit (#1909).

A reviewer Edits a census change, the Family submits the reviewed form
(#1705) leaving the reviewer's value as shown (reviewed rule 1: no new row,
the change stays on its earlier response and the response's
``shown_items`` name it), and then submits a form not drawn as the
reviewed form (a Testing form, or one open across the deploy). Python's
``effective.proposal_index`` finds the shown change for that next
response, and ``derive_proposals`` replaces it or carries its decision
(review pipeline spec, "Resubmission before the reviewed form"). Frozen
migration 0163 (``schema/migrations/0163_edit_replacement.sql``) makes the
census guard find the same change, as the predecessor a same answer
carries and as the change a Family response may replace; before it, the
database refused the submit ("Proposal replacement requires a new final
Family response"). These tests submit real responses and check the file's
DO block against the body it replaces.
"""

import re

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.responses import baselines
from parishkit.stewardship.responses.models import Submission

from .test_census_pipeline_review_postgresql import decide
from .test_census_resubmission_postgresql import again, rows_for
from .test_refresh_checks_postgresql import prepared
from .test_strict_phase_order_postgresql import SCHEMA, Rollback, body

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = SCHEMA / "migrations/0163_edit_replacement.sql"
# The function 0163 replaces, and the file holding the body it replaces.
EARLIER = {
    "stewardship_response_derived_guard_v1": "0160_resubmission_phase_order.sql",
}


def latest():
    """The Family's newest response."""
    return Submission.objects.order_by("-family_version").first()


def shown_edit(harness, monkeypatch):
    """The fixture's first name change, Edited to "Edited" by a reviewer,
    then left as shown on a reviewed form (no new row); every later form is
    drawn by the rule before the reviewed form. Returns the change."""
    harness, staff, _ = prepared(harness)
    (change,) = rows_for("first_name")
    decide(harness, staff, [change], "edit", value="Edited")
    again(harness)
    assert latest().reviewed_view
    (kept,) = rows_for("first_name")
    assert (kept.pk, kept.admin_value, kept.decision) == (
        change.pk,
        "Edited",
        "approved",
    )
    # As the earlier_rule fixture (response_builders), from here on.
    monkeypatch.setattr(baselines, "_shown", lambda *args: (False, None))
    return harness, kept


@pytest.mark.parametrize("answer", ["Changed again", "Edited"])
def test_a_new_answer_supersedes_the_shown_edit(response_service, monkeypatch, answer):
    """A different answer than the Family's own, including the reviewer's
    value itself, supersedes the shown change with a new change To review:
    one open item, the old one history."""
    harness, kept = shown_edit(response_service, monkeypatch)
    again(harness, first_name=answer)
    assert not latest().reviewed_view
    old, new = rows_for("first_name")
    assert old.pk == kept.pk
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert new.submitted_value == answer
    assert (new.decision, new.execution, new.admin_value_set) == (
        "unreviewed",
        "pending",
        False,
    )


def test_the_familys_own_answer_carries_the_edit(response_service, monkeypatch):
    """The Family's own answer again is the same intent: its successor
    carries the Accept with the reviewer's value and the baseline, as a
    successor on the response before does."""
    harness, kept = shown_edit(response_service, monkeypatch)
    again(harness, first_name="Requested")
    old, new = rows_for("first_name")
    assert (old.pk, old.execution, old.superseded_by_id) == (
        kept.pk,
        "superseded",
        new.pk,
    )
    assert (new.decision, new.admin_value_set, new.admin_value) == (
        "approved",
        True,
        "Edited",
    )
    assert (new.baseline_available, new.baseline_value) == (
        kept.baseline_available,
        kept.baseline_value,
    )


def test_the_form_as_drawn_commits(response_service, monkeypatch):
    """The Family sends the older form as it is drawn (it shows the Family's
    own answer): the submit commits, and the shown change is superseded by
    a successor that carries the Accept, the reviewer's value and the
    baseline, so the reviewer's decision is not dropped."""
    harness, kept = shown_edit(response_service, monkeypatch)
    again(harness)
    old, new = rows_for("first_name")
    assert (old.pk, old.execution, old.superseded_by_id) == (
        kept.pk,
        "superseded",
        new.pk,
    )
    assert (new.decision, new.execution, new.admin_value) == (
        "approved",
        "pending",
        "Edited",
    )
    assert (new.baseline_available, new.baseline_value) == (
        kept.baseline_available,
        kept.baseline_value,
    )


def test_an_ignored_change_stays_ignored(response_service, monkeypatch):
    """A change Ignored by a reviewer is shown on the reviewed form as
    ParishSoft's value, which the Family leaves (no new row). The next form,
    drawn by the earlier rule, shows the Family's own answer; sent as drawn
    it commits and the successor carries Ignored and the baseline (the
    "baseline" branch of 0163's clause)."""
    harness, staff, _ = prepared(response_service)
    (change,) = rows_for("first_name")
    decide(harness, staff, [change], "ignore")
    again(harness)
    assert latest().reviewed_view
    (kept,) = rows_for("first_name")
    assert (kept.pk, kept.decision) == (change.pk, "ignored")
    monkeypatch.setattr(baselines, "_shown", lambda *args: (False, None))
    again(harness)
    old, new = rows_for("first_name")
    assert (old.pk, old.superseded_by_id) == (kept.pk, new.pk)
    assert new.decision == "ignored"
    assert (new.baseline_available, new.baseline_value) == (
        kept.baseline_available,
        kept.baseline_value,
    )


def test_an_answer_set_back_withdraws_the_shown_edit(response_service, monkeypatch):
    """The Family sets the first name back to ParishSoft's value: the shown
    change is withdrawn (cancelled), with no new row."""
    harness, kept = shown_edit(response_service, monkeypatch)
    again(harness, first_name=kept.baseline_value)
    (row,) = rows_for("first_name")
    assert (row.pk, row.execution) == (kept.pk, "cancelled")


def test_the_do_block_refuses_the_earlier_body():
    """The file's closing check passes as installed and raises once the
    derived guard is put back to the body it replaced; on that body the
    whole file installs again, with no definer."""
    text = FROZEN.read_text(encoding="utf-8")
    captured = text[
        text.index("SELECT set_config(") : text.index("CREATE OR REPLACE FUNCTION")
    ]
    check = text[text.index("DO $check$") :]
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(captured)
        cursor.execute(check)
        raise Rollback
    for name, earlier in EARLIER.items():
        with (
            pytest.raises(DatabaseError, match="does not find a change"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(captured)
            cursor.execute(body(name, SCHEMA / "migrations" / earlier))
            cursor.execute(check)
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        for name, earlier in EARLIER.items():
            cursor.execute(body(name, SCHEMA / "migrations" / earlier))
        cursor.execute(text)
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef AND proname = ANY(%s)",
            [list(EARLIER)],
        )
        assert cursor.fetchone() == (len(EARLIER),)
        raise Rollback


def test_the_file_adds_only_the_two_shown_clauses():
    """The replaced body is 0160's with only the #1909 edits: removing the
    two added clauses (each from its comment to its closing ``ELSE false
    END)``) gives 0160's body back exactly, comments included."""
    for name, earlier in EARLIER.items():
        new = body(name, FROZEN)
        added = re.findall(
            r"\n *-- A change the prior census response, drawn as the reviewed\n"
            r".*?ELSE false END\)",
            new,
            flags=re.S,
        )
        assert len(added) == 2
        for clause in added:
            new = new.replace(clause, "", 1)
        assert new == body(name, SCHEMA / "migrations" / earlier)
    text = FROZEN.read_text(encoding="utf-8")
    # No definer is made (the header's comment names the word).
    assert not re.search(r"^\s*[^-\s].*SECURITY DEFINER", text, re.M)
