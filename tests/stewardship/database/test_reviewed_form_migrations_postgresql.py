"""The reviewed Family form's frozen files 0153 to 0155 verify themselves (#1705).

Each file ends with a DO block that refuses to commit unless its new
bodies are installed. Run against the definitions they replace, each block
refuses; the file then installs them and the block passes.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
# Each file, and each function it replaces with the earlier file holding its
# previous body.
FILES = {
    "0153_reviewed_census_resubmission.sql": (
        (
            "stewardship_response_derived_guard_v1",
            "0146_census_terminal_undo.sql",
        ),
        ("stewardship_submission_effects_v1", "0128_census_pipeline_review.sql"),
    ),
    "0154_reviewed_ministry_resubmission.sql": (
        ("stewardship_ministry_request_guard_v1", "0148_ministry_terminal_undo.sql"),
        (
            "stewardship_ministry_submission_effects_v1",
            "0131_ministry_review_pipeline.sql",
        ),
    ),
    "0155_reviewed_pledge_resubmission.sql": (
        ("stewardship_pledge_review_states_v1", "0127_pledge_review.sql"),
    ),
}


def statement(path, name):
    """The CREATE [OR REPLACE] FUNCTION statement for ``name`` in ``path``."""
    text = (MIGRATIONS / path).read_text(encoding="utf-8")
    match = re.search(rf"^CREATE (OR REPLACE )?FUNCTION public\.{name}\(", text, re.M)
    tag = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.start())
    end = text.index(tag[1] + ";", tag.end()) + len(tag[1]) + 1
    found = text[match.start() : end]
    return found.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def check_block(path):
    """A frozen file's closing DO block."""
    text = (MIGRATIONS / path).read_text(encoding="utf-8")
    return text[text.index("DO $check$") :]


@pytest.mark.parametrize(("path", "replaced"), list(FILES.items()))
def test_each_block_refuses_the_old_bodies_and_the_file_installs(path, replaced):
    """The block refuses each earlier body; the file installs the new ones."""
    for name, earlier in replaced:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement(earlier, name))
            with (
                pytest.raises(DatabaseError, match=f"{name} lacks the reviewed form"),
                transaction.atomic(),
            ):
                cursor.execute(check_block(path))
            cursor.execute((MIGRATIONS / path).read_text(encoding="utf-8"))
            cursor.execute(check_block(path))
            transaction.set_rollback(True)
    with connection.cursor() as cursor:
        cursor.execute(check_block(path))
