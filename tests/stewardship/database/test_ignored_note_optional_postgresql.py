"""Migration 0158: reopening an Ignored item no longer needs a note (#1889).

Each stream's own tests show Back to review from Ignored recording with no
note, through its service and so through its SQL guard (census review and
resolution, Ministry requests, pledges, Additional information and
Limitations), while a terminal step's undo still needs one. Here the file
itself is tested: its closing check passes as installed and refuses each
guard's previous body (the one the file copied, with its Ignored-note
check), each new body is its previous one with only that clause changed,
and the file installs its own bodies again over all of them.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0158_ignored_note_optional.sql"
# Each replaced guard and the frozen file holding the body 0158 copied.
PREVIOUS = {
    "stewardship_proposal_review_guard_v1": "0130_census_address_guard.sql",
    "stewardship_proposal_resolution_guard_v1": "0210_census_terminal_undo.sql",
    "stewardship_ministry_revision_guard_v1": "0212_ministry_terminal_undo.sql",
    "stewardship_pledge_review_guard_v1": "0213_pledge_terminal_undo.sql",
    "stewardship_information_revision_guard_v1": "0214_information_handled_undo.sql",
    "stewardship_limitation_review_guard_v1": "0215_limitation_handled_undo.sql",
}


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def statement(text, name):
    """The ``CREATE OR REPLACE FUNCTION`` statement for ``name`` in ``text``."""
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}()")
    return text[start : text.index("$$;", start) + len("$$;")]


def previous(name):
    """``name``'s body before 0158, from the file it was copied from."""
    return statement((MIGRATIONS / PREVIOUS[name]).read_text(encoding="utf-8"), name)


def check():
    """0158's closing check: the grants it records first, and its DO block."""
    text = FROZEN.read_text(encoding="utf-8")
    start = text.index("SELECT set_config(")
    recorded = text[start : text.index("\nCREATE OR REPLACE FUNCTION", start)]
    return recorded + text[text.index("DO $check$") :]


def test_the_check_passes_as_installed():
    """A fresh install has every new body, so the closing check passes."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check())
        raise Rollback


@pytest.mark.parametrize("name", sorted(PREVIOUS))
def test_the_check_refuses_each_previous_body(name):
    """Any one guard put back to its body before 0158 fails the check."""
    body = previous(name)
    assert "(#1889)" not in body
    with (
        pytest.raises(DatabaseError, match="Migration 0158 .* did not replace"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(body)
        cursor.execute(check())


def normalized(body):
    """``body`` without its ``--`` comments, its white space collapsed.

    None of the six bodies has ``--`` inside a string literal, so stripping
    to the line end is safe here; comments may change freely, code may not.
    """
    return " ".join(re.sub(r"--[^\n]*", "", body).split())


# Per guard, the one clause 0158 changed (normalized): the previous body's
# Ignored-note check and what replaced it. Each previous body with exactly
# that clause swapped must equal the new body, so any other change to a
# copied body (say, a rebase onto a newer predecessor) fails the test.
NOTE = "'\\S'"
CHANGED = {
    "stewardship_proposal_review_guard_v1": (
        " OR (proposal.decision='ignored' AND NEW.decision='unreviewed'"
        f" AND NEW.note !~ {NOTE})",
        "",
    ),
    # The only blank-note refusal left is a terminal step's undo.
    "stewardship_proposal_resolution_guard_v1": (
        f"(NEW.action='reopened' AND NEW.note !~ {NOTE})",
        f"(undo AND NEW.note !~ {NOTE})",
    ),
    "stewardship_ministry_revision_guard_v1": (
        " OR (target.outcome NOT IN ('joined','leave_confirmed')"
        f" AND NEW.decision_note !~ {NOTE})",
        "",
    ),
    "stewardship_pledge_review_guard_v1": (
        f"NEW.decision IS NULL AND NEW.note ~ {NOTE} WHEN",
        "NEW.decision IS NULL WHEN",
    ),
    "stewardship_information_revision_guard_v1": (
        " IF NEW.review_decision<>'ignored'"
        f" AND (NEW.notes !~ {NOTE} OR NEW.notes=prior.notes)"
        " AND prior.id IS NOT NULL AND NOT item.follow_up_needed"
        " AND item.followed_up_at IS NULL"
        " AND coalesce(prior.review_decision,'ignored')='ignored'"
        " THEN RAISE EXCEPTION 'Reopening an ignored request needs a note'"
        " USING ERRCODE='23514'; END IF;",
        "",
    ),
    "stewardship_limitation_review_guard_v1": (
        f"NEW.decision='none' AND NEW.note ~ {NOTE} WHEN",
        "NEW.decision='none' WHEN",
    ),
}


def test_each_new_body_is_its_previous_one_less_the_ignored_note():
    """Only the Ignored-note clause (and comments) changed in each body."""
    assert set(CHANGED) == set(PREVIOUS)
    text = FROZEN.read_text(encoding="utf-8")
    for name, (removed, added) in CHANGED.items():
        new, old = normalized(statement(text, name)), normalized(previous(name))
        assert "(#1889)" in statement(text, name), name
        assert old.count(removed) == 1, name
        assert old.replace(removed, added) == new, name


def test_the_file_installs_its_bodies_over_the_previous_ones():
    """On a database with every previous body, the file installs its own and
    its check passes; rolled back."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        for name in PREVIOUS:
            cursor.execute(previous(name))
        cursor.execute(FROZEN.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef"
            " AND prosrc LIKE '%%(#1889)%%' AND proname = ANY(%s)",
            [list(PREVIOUS)],
        )
        assert cursor.fetchone() == (6,)
        raise Rollback
