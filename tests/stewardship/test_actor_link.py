"""The shared "Admin user who acted" name and link (#2259).

``{% actor_link %}`` and ``{% actor_sentence %}`` draw every staff page's
actor the same way: the user's Name (else address, else the page's wording
for an account that is gone), linked to the user's Timeline only when the
actor is a current user and the viewer may open users' Timelines
(Administrators). Downloads keep the address (``actor_text``).
"""

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.template import Context, Template

from parishkit.stewardship.accounts.actor_names import (
    FORMER,
    WHO,
    Actor,
    ActorSentence,
    actor_html,
    actor_of,
    actor_text,
    gone,
)
from parishkit.stewardship.accounts.policy import Principal

USER = UUID(int=2259)
LINK = f'<a href="/admin/users/{USER}/timeline/">'
NAMED = Actor("Administrator, IT", "itadmin@example.org", USER)
TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)


def viewer(*roles):
    """A request whose admitted principal holds ``roles``."""
    return SimpleNamespace(principal=Principal(UUID(int=1), frozenset(roles)))


ADMINISTRATOR = viewer("administrator")


def draw(source, request=ADMINISTRATOR, **values):
    """Render ``source`` with the stewardship tags, as the viewer sees it."""
    template = Template("{% load stewardship %}" + source)
    return template.render(Context({"request": request, **values}))


def test_a_named_user_links_their_timeline_for_an_administrator():
    """The Name, escaped, opens the user's Timeline by the record id."""
    html = draw("{% actor_link who %}", who=Actor("Adams, <b>", "a@example.org", USER))
    assert html == f"{LINK}Adams, &lt;b&gt;</a>"
    assert draw("{% actor_link who %}", who=NAMED) == f"{LINK}Administrator, IT</a>"


@pytest.mark.parametrize(
    "request_",
    [viewer("staff"), viewer("ministry_leader"), viewer(), SimpleNamespace()],
)
def test_no_link_for_a_reader_who_cannot_open_users_timelines(request_):
    """Staff, Ministry leaders and roleless or missing principals get the
    same words with no link: no reader meets a link that would be refused."""
    assert draw("{% actor_link who %}", request_, who=NAMED) == "Administrator, IT"
    assert "href" not in actor_html(NAMED, getattr(request_, "principal", None))


def test_no_name_shows_the_address_and_gone_accounts_keep_their_wording():
    """An address that is no current user, a gone account and plain wording
    are shown unlinked, as they always were."""
    address = Actor("x@example.org", "x@example.org", None)
    assert draw("{% actor_link who %}", who=address) == "x@example.org"
    assert draw("{% actor_link who %}", who=gone()) == FORMER
    assert draw("{% actor_link who %}", who="Family") == "Family"
    assert draw("{% actor_link who 'none' %}", who="") == "none"
    assert actor_of({}, UUID(int=5)) == gone()
    assert actor_of({str(USER): NAMED}, USER) is NAMED


def test_downloads_keep_the_address():
    """``actor_text`` gives the address, else the label; text passes through."""
    assert actor_text(NAMED) == "itadmin@example.org"
    assert actor_text(gone()) == FORMER
    assert actor_text("Family") == "Family"
    assert actor_text(None) == ""
    assert str(NAMED) == "Administrator, IT"


def test_a_sentence_is_text_by_address_and_a_page_line_by_name():
    """An ``ActorSentence`` reads by address as text (export, pk-admin) and
    draws the actor by name, linked, inside the same escaped words."""
    sentence = ActorSentence(f"Phone <home>; by {WHO}; note: ok", NAMED)
    assert sentence == "Phone <home>; by itadmin@example.org; note: ok"
    assert str(sentence) == "Phone <home>; by itadmin@example.org; note: ok"
    html = draw("{% actor_sentence line %}", line=sentence)
    assert html == f"Phone &lt;home&gt;; by {LINK}Administrator, IT</a>; note: ok"
    staff = draw("{% actor_sentence line %}", viewer("staff"), line=sentence)
    assert staff == "Phone &lt;home&gt;; by Administrator, IT; note: ok"
    assert draw("{% actor_sentence line %}", line="a <b>") == "a &lt;b&gt;"


# Template values that hold an actor: each must be drawn by the shared tag,
# never printed as a plain variable (which would show a name with no link).
ACTOR_VALUES = (
    "decided_by",
    "actor_label",
    "completed_by",
    "completer_label",
    "assignee_label",
    "row.actor",
    "event.detail",
    "event.actor",
    "pause.actor",
    "accept_actor",
    "debug_actor",
    "closed_by",
    "checked_by",
    "started_by",
    "roster_by",
    "imported_by",
    "current_by",
)
NAMES = "|".join(map(re.escape, ACTOR_VALUES))


def test_admin_templates_draw_actors_with_the_shared_tags():
    """Guard: no Admin template prints an actor value as ``{{ … }}`` or hands
    one to ``blocktranslate with`` (which would show it with no link)."""
    printed = re.compile(r"\{\{\s*[\w.]*(?:" + NAMES + r")(?:\.\d+)?\s*[|}]")
    handed = re.compile(r"\bwith\s+\w+=[\w.]*(?:" + NAMES + r")\b")
    found = [
        f"{path.name}: {match.group(0)}"
        for path in TEMPLATES.rglob("*.html")
        for pattern in (printed, handed)
        for match in pattern.finditer(path.read_text())
    ]
    # The importer's address split for the narrow column, when no current
    # user names it (``past_campaign_views._by``).
    allowed = {
        "past-campaigns.html: {{ row.imported_by.0 }",
        "past-campaigns.html: {{ row.imported_by.1 }",
    }
    assert [item for item in found if item not in allowed] == []


def test_a_lazy_fallback_still_draws_as_text():
    """A page's own lazily translated wording for a gone account (Update
    ParishSoft's "a former portal user") renders as text."""
    from django.utils.translation import gettext_lazy

    wording = actor_of({}, UUID(int=9), gettext_lazy("a former portal user"))
    assert str(wording) == "a former portal user"
    assert draw("{% actor_link who %}", who=wording) == "a former portal user"


def test_a_matched_account_is_named_by_the_match_its_entry_falls_in():
    """Inside a verified-address match's window (#2343) an entry reads "user
    address through sign-in address", linked to the user's Timeline; outside
    every window it reads the sign-in address, even while the account acts
    as the user's address now; another actor is left as it is."""
    from datetime import UTC, datetime, timedelta

    from parishkit.stewardship.accounts.actor_names import through

    start = datetime(2026, 11, 1, 12, tzinfo=UTC)
    record = uuid4()
    match = {
        "email": "staff@example.org",
        "user_record_id": record,
        "sign_in_email": "person@personal.example",
        "matched_at": start,
        "ended_at": start + timedelta(days=1),
    }
    now = Actor("Staff, Sam", "staff@example.org", record)
    inside = through(now, [match], start + timedelta(hours=1))
    assert inside == Actor(
        "staff@example.org through person@personal.example",
        "staff@example.org",
        record,
    )
    assert through(now, [match], start - timedelta(hours=1)) == Actor(
        "person@personal.example", "person@personal.example"
    )
    plain = Actor("Admin", "admin@example.org", uuid4())
    assert through(plain, [], start) is plain
    assert through(plain, None, start) is plain
    assert through(None, [match], start) is None
