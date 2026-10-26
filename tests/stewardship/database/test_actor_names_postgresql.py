"""The acting Admin user's name and Timeline id, read once a page (#2259).

``actor_names`` (by account), ``address_actors`` (by an address a read
already joined) and ``ACTOR_USER_JOIN`` (inside that read's SQL) find the
same current user row: Name and record id, matched by address in lower
case, in one query however many actors, none for none.
"""

from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.actor_names import (
    ACTOR_USER_JOIN,
    FORMER,
    Actor,
    actor_names,
    actor_of,
    address_actors,
)

from ..policy_factory import user
from .campaign_builders import change
from .response_builders import activate_response_service
from .user_builders import add_rules, convert, identity, user_id

pytestmark = pytest.mark.django_db(transaction=True)

NAMED = "named@example.org"
# An account whose address is no user: it has no Name to show.
UNNAMED = "unnamed@example.org"
REMOVED = "removed@example.org"


@pytest.fixture
def accounts(response_service):
    """Three accounts: a named user (signed in with a mixed-case address), an
    account whose address is no user, and a removed user. Every user has a
    Name (``users_schema.valid_name``), so "no Name" is an account with no
    current user row. Returns ``(ids, records)``."""
    store = activate_response_service(response_service).service.store
    convert(store)
    add_rules(
        store,
        user(NAMED, role="staff", name="Adams, Ann"),
        user(REMOVED, role="staff", name="Gone, Greg"),
    )
    records = {email: UUID(user_id(store, email)) for email in (NAMED, REMOVED)}
    removal = {
        "operation": "remove",
        "section": "login_rules",
        "id": str(records[REMOVED]),
    }
    assert change(store, store.active(), uuid4(), [removal]).state == "applied"
    ids = {
        NAMED: identity("Named@Example.org").pk,
        UNNAMED: identity(UNNAMED).pk,
        REMOVED: identity(REMOVED).pk,
    }
    return ids, records


def test_one_query_names_every_actor(accounts):
    """Name, else address; a removed user keeps the address, unlinked; an
    account the portal no longer has is absent and words ``FORMER``."""
    ids, records = accounts
    gone = uuid4()
    with CaptureQueriesContext(connection) as queries:
        found = actor_names([*ids.values(), *ids.values(), gone, None, "x"])
    assert len(queries) == 1
    assert found[str(ids[NAMED])] == Actor(
        "Adams, Ann", "Named@Example.org", records[NAMED]
    )
    assert found[str(ids[UNNAMED])] == Actor(UNNAMED, UNNAMED, None)
    assert found[str(ids[REMOVED])] == Actor(REMOVED, REMOVED, None)
    assert str(gone) not in found
    assert actor_of(found, gone) == Actor(FORMER)
    with CaptureQueriesContext(connection) as queries:
        assert actor_names([None]) == {}
        assert address_actors([None, ""]) == {}
    assert len(queries) == 0


def test_the_address_read_and_the_sql_join_agree(accounts):
    """``address_actors`` and ``ACTOR_USER_JOIN`` find the same user rows."""
    ids, records = accounts
    addresses = ["Named@Example.org", UNNAMED, REMOVED, "nobody@example.org"]
    with CaptureQueriesContext(connection) as queries:
        found = address_actors(addresses)
    assert len(queries) == 1
    assert found["Named@Example.org"] == Actor(
        "Adams, Ann", "Named@Example.org", records[NAMED]
    )
    assert found[REMOVED] == Actor(REMOVED, REMOVED, None)
    assert found["nobody@example.org"].user_id is None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT u.id::text, uu.record_id, uu.name FROM stewardship_portal_user u "
            + ACTOR_USER_JOIN.format(alias="uu", user="u")
            + " WHERE u.id = ANY(%s)",
            [list(ids.values())],
        )
        joined = {key: (record, name) for key, record, name in cursor}
    named = actor_names(ids.values())
    for key, (record, name) in joined.items():
        assert named[key].user_id == record
        assert named[key].label == ((name or "").strip() or named[key].email)
