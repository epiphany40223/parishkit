"""An unchanged Family resubmission and strict phase order (#1895), in PostgreSQL.

Strict phase order (#1890) keeps an open census change ParishSoft already
holds in its stage until its review and step. A Family that opens its form
again then sees ParishSoft's value, and submitting it unchanged is rule 1 of
the review pipeline's resubmission rules (docs/specs/stewardship/
review-pipeline/spec.md, "Resubmission before the reviewed form"): the
change keeps its stage and decision, with no new row, where it used to be
resolved upstream (Confirmed) with no review and no step. Frozen migration
0160 (``schema/migrations/0160_resubmission_phase_order.sql``) makes the census
guard and the submission's completeness check hold the same rule. These
tests submit real Family responses after real refreshes, force the old
derivation past the code to show the database refuses it, and check the
file's DO block against the bodies it replaces.
"""

import re

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.pipeline import Decision, Stage
from parishkit.stewardship.responses import proposals
from parishkit.stewardship.responses.effective import CLOSED_EXECUTIONS
from parishkit.stewardship.web.review_worklist import ALREADY_HELD

from .test_census_pipeline_review_postgresql import decide
from .test_census_resubmission_postgresql import again, rows_for
from .test_refresh_checks_postgresql import census_row, prepared, refresh
from .test_strict_phase_order_postgresql import SCHEMA, Rollback, body, stage_note

pytestmark = pytest.mark.django_db(transaction=True)
# Most tests here test the rule before the reviewed Family form (#1705):
# forms drawn before its release, as one open across the deploy is (and
# every Testing form).
earlier = pytest.mark.usefixtures("earlier_rule")

FROZEN = SCHEMA / "migrations/0160_resubmission_phase_order.sql"
# Each function 0160 replaces, and the file holding the body it replaces.
EARLIER = {
    "stewardship_response_derived_guard_v1": "0159_strict_phase_order.sql",
    "stewardship_submission_effects_v1": "0216_reviewed_census_resubmission.sql",
}
# ParishSoft holding both of the fixture's answers (Member 3's first name
# and prefix, which the Family answered "Requested" and "Dr.").
HELD = {"firstName": "Requested", "salutation": "Dr."}


def held(harness):
    """The fixture's two changes once a refresh finds ParishSoft holding
    both: still To review, unreviewed and open (#1890)."""
    harness, staff, rows = prepared(harness)
    refresh(harness, member=HELD)
    return harness, staff, rows


def only(field):
    """The one census row of ``field``, which no response has replaced."""
    (row,) = rows_for(field)
    return row


@earlier
def test_an_unchanged_answer_keeps_a_change_parishsoft_holds(response_service):
    """The Family submits its form again unchanged, twice, while ParishSoft
    holds both answers: each change keeps its stage (To review), decision
    and version, and no row is made. Accepted, they are Ready to update and
    To enter by hand, and stay so through another unchanged submit, each
    still saying ParishSoft already holds the value. Before #1895 the first
    submit resolved both upstream (Confirmed)."""
    harness, staff, rows = held(response_service)
    versions = {field: only(field).version for field in rows}
    again(harness)
    again(harness)
    for field in ("first_name", "prefix"):
        kept = only(field)
        assert (kept.decision, kept.execution) == ("unreviewed", "pending")
        assert kept.version == versions[field]
        assert census_row(harness, kept)["stage"] == Stage.TO_REVIEW
        assert stage_note(harness, kept) == ALREADY_HELD
    decide(harness, staff, [only("first_name"), only("prefix")], "accept")
    again(harness)
    for field, stage in (
        ("first_name", Stage.READY_TO_UPDATE),
        ("prefix", Stage.TO_ENTER_BY_HAND),
    ):
        kept = only(field)
        assert (kept.decision, kept.execution) == ("approved", "pending")
        row = census_row(harness, kept)
        assert row["stage"] == stage
        assert row["placement"].decision is Decision.ACCEPTED
        assert stage_note(harness, kept) == ALREADY_HELD


@earlier
def test_an_unchanged_answer_keeps_a_reviewers_edit(response_service):
    """A reviewer Edits the change, ParishSoft holds the Family's own
    answer, and the Family submits its older (not reviewed) form unchanged:
    the change is kept as accepted with the Edit, not resolved upstream
    (Confirmed), which would silently drop the Edit."""
    harness, staff, rows = held(response_service)
    decide(harness, staff, [only("first_name")], "edit", value="Edited")
    again(harness)
    kept = only("first_name")
    assert (kept.decision, kept.execution) == ("approved", "pending")
    assert kept.admin_value == "Edited"


@earlier
def test_a_later_answer_replaces_the_kept_change(response_service):
    """A change kept on an earlier response is still the Family's current
    item: a later different answer supersedes it with a new change To
    review (one open item, not two), and the kept change is history."""
    harness, _, rows = held(response_service)
    kept = only("first_name")
    again(harness)
    again(harness, first_name="Changed again")
    old, new = rows_for("first_name")
    assert old.pk == kept.pk
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert new.submitted_value == "Changed again"
    assert (new.decision, new.execution) == ("unreviewed", "pending")


@earlier
def test_the_kept_change_carries_its_decision_once_parishsoft_moves(
    response_service,
):
    """An accepted change kept on an earlier response, which ParishSoft
    then moves away from (a Conflict), is the predecessor the census guard
    finds for the Family's same answer again: the successor carries its
    Accept and its baseline, as a successor on the response before does."""
    harness, staff, rows = held(response_service)
    decide(harness, staff, [only("first_name")], "accept")
    again(harness)
    kept = only("first_name")
    assert (kept.decision, kept.execution) == ("approved", "pending")
    refresh(harness, member=HELD | {"firstName": "Elsewhere"})
    assert only("first_name").execution == "conflict"
    again(harness)
    old, new = rows_for("first_name")
    assert old.pk == kept.pk
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert new.submitted_value == "Requested"
    assert (new.decision, new.execution) == ("approved", "conflict")
    assert (new.baseline_available, new.baseline_value) == (
        kept.baseline_available,
        kept.baseline_value,
    )


@earlier
def test_an_answer_set_back_to_parishsoft_still_withdraws(response_service):
    """Rule 3 is unchanged when the previous answer is not ParishSoft's:
    the Family sets the prefix back to ParishSoft's value, and the change is
    withdrawn (cancelled), not kept or confirmed. The unchanged first name,
    which ParishSoft does not hold, gets its successor as before."""
    harness, _, rows = prepared(response_service)
    again(harness, prefix=rows["prefix"].baseline_value)
    assert only("prefix").execution == "cancelled"
    old, new = rows_for("first_name")
    assert (old.execution, new.execution) == ("superseded", "pending")


def test_the_reviewed_form_withdraws_an_answer_parishsoft_holds(response_service):
    """The reviewed form's rule 3 (#1895): ParishSoft holds the Family's
    first answer, a reviewer's Edit is in force (the form shows it), and
    the Family types its first answer back. The change is withdrawn
    (cancelled), never Confirmed (it was resolved upstream before)."""
    harness, staff, rows = held(response_service)
    decide(harness, staff, [only("first_name")], "edit", value="Edited")
    assert only("first_name").admin_value == "Edited"
    again(harness, first_name="Requested")
    (row,) = rows_for("first_name")
    assert row.execution == "cancelled"


def old_rule(monkeypatch, outcome):
    """Force the Family derivation's outcome for an open change it would
    keep: ``outcome`` (``None`` keeps every open change, whatever the
    answer)."""
    found = proposals._replacement

    def forced(old, new, held, kind, key):
        """The real outcome, except as forced."""
        if old is None or old.execution in CLOSED_EXECUTIONS:
            return None
        if outcome is None:
            return None
        decided = found(old, new, held, kind, key)
        return outcome if decided is None else decided

    monkeypatch.setattr(proposals, "_replacement", forced)


@earlier
def test_the_census_guard_refuses_a_resubmission_confirmation(
    response_service, monkeypatch
):
    """A derivation that resolves a held change upstream at an unchanged
    submit, as before #1895, is refused by the census guard (a Family
    response only supersedes or withdraws) and the response rolls back."""
    harness, _, rows = held(response_service)
    before = {field: only(field).version for field in rows}
    old_rule(monkeypatch, "resolved_upstream")
    with pytest.raises(IntegrityError, match="new final Family response"):
        again(harness)
    for field in rows:
        assert (only(field).execution, only(field).version) == (
            "pending",
            before[field],
        )


@earlier
def test_the_completeness_check_refuses_a_change_kept_after_a_set_back(
    response_service, monkeypatch
):
    """The completeness check admits an open change left as it is only when
    the answer is its own and ParishSoft holds it: a derivation that keeps
    the prefix change although the Family set it back to ParishSoft's value
    (rule 3) is refused."""
    harness, _, rows = prepared(response_service)
    old_rule(monkeypatch, None)
    with pytest.raises(IntegrityError, match="reconcile earlier census work"):
        again(harness, prefix=rows["prefix"].baseline_value)
    assert only("prefix").execution == "pending"


@earlier
def test_the_completeness_check_finds_a_kept_change_left_beside_a_new_one(
    response_service, monkeypatch
):
    """A change kept on an earlier response is checked on later responses
    too: a derivation that makes a new change for a different answer but
    leaves the kept one open (two open items for one field) is refused."""
    harness, _, _ = held(response_service)
    again(harness)
    old_rule(monkeypatch, None)
    with pytest.raises(IntegrityError, match="reconcile earlier census work"):
        again(harness, first_name="Changed again")
    assert only("first_name").execution == "pending"


def test_the_do_block_refuses_each_earlier_body():
    """The file's closing check passes as installed and raises once either
    replaced function is put back to the body it replaced; on the earlier
    bodies the whole file installs again, with no definer among them."""
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
            pytest.raises(DatabaseError, match="The "),
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


def test_the_file_copies_each_body_it_replaces_from_the_newest():
    """Each replaced body is its newest earlier copy with only the #1895
    edits: every code line of the earlier body survives in order (comments
    may be reworded), except the one replacement-outcome line that no
    longer admits resolved_upstream and the two lookups' closing lines."""
    text = FROZEN.read_text(encoding="utf-8")
    for name, earlier in EARLIER.items():
        old = body(name, SCHEMA / "migrations" / earlier).splitlines()
        new = iter(body(name, FROZEN).splitlines())
        for line in old:
            if line.strip().startswith("--"):
                continue
            # The predecessor lookups gain an OR before their closing
            # parenthesis.
            if line.endswith("'death_date')))") and (
                "NEW.field" in line or "proposal.field" in line
            ):
                line = line[:-1]
            if "'superseded','cancelled','resolved_upstream'" in line:
                line = line.replace(",'resolved_upstream'", "")
            assert any(line == candidate for candidate in new), (name, line)
    # No definer is made (the header's comment names the word).
    assert not re.search(r"^\s*[^-\s].*SECURITY DEFINER", text, re.M)
