"""Home's Next steps table and Today line, without a database (NAV-18).

The admin-portal spec's "Home page" table chooses the steps by campaign state
and role; each step is the viewer's own menu entry, so it is a link only
while the menu offers it and keeps the menu's reason while greyed out.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.accounts import home_steps

ALL = frozenset(entry.name for entry in navigation.MENU)


def campaign(state="draft", paused=False):
    """A stand-in campaign row with only what the state reading needs."""
    return SimpleNamespace(state=state, delivery_paused=paused)


def menu(names=ALL, greyed=(), counts=None):
    """The chrome's menu entries by name, as ``admin_navigation.build`` draws them."""
    counts = counts or {}
    return {
        name: {
            "name": name,
            "label": navigation.PAGES[name].label,
            "url": None if name in greyed else f"/{name}/",
            "reason": navigation.NO_CAMPAIGN if name in greyed else None,
            "count": counts.get(name),
        }
        for name in names
    }


def labels(steps):
    """Each step's page name, in order."""
    return [step.label for step in steps]


@pytest.mark.parametrize(
    ("row", "mode", "go_live", "expected"),
    [
        (None, "testing", False, None),
        (campaign("draft"), "testing", False, "testing"),
        (campaign("draft"), "production", True, "activating"),
        (campaign("scheduled"), "production", False, "activating"),
        (campaign("active"), "production", False, "active"),
        (campaign("active"), "testing", False, "active"),
        (campaign("active", paused=True), "production", False, "paused"),
        (campaign("scheduled", paused=True), "production", False, "paused"),
        # Testing never pauses live mail; the flag alone does not count.
        (campaign("active", paused=True), "testing", False, "active"),
        (campaign("closed", paused=True), "production", False, "closed"),
        (campaign("archived"), "production", False, "closed"),
    ],
)
def test_campaign_state_follows_the_spec_rows(row, mode, go_live, expected):
    """Each campaign state reads as one row of the spec's table."""
    assert home_steps.campaign_state(row, mode, go_live=go_live) == expected


def test_role_is_the_highest_held():
    """An Administrator reads the Administrator column, a Staff member Staff's."""
    assert home_steps.role({"administrator", "staff"}) == "administrator"
    assert home_steps.role({"staff", "ministry_leader"}) == "staff"
    assert home_steps.role({"ministry_leader"}) == "ministry_leader"
    assert home_steps.role(set()) is None


def test_testing_draft_steps_by_role():
    """The Testing row: an Administrator's setup pages, Staff's reports."""
    revision = uuid4()
    admin = home_steps.next_steps(
        "testing", "administrator", menu(), test_email=revision
    )
    assert labels(admin) == [
        "Campaign settings",
        "Pages and emails",
        "Dates and mail schedules",
        "Preview and test email",
        "Go-live readiness",
        "Response dashboard",
    ]
    assert admin[3].url == reverse("admin:campaign_mail", args=[revision])
    staff = home_steps.next_steps("testing", "staff", menu())
    assert labels(staff) == ["Response dashboard", "Participation"]
    leader = home_steps.next_steps("testing", "ministry_leader", menu())
    assert labels(leader) == ["Ministry requests"]


def test_steps_follow_the_viewers_menu():
    """A step the role's menu leaves out is left out; a greyed one keeps its
    reason and has no link, and Preview and test email follows Pages and
    emails (and needs an invitation to test)."""
    names = ALL - {"go_live"}
    steps = home_steps.next_steps(
        "testing",
        "administrator",
        menu(names, greyed={"content_catalog"}),
        test_email=uuid4(),
    )
    assert "Go-live readiness" not in labels(steps)
    by_label = {step.label: step for step in steps}
    for name in ("Pages and emails", "Preview and test email"):
        assert by_label[name].url is None
        assert by_label[name].reason == navigation.NO_CAMPAIGN
    without = home_steps.next_steps("testing", "administrator", menu())
    assert "Preview and test email" not in labels(without)


def test_active_and_paused_steps_carry_the_open_counts():
    """The queues show their open counts; Sent mail filters to the emails
    that may not have arrived while there are some; paused mail comes first."""
    entries = menu(counts={"information_queue": 3, "ministry_report": 0})
    active = home_steps.next_steps("active", "administrator", entries, mail_unknown=2)
    assert labels(active) == [
        "Response dashboard",
        "Additional information",
        "Ministry requests",
        "Family email progress",
        "Sent mail",
    ]
    assert (active[1].count, str(active[1].note)) == (3, "open")
    assert (active[2].count, str(active[2].note)) == (0, "open")
    assert active[4].url == "/deliveries/?state=delivery_unknown"
    assert (active[4].count, str(active[4].note)) == (2, "may not have arrived")
    quiet = home_steps.next_steps("active", "administrator", entries, mail_unknown=0)
    assert (quiet[4].url, quiet[4].count) == ("/deliveries/", None)
    paused = home_steps.next_steps("paused", "administrator", entries)
    assert labels(paused)[0] == "Pause and resume mail"
    assert labels(paused)[1:] == labels(active)
    assert labels(home_steps.next_steps("paused", "staff", entries)) == [
        "Response dashboard",
        "Additional information",
        "Ministry requests",
    ]
    leader = home_steps.next_steps("active", "ministry_leader", entries)
    assert labels(leader) == ["Ministry requests"]


def test_greyed_queue_shows_no_count():
    """A greyed queue entry names its reason and no count, as the menu does."""
    entries = menu(greyed={"information_queue"}, counts={"information_queue": 4})
    step = home_steps.next_steps("active", "staff", entries)[1]
    assert (step.url, step.note) == (None, None)
    assert step.reason == navigation.NO_CAMPAIGN


def test_closed_and_activating_steps():
    """The closed row offers the reports; activation, its progress and mail."""
    assert labels(home_steps.next_steps("closed", "administrator", menu())) == [
        "Participation",
        "Emailed reports",
    ]
    assert labels(home_steps.next_steps("activating", "administrator", menu())) == [
        "Production activation",
        "Sent mail",
    ]
    assert home_steps.next_steps(None, "administrator", menu()) == []


def test_draft_in_production_preparation_leads_with_go_live_readiness():
    """Home lists Go-live readiness first while a draft is being prepared."""
    entries = menu()
    context = {
        "admin_chrome": {
            "sections": [{"items": list(entries.values())}],
            "go_live": True,
        },
        "configuration": SimpleNamespace(mode="production"),
        "principal": SimpleNamespace(roles={"administrator"}),
        "dashboard": {"campaign": campaign("draft")},
    }
    assert labels(home_steps.home(context)["steps"]) == [
        "Go-live readiness",
        "Production activation",
        "Sent mail",
    ]
    context["dashboard"] = {"campaign": campaign("scheduled")}
    assert labels(home_steps.home(context)["steps"])[0] == "Production activation"


def test_today_counts_by_role():
    """Administrators and Staff count recent responses; a leader, open requests."""
    assert home_steps.today({"families": {"recent": 4}}) == {"responded": 4}
    mine = {"ministries": [{"join": 2, "leave": 1}, {"join": 0, "leave": 3}]}
    assert home_steps.today({"my_ministries": mine}) == {"requests": 6}
    assert home_steps.today({"families": {"active": 9}}) == {}


def test_home_returns_nothing_without_a_campaign_or_chrome():
    """No current campaign: the campaign panel says what to do instead."""
    principal = SimpleNamespace(roles={"administrator"})
    configuration = SimpleNamespace(mode="testing")
    chrome = {"sections": [{"items": list(menu().values())}]}
    context = {
        "admin_chrome": chrome,
        "configuration": configuration,
        "principal": principal,
        "dashboard": {"campaign": None},
    }
    assert home_steps.home(context) is None
    assert home_steps.home({**context, "admin_chrome": {}}) is None
    context["dashboard"] = {"campaign": campaign("draft"), "families": {"recent": 1}}
    result = home_steps.home(context)
    assert labels(result["steps"])[0] == "Campaign settings"
    assert result["today"] == {"responded": 1}


def test_template_links_available_steps_and_names_greyed_ones():
    """The panel links what the menu offers, writes the reason for the rest,
    and leads with the Today line."""
    entries = menu(greyed={"ministry_report"}, counts={"information_queue": 5})
    context = {
        "admin_chrome": {
            "sections": [{"items": list(entries.values())}],
            "delivery_unknown": 1,
        },
        "configuration": SimpleNamespace(mode="production"),
        "principal": SimpleNamespace(roles={"administrator"}),
        "dashboard": {"campaign": campaign("active"), "families": {"recent": 1}},
    }
    html = render_to_string("stewardship/home-next-steps.html", context)
    assert "data-next-steps" in html and ">Next steps</h2>" in html
    assert "Today: 1 Family responded in the past 24 hours." in html
    assert '<a href="/information_queue/">Additional information</a> — 5 open' in html
    assert f"Ministry requests — {navigation.NO_CAMPAIGN}" in html
    assert (
        '<a href="/deliveries/?state=delivery_unknown">Sent mail</a>'
        " — 1 may not have arrived"
    ) in html
    context["dashboard"] = {"campaign": None}
    assert "data-next-steps" not in render_to_string(
        "stewardship/home-next-steps.html", context
    )
