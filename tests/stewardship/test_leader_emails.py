"""Ministry leader emails (#1592) without a database: content, routing, plans.

The email names no parishioner, says how to sign in with the shared Google
Account guidance, lists each Ministry's open requests in Home's words, and
in Testing goes to the Testing recipient and says so; its markup is the
report emails' closed set, which the private mail helper admits. The owner
is the third Administrator-notice owner, routed by mode. The facts
function's filters are the follow-up queue's own defaults.
"""

import json
import re
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import google_account_help
from parishkit.stewardship.accounts.leader_emails import (
    EMAIL_SORTING,
    LeaderFacts,
    facts_digest,
    plan_emails,
)
from parishkit.stewardship.jobs.leader_email_content import (
    LeaderEmailFacts,
    LeaderMinistry,
    counts_text,
    portal_urls,
    recorded_ministries,
    render_leader_email,
)
from parishkit.stewardship.jobs.leader_email_models import REQUEST_LIMIT
from parishkit.stewardship.jobs.leader_email_owner import LEADER, leader_owner
from parishkit.stewardship.jobs.operational_owner import OPERATIONAL
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.jobs.security_owner import SECURITY
from parishkit.stewardship.web.weekly_digest_content import validate_weekly_body
from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "src/parishkit/stewardship/schema/migrations/0117_leader_emails.sql"
RECIPIENTS = [
    {
        "email": "ann@example.org",
        "ministries": [
            {"duid": 4, "name": "Choir & <Bells>", "join": 2, "leave": 1},
            {"duid": 9, "name": "Food pantry", "join": 0, "leave": 0},
        ],
    },
    {"email": "bob@example.org", "ministries": []},
]


def facts(mode="production", testing=None):
    """One request's facts, as the guard records them."""
    return LeaderEmailFacts(
        parish="St. Example Parish",
        campaign="Stewardship 2027",
        public_origin="https://portal.example.org",
        mode=mode,
        testing_recipient=testing,
        ministries=recorded_ministries(RECIPIENTS),
    )


def test_a_production_email_says_how_to_sign_in_and_what_waits():
    """The sign-in link, the address, the guidance, each Ministry and a link."""
    subject, html, text, routed = render_leader_email(facts(), "ann@example.org")
    assert routed == "ann@example.org"
    assert subject == "Sign in to see your Ministry requests — St. Example Parish"
    assert "Sign in: https://portal.example.org/admin/login" in text
    assert "Your sign-in address is ann@example.org" in text
    for line in (
        google_account_help.WHICH_ADDRESS,
        google_account_help.NO_ACCOUNT,
        google_account_help.STILL_STUCK,
    ):
        assert str(line) in text
    assert google_account_help.HELP_URL in text and google_account_help.HELP_URL in html
    assert "- Choir & <Bells>: 2 asking to join · 1 asking to leave" in text
    assert "- Food pantry: No open requests" in text
    assert (
        "Open Ministry requests: https://portal.example.org/admin/reports/ministries/"
        in text
    )
    # Names are escaped in the HTML, and the markup is the closed report set
    # the private mail helper admits unchanged.
    assert "Choir &amp; &lt;Bells&gt;" in html and "<Bells>" not in html
    validate_weekly_body(html, text)
    WeeklyDeliveryMail(
        uuid4(), "s@example.org", "r@example.org", (routed,), subject, html, text
    )
    assert "TEST" not in subject + text


def test_a_testing_email_goes_to_the_testing_recipient_and_says_so():
    """Only the Testing recipient; subject and both bodies name the leader."""
    subject, html, text, routed = render_leader_email(
        facts("testing", "test@example.org"), "ann@example.org"
    )
    assert routed == "test@example.org"
    assert subject.startswith("[TEST] Sign in to see your Ministry requests")
    banner = (
        "TEST — sent to test@example.org instead of Ministry leader ann@example.org."
    )
    assert text.startswith(banner)
    assert html.startswith("<h2>TEST</h2><p>" + banner + "</p>")
    validate_weekly_body(html, text)


@pytest.mark.parametrize(
    "mode, testing", [("testing", None), ("production", "t@example.org"), ("x", None)]
)
def test_mode_and_testing_recipient_must_agree(mode, testing):
    """A Testing email always has a Testing recipient, a Production one never."""
    with pytest.raises(ValueError):
        facts(mode, testing)


def test_only_a_recorded_leader_can_be_rendered():
    """An address the request did not record is refused."""
    with pytest.raises(ValueError):
        render_leader_email(facts(), "stranger@example.org")


@pytest.mark.parametrize(
    "value",
    [
        {"duid": 4, "name": "Choir", "join": -1, "leave": 0},
        {"duid": "4", "name": "Choir", "join": 0, "leave": 0},
        {"duid": 4, "name": "Choir", "join": 0, "leave": 0, "member": "Ann"},
        {"duid": 4, "join": 0, "leave": 0},
    ],
)
def test_a_recorded_ministry_has_a_closed_shape(value):
    """Facts are counts and a name only; anything more is refused."""
    with pytest.raises(ValueError):
        LeaderMinistry.from_fact(value)


def test_the_counts_read_as_home_words_them():
    """The My Ministries panel's words, "No open requests" for none."""
    assert counts_text(LeaderMinistry(1, "A", 0, 0)) == "No open requests"
    assert counts_text(LeaderMinistry(1, "A", 1, 0)) == (
        "1 asking to join · 0 asking to leave"
    )


def test_links_require_a_public_http_origin():
    """Both links share the report links' origin checks."""
    assert portal_urls("https://portal.example.org/") == (
        "https://portal.example.org/admin/login",
        "https://portal.example.org/admin/reports/ministries/",
    )
    for origin in ("ftp://x.example", "https://user:pw@x.example", None):
        with pytest.raises(ValueError):
            portal_urls(origin)


def test_a_long_parish_name_keeps_the_subject_within_its_limit():
    """The subject never passes the room the "[TEST] " prefix leaves."""
    subject, *_ = render_leader_email(
        replace(facts(), parish="P" * 400), "ann@example.org"
    )
    assert len(subject) == 247 and subject.endswith("…")


def test_the_owner_is_routed_by_mode_and_distinct():
    """The third owner: its own purpose, Task type, namespace and tables."""
    assert LEADER.routing("testing") == "testing_override"
    assert LEADER.routing("production") == "production"
    assert LEADER.routings == ("testing_override", "production")
    assert OPERATIONAL.routings == SECURITY.routings == ("operational",)
    for other in (OPERATIONAL, SECURITY):
        for name in ("purpose", "task_type", "namespace", "cohort_table"):
            assert getattr(LEADER, name) != getattr(other, name)
    assert LEADER.keeps_content and not SECURITY.keeps_content
    # Only the leader email owner is routed by mode.
    with pytest.raises(TypeError):
        replace(SECURITY, testing_routed=True)
    with pytest.raises(TypeError):
        replace(LEADER, testing_routed=False)
    # The scheduler's and mail dispatch's owner compiles no link.
    with pytest.raises(PermissionError):
        LEADER.alert(None, "production")
    assert leader_owner("https://portal.example.org").purpose == LEADER.purpose


def identity(**changes):
    """A Ministry leader email's delivery identity."""
    values = dict(
        scope_id=uuid4(),
        semantic_key=uuid4(),
        mode="production",
        routing="production",
        purpose="leader_email",
    )
    return DeliveryIdentity(**(values | changes))


def test_the_delivery_identity_is_parish_scoped_and_routed_by_mode():
    """No campaign or Family; Testing only to the Testing override."""
    identity()
    identity(mode="testing", routing="testing_override")
    for changes in (
        {"routing": "operational"},
        {"mode": "testing"},
        {"campaign_id": uuid4()},
        {"family_id": uuid4()},
    ):
        with pytest.raises(ValueError):
            identity(**changes)


def leaders(*emails):
    """Users-page rows of Ministry leader users."""
    return [{"email": email, "name": email.split("@")[0].title()} for email in emails]


def test_the_plan_emails_leaders_and_skips_the_rest():
    """Chosen non-leaders and leaders of nothing are skipped, with why."""
    found = {
        "ann@example.org": LeaderFacts(
            "ann@example.org", (LeaderMinistry(9, "F", 1, 0),)
        ),
        "bob@example.org": LeaderFacts("bob@example.org", ()),
    }
    plan = plan_emails(
        leaders("bob@example.org", "ann@example.org"),
        found,
        ["ann@example.org", "bob@example.org", "admin@example.org"],
    )
    assert [item["email"] for item in plan.emailed] == ["ann@example.org"]
    assert plan.skipped == [
        {"email": "bob@example.org", "reason": "no_ministry"},
        {"email": "admin@example.org", "reason": "not_leader"},
    ]
    assert plan.remaining == 0 and plan.addresses == ["ann@example.org"]
    # Email all takes every Ministry leader user.
    assert plan_emails(leaders("ann@example.org"), found).addresses == [
        "ann@example.org"
    ]


def test_one_apply_emails_at_most_the_request_limit():
    """The first leaders by Name are emailed; the rest are counted."""
    emails = [f"leader{index:03}@example.org" for index in range(REQUEST_LIMIT + 5)]
    found = {
        email: LeaderFacts(email, (LeaderMinistry(9, "F", 0, 0),)) for email in emails
    }
    plan = plan_emails(leaders(*emails), found)
    assert len(plan.emailed) == REQUEST_LIMIT and plan.remaining == 5
    assert plan.emailed[0]["email"] == "leader000@example.org"


def test_the_table_sorts_by_its_columns_only():
    """Name by default; Last emailed newest first on the first choice."""
    assert EMAIL_SORTING.parse({}, "emails_") == "name"
    assert EMAIL_SORTING.parse({"emails_sort": "-last_emailed"}, "emails_")
    with pytest.raises(ValueError):
        EMAIL_SORTING.parse({"emails_sort": "role"}, "emails_")


def test_the_facts_filters_are_the_follow_up_queues_defaults():
    """The SQL literal is selection_filters(FollowupQuery()), key for key."""
    from parishkit.stewardship.reports.ministry_followup import (
        FollowupQuery,
        selection_filters,
    )

    text = FROZEN.read_text(encoding="utf-8")
    literal = re.search(r"'(\{\"search\".*?\})'::jsonb", text)[1]
    assert json.loads(literal) == json.loads(selection_filters(FollowupQuery()))


def test_recorded_facts_read_back_in_order():
    """Each address's Ministries in the order the request recorded them."""
    ministries = recorded_ministries(RECIPIENTS)
    assert isinstance(ministries, MappingProxyType)
    assert [item.duid for item in ministries["ann@example.org"]] == [4, 9]
    assert ministries["bob@example.org"] == ()
    with pytest.raises(ValueError):
        recorded_ministries([RECIPIENTS[0], RECIPIENTS[0]])


def test_the_review_digest_matches_only_the_same_recorded_facts():
    """Apply compares the guard's recorded facts with the reviewed ones.

    The review's plan and the request's recorded facts digest alike exactly
    when every email would state the same Ministries and counts.
    """
    ministries = recorded_ministries(RECIPIENTS[:1])
    plan = plan_emails(
        leaders("ann@example.org"),
        {
            "ann@example.org": LeaderFacts(
                "ann@example.org", ministries["ann@example.org"]
            )
        },
    )
    assert plan.digest == facts_digest(ministries)
    changed = json.loads(json.dumps(RECIPIENTS[:1]))
    changed[0]["ministries"][1]["join"] = 1
    assert plan.digest != facts_digest(recorded_ministries(changed))
    fewer = json.loads(json.dumps(RECIPIENTS[:1]))
    del fewer[0]["ministries"][0]
    assert plan.digest != facts_digest(recorded_ministries(fewer))
