"""Each setup test is bound to its own inputs (#1578), as SQL enforces it.

Migration 0169 (``schema/migrations/0169_setup_test_binding.sql``) replaces
the email and Slack live checks without their joins on a succeeded parish
data load and on the exact draft version, and the readiness guard with the
tests bound to the staged credential and the settings sealed with it. These
tests check the file's own DO block against the bodies it replaces.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0169_setup_test_binding.sql"


def earlier(file, name):
    """``name``'s CREATE OR REPLACE text in an earlier frozen file."""
    text = (MIGRATIONS / file).read_text(encoding="utf-8")
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    tag = re.search(r" AS (\$[A-Za-z_]*\$)", text[start:])[1]
    body = text.index(tag, start + text[start:].index(" AS ") + 4) + len(tag)
    return text[start : text.index(tag + ";", body) + len(tag) + 1]


# The bodies 0169 replaces: 0102's live checks and 0055's readiness guard.
OLD = {
    "stewardship_setup_mail_live_v1": "0102_setup_load_void.sql",
    "stewardship_setup_slack_live_v1": "0102_setup_load_void.sql",
    "stewardship_setup_readiness_guard_v1": "0055_setup_users.sql",
}


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


@pytest.mark.parametrize("name", sorted(OLD))
def test_the_check_refuses_each_old_body_and_the_file_installs_the_new(name):
    """The DO block passes on the installed functions and fails once any one
    is the body 0169 replaces; the whole file, run over that body, installs
    the new one again."""
    text = FROZEN.read_text(encoding="utf-8")
    # The check compares the grants the file records before replacing.
    grants = re.search(r"^SELECT set_config\(.*?, true\);", text, re.M | re.S)[0]
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    old = earlier(OLD[name], name)
    assert "original.state='succeeded'" in old or "candidate_digest" in old
    with connection.cursor() as cursor:
        with transaction.atomic():
            cursor.execute(grants)
            cursor.execute(check)
        with (
            pytest.raises(DatabaseError, match="Migration 0169"),
            transaction.atomic(),
        ):
            cursor.execute(grants)
            cursor.execute(old)
            cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(old)
            cursor.execute(text)
            cursor.execute(
                "SELECT strpos(prosrc, 'attempt.version=$2')=0"
                " AND strpos(prosrc, 'original.state=''succeeded''')=0"
                " FROM pg_proc WHERE proname=%s",
                [name],
            )
            assert cursor.fetchone() == (True,)
            raise Rollback
