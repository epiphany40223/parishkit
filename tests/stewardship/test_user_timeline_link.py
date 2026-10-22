"""Links from a user's address to that user's Timeline (#1885).

Sent mail's Recipient and the Mail message page's To: addresses link a
current user's address to the user's Timeline (``admin:user_timeline``) by
the user's record id, never the address, for viewers who may open it
(``MANAGE_USERS``). These tests check the shared tag's decision and the
reads' shaping without a database; the PostgreSQL tests check the pages.
"""

from types import SimpleNamespace
from uuid import UUID

import pytest
from django.template import Context, Template

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.jobs import delivery_reads

USER = UUID(int=1885)
TIMELINE = f"/admin/users/{USER}/timeline/"


def viewer(*roles):
    """A request whose admitted principal holds ``roles``."""
    return SimpleNamespace(principal=Principal(UUID(int=1), frozenset(roles)))


def draw(request, user_id):
    """The tag's answer for ``user_id`` as ``request``'s viewer sees it."""
    template = Template("{% load stewardship %}{% user_timeline_url user_id %}")
    return template.render(Context({"request": request, "user_id": user_id}))


def test_an_administrator_gets_the_users_timeline():
    """The address carries the record id only."""
    assert draw(viewer("administrator"), USER) == TIMELINE


@pytest.mark.parametrize(
    "request_",
    [viewer("staff"), viewer("ministry_leader"), SimpleNamespace(), None],
    ids=["staff", "leader", "no-principal", "no-request"],
)
def test_no_link_for_a_viewer_who_may_not_open_it(request_):
    """Only MANAGE_USERS (Administrators) may open a user's Timeline."""
    assert draw(request_, USER) == ""


def test_no_link_without_a_user():
    """An address that is no current user's draws no link."""
    assert draw(viewer("administrator"), None) == ""


def test_names_come_from_the_same_users_read(monkeypatch):
    """administrator_names keeps only the users with a Name, from the
    users read it is given (so a caller holding it reads nothing again)."""
    monkeypatch.setattr(delivery_reads, "address_users", pytest.fail)
    users = {"a@example.org": (USER, "Ann"), "b@example.org": (UUID(int=2), "")}
    assert delivery_reads.administrator_names(["A@example.org"], users) == {
        "a@example.org": "Ann"
    }


def test_rows_gain_their_users_record_id(monkeypatch):
    """with_recipients gives each email to a user's address that user's
    record id, compared in lower case, and None to every other email."""
    rows = [
        {"id": "notice", "family_id": None},
        {"id": "other", "family_id": None},
        {"id": "family", "family_id": UUID(int=9)},
    ]
    monkeypatch.setattr(
        delivery_reads,
        "administrator_addresses",
        lambda ids, actor: {"notice": "Ann@Example.org", "other": "x@example.org"},
    )
    calls = []

    def users(addresses):
        """One users read, for every shown address at once."""
        calls.append(sorted(addresses))
        return {"ann@example.org": (USER, "Ann")}

    monkeypatch.setattr(delivery_reads, "address_users", users)
    monkeypatch.setattr(delivery_reads, "member_names", lambda ids, actor: {})
    shaped = {row["id"]: row for row in delivery_reads.with_recipients(rows, "me")}
    assert calls == [["Ann@Example.org", "x@example.org"]]
    assert shaped["notice"]["recipient_user_id"] == USER
    assert shaped["notice"]["recipient_name"] == "Ann"
    assert shaped["other"]["recipient_user_id"] is None
    assert shaped["family"]["recipient_user_id"] is None


def test_intended_addresses_gain_their_users():
    """with_user_links pairs each intended address and its outcome with the
    user it is, in lower case; a refused read stays None."""
    recipients = {
        "intended": ["Ann@Example.org", "b@example.org"],
        "intended_rows": [("Ann@Example.org", "delivered"), ("b@example.org", None)],
    }
    linked = delivery_reads.with_user_links(
        recipients, {"ann@example.org": (USER, "Ann")}
    )
    assert linked["intended_links"] == [
        ("Ann@Example.org", "delivered", USER),
        ("b@example.org", None, None),
    ]
    assert linked["intended_rows"] == recipients["intended_rows"]
    assert delivery_reads.with_user_links(None) is None
