"""The trigger-built types System logs' Activity groups list (#953).

``audit.log_descriptions.BUILT_TYPES`` names, for each prefix a trigger
builds audit types from, the values the installed database allows: a CHECK
constraint, or the guard function that refuses any other value. This compares
each list with the migrated database, so a group cannot name a type that is
never written, and a new state or action fails here until it is placed. It
also scans every installed function that writes audit records for a
``'<prefix>_'||`` construction, so a new trigger-built family of types cannot
escape ``BUILT_TYPES`` (and so the written-types guard in test_log_rows).
"""

import re

import pytest
from django.db import connection

from parishkit.stewardship.audit.log_descriptions import BUILT_TYPES

pytestmark = pytest.mark.django_db


def _constraint(name):
    """The quoted values one CHECK constraint allows, from its definition."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE contype='c' AND conname=%s",
            [name],
        )
        (definition,) = cursor.fetchone()
    return set(re.findall(r"'([a-z_]+)'", definition))


def _function(name):
    """The installed body of one guard function."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname=%s",
            [name],
        )
        (body,) = cursor.fetchone()
    return body


# Text concatenations in audit-writing functions that build something other
# than an audit type, with what each builds. Each must still be found, so a
# stale entry cannot hide a prefix.
NOT_AUDIT_TYPES = {
    "admin_": "a delivery resolution's reason code",
    "pk_stewardship_": "a credential consumer's database role name",
}


def _allowed(kind, name, prefix):
    """The values one closing source admits: a CHECK constraint's list, a
    ``CASE NEW.action`` guard's WHEN arms, a held-email command's allowed
    decisions, or the literal arms of the type an audit trigger builds."""
    if kind == "constraint":
        return _constraint(name)
    body = _function(name)
    if name == "stewardship_setup_mail_audit_v1":
        # event='setup_mail_' || CASE WHEN <scrubbed> THEN 'scrubbed' ELSE
        # NEW.state END: the literal arms add to the state constraint.
        built = re.search(rf"'{prefix}'\s*\|\|\s*CASE(.*?)\bEND\b", body, re.S)
        return set(re.findall(r"THEN '([a-z_]+)'", built.group(1)))
    if name == "stewardship_delivery_control_guard_v1":
        # A held-email command's decision outside this list is refused.
        listed = re.search(
            r"selection->>'decision'\s*,\s*''\s*\)\s*NOT IN \(([^)]*)\)", body
        )
        return set(re.findall(r"'([a-z_]+)'", listed.group(1)))
    # CASE NEW.action WHEN 'pause' ... WHEN 'first_delivery','first_submission'
    # ... ELSE RAISE: the WHEN arms are the whole list.
    arms = re.findall(r"WHEN ('[a-z_]+'(?:\s*,\s*'[a-z_]+')*) THEN", body)
    return {value for arm in arms for value in re.findall(r"'([a-z_]+)'", arm)}


@pytest.mark.parametrize("prefix", sorted(BUILT_TYPES))
def test_each_built_list_is_the_databases(prefix):
    """Each prefix's list is exactly what its closing sources admit
    together, so a new state or action fails here until it is placed."""
    sources, values = BUILT_TYPES[prefix]
    found = set()
    for kind, name in sources:
        admitted = _allowed(kind, name, prefix)
        assert admitted, (prefix, name)
        found |= admitted
    assert found == set(values)


def test_every_built_audit_prefix_is_closed():
    """Every ``'<prefix>_'||`` an installed function that inserts audit
    records builds is a ``BUILT_TYPES`` prefix (or a known non-type), so the
    written-types guard cannot pass while a trigger writes types it never
    heard of (the setup_mail_, setup_slack_ and task_ gap on #980)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.proname, pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.prokind='f' "
            "AND p.prosrc ~ 'INSERT INTO (public\\.)?stewardship_audit_event'"
        )
        functions = cursor.fetchall()
    built = {}
    for name, body in functions:
        for prefix in re.findall(r"'([a-z][a-z0-9_]*_)'\s*\|\|", body):
            built.setdefault(prefix, set()).add(name)
    # The scan cannot pass vacuously.
    assert {"config_request_", "setup_mail_", "task_"} <= set(built)
    unclosed = set(built) - set(BUILT_TYPES) - set(NOT_AUDIT_TYPES)
    assert not unclosed, {prefix: sorted(built[prefix]) for prefix in unclosed}
    assert set(NOT_AUDIT_TYPES) <= set(built), sorted(set(NOT_AUDIT_TYPES) - set(built))
