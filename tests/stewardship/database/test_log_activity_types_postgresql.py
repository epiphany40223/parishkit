"""The trigger-built types System logs' Activity groups list (#953).

``audit.log_descriptions.BUILT_TYPES`` names, for each prefix a trigger
builds audit types from, the values the installed database allows: a CHECK
constraint, or the guard function that refuses any other value. This compares
each list with the migrated database, so a group cannot name a type that is
never written, and a new state or action fails here until it is placed.
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


def _allowed(kind, name):
    """The values one closing source admits: a CHECK constraint's list, a
    ``CASE NEW.action`` guard's WHEN arms, or a held-email command's
    allowed decisions."""
    if kind == "constraint":
        return _constraint(name)
    body = _function(name)
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
        admitted = _allowed(kind, name)
        assert admitted, (prefix, name)
        found |= admitted
    assert found == set(values)
