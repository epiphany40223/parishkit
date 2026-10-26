"""Migration 0180: undoing an added request's decision reopens it (#2173).

The Ministry review tests (``test_ministry_review_postgresql``) show Back to
review sending a request the parish added to To review, Ignore dropping it,
and a Family response leaving it in force, through the service and so the
SQL guards. Here the file itself is tested: its closing check passes as
installed and refuses each replaced function's previous body, each new
body is its previous one with only the intended clause changed and its
operators qualified (#2166), and the file installs its own bodies again
over the previous ones.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from ..sql_operators import bare_operators

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0180_added_undo_to_review.sql"
# Each replaced function and the frozen file holding the body 0180 copied.
PREVIOUS = {
    "stewardship_ministry_revision_guard_v1": "0158_ignored_note_optional.sql",
    "stewardship_ministry_submission_effects_v1": (
        "0154_reviewed_ministry_resubmission.sql"
    ),
}
# Per function, the one clause 0180 changed (normalized, see below): the
# previous body's text and what replaced it. The previous body with exactly
# that clause swapped must equal the new body, so any other change to a
# copied body (say, a rebase onto a newer predecessor) fails the test.
CHANGED = {
    "stewardship_ministry_revision_guard_v1": (
        "NEW.state<>CASE WHEN target.parish_added THEN 'cancelled'"
        " ELSE 'in_progress' END",
        "NEW.state<>'in_progress'",
    ),
    "stewardship_ministry_submission_effects_v1": (
        "IF prior.state IN ('new','assigned','in_progress') AND NOT (",
        "IF prior.state IN ('new','assigned','in_progress')"
        " AND NOT prior.parish_added AND NOT (",
    ),
}
# The qualified operator form (#2166), turned back to the bare operator to
# compare a new body with its previous one.
QUALIFIED = re.compile(r"OPERATOR\(pg_catalog\.([^)]+)\)")


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def statement(text, name):
    """The ``CREATE OR REPLACE FUNCTION`` statement for ``name`` in ``text``."""
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}()")
    return text[start : text.index("$$;", start) + len("$$;")]


def previous(name):
    """``name``'s body before 0180, from the file it was copied from."""
    return statement((MIGRATIONS / PREVIOUS[name]).read_text(encoding="utf-8"), name)


def check():
    """0180's closing check: the grants it records first, and its DO block."""
    text = FROZEN.read_text(encoding="utf-8")
    start = text.index("SELECT set_config(")
    # The recording statement ends at its own "true);", before the comment
    # naming the first replaced function.
    recorded = text[start : text.index(", true);", start) + len(", true);")]
    return recorded + "\n" + text[text.index("DO $check$") :]


def normalized(body):
    """``body`` without its ``--`` comments, its white space collapsed.

    Neither body has ``--`` inside a string literal, so stripping to the
    line end is safe here; comments may change freely, code may not.
    """
    return " ".join(re.sub(r"--[^\n]*", "", body).split())


def test_the_check_passes_as_installed():
    """A fresh install has both new bodies, so the closing check passes."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check())
        raise Rollback


@pytest.mark.parametrize("name", sorted(PREVIOUS))
def test_the_check_refuses_each_previous_body(name):
    """Either function put back to its body before 0180 fails the check."""
    body = previous(name)
    assert "(#2173)" not in body
    with (
        pytest.raises(DatabaseError, match=f"Migration 0180 did not replace {name}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(body)
        cursor.execute(check())


def test_each_new_body_is_its_previous_one_with_one_clause_changed():
    """Only the intended clause (and comments) changed in each body, besides
    its operators, which are all qualified (#2166)."""
    assert set(CHANGED) == set(PREVIOUS)
    text = FROZEN.read_text(encoding="utf-8")
    for name, (removed, added) in CHANGED.items():
        body = statement(text, name)
        assert "(#2173)" in body, name
        assert "OPERATOR(pg_catalog." in body, name
        assert not bare_operators(body), name
        new = normalized(QUALIFIED.sub(r"\1", body))
        old = normalized(previous(name))
        assert old.count(removed) == 1, name
        assert old.replace(removed, added) == new, name


def test_the_file_installs_its_bodies_over_the_previous_ones():
    """On a database with both previous bodies, the file installs its own and
    its check passes; rolled back."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        for name in PREVIOUS:
            cursor.execute(previous(name))
        cursor.execute(FROZEN.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef"
            " AND prosrc LIKE '%%(#2173)%%' AND proname = ANY(%s)",
            [list(PREVIOUS)],
        )
        assert cursor.fetchone() == (2,)
        raise Rollback
