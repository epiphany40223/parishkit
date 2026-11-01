"""The shared Family name functions name Families as Python does.

``stewardship_family_display_name_v1`` and the functions it is built from
(``stewardship_name_strip_v1``, ``stewardship_family_surname_v1`` and
``stewardship_family_head_part_v1``, migration 0045, and 0194's
``stewardship_family_last_name_v1`` and ``stewardship_family_heads_name_v2``;
0194 replaces the lookup and the head part for mixed surnames, #2320) are
the SQL twins of ``family_names`` and ``snapshot_names``. Each test seeds
Families into the current snapshot and compares the SQL name with
``snapshot_family_names`` (and the expected text, where a case states it), so
the two cannot drift, Unicode white space included (#960, #932).
"""

import itertools
import json
import re
import sys
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.source.family_names import family_display_name
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.source.snapshot_names import snapshot_family_names

from .family_name_cases import BASE, DEFAULT, NAME_CASES, member, seed_name_cases
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

# White space Python's str.strip() removes, padding names in the cases below:
# ASCII controls, the information separators, NEL, no-break and ideographic
# spaces. PostgreSQL's plain btrim() would keep all but the space.
PAD = "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\x85\xa0       　"

# Family payloads: each surname source, padding, a blank and a non-string
# value (Python's _text treats a number as blank), and no name at all.
FAMILIES = (
    {"lastName": PAD + "Lee" + PAD},
    {"lastName": PAD, "mailingName": " The Lees\t"},
    {"lastName": 7, "mailingName": " ", "firstName": " Pat "},
    {"lastName": "", "firstName": "Pat", "mailingName": None},
    {"lastName": "Lee", "mailingName": "Ann Smith"},
    {"firstName": " "},
)

# Head lists, each {offset: Member payload}: shared and other surnames, an
# inactive head (and one whose active flag is the string "true"), a blank
# first name, a first name only, a padded surname, and none.
HEADS = (
    {},
    {1: member(PAD + "Ann", "Lee" + PAD), 2: member("Bob", "Lee")},
    {1: member("Ann", "Lee"), 2: member("Jeff", "Smith"), 3: member(" ", "Lee")},
    {1: member("Ann", "Lee", active=False), 2: {"firstName": "Cy", "active": "true"}},
    {1: member("Di", ""), 2: member("", "Ray"), 3: member("Eve", "Lee")},
    {
        1: member("Ann", "Lee"),
        2: member("Bob", "Lee"),
        3: member("Cy", "Fox"),
        4: member("Di", "Lee"),
    },
)


def sql_names(snapshot_id, duids, default):
    """{Family DUID: stewardship_family_display_name_v1}, read as the web role."""
    with task_login(ServiceRole.WEB), work_transaction(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT d, stewardship_family_display_name_v1(%s, d, %s) "
            "FROM unnest(%s::bigint[]) d",
            [snapshot_id, default, list(duids)],
        )
        return dict(cursor.fetchall())


def generated_cases():
    """Every FAMILIES x HEADS pair, with fresh Member DUIDs for its heads."""
    cases = []
    for number, (family, heads) in enumerate(itertools.product(FAMILIES, HEADS)):
        start = BASE + 20_000 + number * 10
        heads = {start + offset: values for offset, values in heads.items()}
        cases.append((f"generated {number}", family, heads, None))
    return cases


def test_display_name_matches_python_on_every_shared_edge_case(response_service):
    """The lookup and snapshot_family_names agree on each NAME_CASES entry,
    and both give the name the case expects."""
    snapshot_id = SourceCurrent.objects.get().snapshot_id
    expected = seed_name_cases(snapshot_id)
    labels = {BASE + 1000 + offset: case[0] for offset, case in enumerate(NAME_CASES)}
    python = snapshot_family_names(snapshot_id, list(expected), DEFAULT)
    sql = sql_names(snapshot_id, expected, DEFAULT)
    for duid, name in expected.items():
        assert (labels[duid], python[duid]) == (labels[duid], name)
        assert (labels[duid], sql[duid]) == (labels[duid], name)


@pytest.mark.parametrize("default", [DEFAULT, ""])
def test_display_name_matches_python_on_generated_names(response_service, default):
    """Every combination of surname source and head list names the Family as
    snapshot_family_names does, with a fallback name or an empty one."""
    snapshot_id = SourceCurrent.objects.get().snapshot_id
    cases = generated_cases()
    duids = seed_name_cases(snapshot_id, cases, first=BASE + 5000)
    python = snapshot_family_names(snapshot_id, list(duids), default)
    assert sql_names(snapshot_id, duids, default) == python
    # The cases do reach the edges: padding trimmed, a fallback, heads joined.
    assert "Lee, Ann and Bob" in python.values()
    assert "The Lees" in python.values() and "Pat" in python.values()
    assert any(
        name.endswith(" / Fox, Ann, Bob and Di / Cy") for name in python.values()
    )


def test_display_name_of_a_family_outside_the_snapshot_is_null(response_service):
    """A DUID the snapshot does not have gives NULL, not the fallback."""
    snapshot_id = SourceCurrent.objects.get().snapshot_id
    assert sql_names(snapshot_id, [BASE + 9999], DEFAULT) == {BASE + 9999: None}


def test_display_name_treats_non_array_heads_as_none(response_service):
    """A malformed active_head_duids names the Family by surname alone
    instead of failing the whole query (Python cannot read these at all)."""
    snapshot_id = SourceCurrent.objects.get().snapshot_id
    cases = (
        (
            "heads as a string",
            {"lastName": "Lee", "active_head_duids": "12"},
            {},
            "Lee",
        ),
        (
            "heads as an object",
            {"lastName": "Ray", "active_head_duids": {"a": 1}},
            {},
            "Ray",
        ),
        ("heads as a number", {"lastName": "Fox", "active_head_duids": 5}, {}, "Fox"),
    )
    expected = seed_name_cases(snapshot_id, cases, first=BASE + 8000)
    assert sql_names(snapshot_id, expected, "") == expected


def test_surname_matches_family_display_name(response_service):
    """stewardship_family_surname_v1 is family_display_name, payload by payload."""
    payloads = [*FAMILIES, *(case[1] for case in NAME_CASES), {}, {"lastName": None}]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_family_surname_v1(e.family, %s) "
            "FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY e(family, n) "
            "ORDER BY e.n",
            [DEFAULT, json.dumps(payloads)],
        )
        sql = [row[0] for row in cursor.fetchall()]
    assert sql == [family_display_name(values, DEFAULT) for values in payloads]


def test_name_strip_removes_exactly_what_str_strip_removes(response_service):
    """Over every code point, stewardship_name_strip_v1 trims a character
    exactly when Python's str.strip() does (the btrim-versus-Unicode gap)."""
    python = {code for code in range(1, sys.maxunicode + 1) if not chr(code).strip()}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT array_agg(c ORDER BY c) FROM generate_series(1, 1114111) c "
            "WHERE c NOT BETWEEN 55296 AND 57343 "
            "AND stewardship_name_strip_v1(chr(c)) = ''"
        )
        sql = set(cursor.fetchone()[0])
    assert sql == python


MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"


class Rollback(Exception):
    """Undoes a transaction.atomic() block that passed."""


def statement(path, name):
    """One function's CREATE statement in a schema file, up to its closing $$."""
    text = (MIGRATIONS / path).read_text(encoding="utf-8")
    found = re.search(rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", text, re.M)
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, found.start())
    end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    return re.sub(
        r"^CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", text[found.start() : end]
    )


def test_0194_check_refuses_each_older_definition():
    """0194's DO block passes on the installed functions and refuses each
    older body: 0045's head part, heads name (the inline "Surname, A and B
    Smith" form) and display name lookup, 0094's directory selections, and
    0182's and 0175's Financial and Additional information selections; it
    passes again once 0194's statements are run over them. The baseline
    selections are 0194's."""
    frozen = "migrations/0194_mixed_surname_names.sql"
    text = (MIGRATIONS / frozen).read_text(encoding="utf-8")
    acl = re.search(
        r"^SELECT set_config\('stewardship.migration_0194_acl'.*?;$", text, re.M | re.S
    )[0]
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    older = {
        "stewardship_family_head_part_v1": "migrations/0045_report_duid_sorts.sql",
        "stewardship_family_heads_name_v1": "migrations/0045_report_duid_sorts.sql",
        "stewardship_family_display_name_v1": "migrations/0045_report_duid_sorts.sql",
        "stewardship_directory_report_v1": "migrations/0094_source_status_names.sql",
        "stewardship_directory_report_v2": "migrations/0094_source_status_names.sql",
        "stewardship_financial_report_v1": "migrations/0182_financial_in_force.sql",
        "stewardship_information_report_v1": "migrations/0175_campaign_families.sql",
        "stewardship_information_report_v2": "migrations/0175_campaign_families.sql",
    }
    with connection.cursor() as cursor:
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(acl)
            cursor.execute(check)
            raise Rollback
        for name, path in older.items():
            old, new = statement(path, name), statement(frozen, name)
            assert old != new
            with (
                pytest.raises(DatabaseError, match="Migration 0194"),
                transaction.atomic(),
            ):
                cursor.execute(acl)
                cursor.execute(old)
                cursor.execute(check)
            with pytest.raises(Rollback), transaction.atomic():
                cursor.execute(acl)
                cursor.execute(old)
                cursor.execute(new)
                cursor.execute(check)
                raise Rollback
    for path, name in (
        ("directory_reports.sql", "stewardship_directory_report_v1"),
        ("financial_reports.sql", "stewardship_financial_report_v1"),
        ("information_reports.sql", "stewardship_information_report_v1"),
    ):
        assert statement(path, name) == statement(frozen, name)
