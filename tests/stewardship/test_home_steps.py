"""Home's Next steps table, without a database (NAV-18).

The admin-portal spec's "Home page" table chooses the steps by campaign
state. Home is the Administrator's (#1726); each step is their own menu
entry, so it is a link only while the menu offers it and keeps the menu's
reason while greyed out.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.accounts import home_steps
from parishkit.stewardship.jobs.delivery_metadata import mode_split

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
        # Ministry requests carries its To enter by hand count for Home
        # (admin_context.ENTRY_COUNT, R39 finding 2).
        | (
            {"entry_count": counts.get("ministry_entry")}
            if name == "ministry_report"
            else {}
        )
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


def test_testing_draft_steps():
    """The Testing row: the campaign's setup pages; Home's Family
    participation tiles link the Response dashboard instead (#1673)."""
    revision = uuid4()
    admin = home_steps.next_steps("testing", menu(), test_email=revision)
    assert labels(admin) == [
        "Campaign settings",
        "Pages and emails",
        "Dates and mail schedules",
        "Preview and test email",
        "Go-live readiness",
    ]
    assert admin[3].url == reverse("admin:campaign_mail", args=[revision])


def test_steps_follow_the_viewers_menu():
    """A step the role's menu leaves out is left out; a greyed one keeps its
    reason and has no link, and Preview and test email follows Pages and
    emails (and needs an invitation to test)."""
    names = ALL - {"go_live"}
    steps = home_steps.next_steps(
        "testing",
        menu(names, greyed={"content_catalog"}),
        test_email=uuid4(),
    )
    assert "Go-live readiness" not in labels(steps)
    by_label = {step.label: step for step in steps}
    for name in ("Pages and emails", "Preview and test email"):
        assert by_label[name].url is None
        assert by_label[name].reason == navigation.NO_CAMPAIGN
    without = home_steps.next_steps("testing", menu())
    assert "Preview and test email" not in labels(without)


# The four lists Staff work through at campaign end (#1590), while the
# campaign runs and after it closes. The Response dashboard is linked from
# Home's Family participation tiles instead (#1673).
WORK = [
    "Census changes",
    "Ministry requests to enter by hand",
    "Additional information",
    "Ministry requests",
]


def split(count, other, mode="production"):
    """The chrome's split uncertain count (``delivery_metadata.mode_split``)."""
    return mode_split(
        {mode: count, "testing" if mode == "production" else "production": other},
        mode,
    )


def test_mail_problems_link_each_mode_separately():
    """The operating mode's count links that mode; the other mode's has its
    own link and is never added in, also when only the other mode has some
    (#1576)."""
    entries = menu()

    def sent(steps):
        """The Sent mail step (#1590 added steps before it)."""
        return next(step for step in steps if step.label == "Sent mail")

    both = sent(home_steps.next_steps("active", entries, mail_unknown=split(2, 3)))
    assert both.url == "/deliveries/?state=needs_review&mode=production"
    assert both.count == 2
    assert both.other.url == "/deliveries/?state=needs_review&mode=testing"
    assert (both.other.count, str(both.other.note), str(both.other.more)) == (
        3,
        "in Testing",
        "may not have arrived",
    )
    only = sent(home_steps.next_steps("active", entries, mail_unknown=split(0, 1)))
    assert (only.url, only.count) == ("/deliveries/", None)
    assert only.other.url == "/deliveries/?state=needs_review&mode=testing"
    testing = sent(
        home_steps.next_steps(
            "active",
            entries,
            mail_unknown=split(0, 4, mode="testing"),
        )
    )
    assert testing.other.url == ("/deliveries/?state=needs_review&mode=production")
    assert str(testing.other.note) == "in Production"


def test_active_and_paused_steps_carry_the_open_counts():
    """The queues show their open counts; Sent mail filters to the emails
    that may not have arrived while there are some; paused mail comes first."""
    entries = menu(
        counts={
            "census_changes": 4,
            "ministry_entry": 6,
            "information_queue": 3,
            "ministry_report": 0,
        }
    )
    active = home_steps.next_steps("active", entries, mail_unknown=split(2, 0))
    assert labels(active) == [*WORK, "Family email progress", "Sent mail"]
    assert (active[0].url, active[0].count, str(active[0].note)) == (
        "/census_changes/",
        4,
        "to do",
    )
    assert (active[1].url, active[1].count, str(active[1].note)) == (
        "/ministry_report/?stage=to_enter_by_hand",
        6,
        "to enter by hand",
    )
    assert (active[2].count, str(active[2].note)) == (3, "open")
    assert (active[3].count, str(active[3].note)) == (0, "open")
    assert active[5].url == "/deliveries/?state=needs_review&mode=production"
    assert (active[5].count, str(active[5].note)) == (2, "may not have arrived")
    assert active[5].other is None
    for idle in (
        home_steps.next_steps("active", entries, mail_unknown=split(0, 0)),
        home_steps.next_steps("active", entries),
    ):
        assert (idle[5].url, idle[5].count, idle[5].other) == (
            "/deliveries/",
            None,
            None,
        )
    # A to-enter count the chrome could not read shows the link with no number.
    unread = home_steps.next_steps("active", menu())[1]
    assert (unread.url, unread.count, unread.note) == (
        "/ministry_report/?stage=to_enter_by_hand",
        None,
        None,
    )
    paused = home_steps.next_steps("paused", entries)
    assert labels(paused)[0] == "Pause and resume mail"
    assert labels(paused)[1:] == labels(active)


def test_greyed_queue_shows_no_count():
    """A greyed queue entry names its reason and no count, as the menu does."""
    entries = menu(greyed={"information_queue"}, counts={"information_queue": 4})
    step = home_steps.next_steps("active", entries)[2]
    assert step.label == "Additional information"
    assert (step.url, step.note) == (None, None)
    assert step.reason == navigation.NO_CAMPAIGN


def test_to_enter_step_follows_ministry_requests():
    """Ministry requests to enter by hand follows Ministry requests' menu
    entry (R39 finding 2, which folded Roster changes to enter into it):
    greyed with its reason, and left out when the menu has none."""
    greyed = menu(greyed={"ministry_report"}, counts={"ministry_entry": 3})
    step = home_steps.next_steps("closed", greyed)[1]
    assert (step.label, step.url, step.note) == (
        "Ministry requests to enter by hand",
        None,
        None,
    )
    assert step.reason == navigation.NO_CAMPAIGN
    without = menu(ALL - {"ministry_report"})
    assert "Ministry requests to enter by hand" not in labels(
        home_steps.next_steps("closed", without)
    )


def test_closed_and_activating_steps():
    """The closed row offers the campaign-end work lists with their counts
    (#1590) and Emailed reports. Activation lists its progress and mail."""
    entries = menu(
        counts={
            "census_changes": 2,
            "ministry_entry": 0,
            "information_queue": 1,
            "ministry_report": 5,
        }
    )
    closed = home_steps.next_steps("closed", entries)
    assert labels(closed) == [*WORK, "Emailed reports"]
    assert [(step.count, str(step.note)) for step in closed[0:4]] == [
        (2, "to do"),
        (0, "to enter by hand"),
        (1, "open"),
        (5, "open"),
    ]
    assert labels(home_steps.next_steps("activating", menu())) == [
        "Production activation",
        "Sent mail",
    ]
    assert home_steps.next_steps(None, menu()) == []


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
    assert labels(home_steps.home(context)) == [
        "Go-live readiness",
        "Production activation",
        "Sent mail",
    ]
    context["dashboard"] = {"campaign": campaign("scheduled")}
    assert labels(home_steps.home(context))[0] == "Production activation"


def test_home_returns_nothing_without_a_campaign_chrome_or_administrator():
    """No current campaign: the campaign panel says what to do instead. Only
    an Administrator is shown Home (#1726), so no one else gets steps."""
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
    assert labels(home_steps.home(context))[0] == "Campaign settings"
    for roles in ({"staff"}, {"staff", "ministry_leader"}, {"ministry_leader"}):
        other = context | {"principal": SimpleNamespace(roles=roles)}
        assert home_steps.home(other) is None


def test_template_links_available_steps_and_names_greyed_ones():
    """The panel's link cards link what the menu offers, with the count large
    and what it means, and write the reason for the rest (#1673)."""
    entries = menu(
        greyed={"ministry_report"},
        counts={"information_queue": 5, "census_changes": 2},
    )
    context = {
        "admin_chrome": {
            "sections": [{"items": list(entries.values())}],
            "delivery_unknown": split(1, 2),
        },
        "configuration": SimpleNamespace(mode="production"),
        "principal": SimpleNamespace(roles={"administrator"}),
        "dashboard": {"campaign": campaign("active"), "families": {"recent": 1}},
    }
    html = render_to_string("stewardship/home-next-steps.html", context)
    assert "data-next-steps" in html and ">Next steps</h2>" in html
    assert "data-today" not in html

    def card(url, label, count, note, look):
        """A linked card's markup, as components/link-cards.html draws it."""
        return (
            f'<li class="link-card {look}" data-link-card><a href="{url}">'
            f'<span class="link-card-label">{label}</span> '
            f'<span class="link-card-count">{count}</span> '
            f'<span class="link-card-note">{note}</span></a></li>'
        )

    assert (
        card(
            "/information_queue/",
            "Additional information",
            5,
            "open",
            "link-card-attention",
        )
        in html
    )
    assert (
        card("/census_changes/", "Census changes", 2, "to do", "link-card-attention")
        in html
    )
    # Ministry requests to enter by hand is greyed with Ministry requests'
    # menu reason (R39 finding 2).
    for label in ("Ministry requests to enter by hand", "Ministry requests"):
        assert (
            f'<li class="link-card link-card-off" data-link-card>'
            f'<span class="link-card-label">{label}</span> '
            f'<span class="link-card-note">{navigation.NO_CAMPAIGN}</span></li>'
        ) in html
    # Sent mail's other mode has some too: a second, small link at the
    # card's end, whose hidden words finish its name (#1576).
    production = card(
        "/deliveries/?state=needs_review&amp;mode=production",
        "Sent mail",
        1,
        "may not have arrived",
        "link-card-attention link-card-split",
    ).removesuffix("</li>")
    assert (
        f"{production} "
        '<a class="link-card-other" '
        'href="/deliveries/?state=needs_review&amp;mode=testing">2 in Testing'
        '<span class="visually-hidden"> may not have arrived</span></a></li>'
    ) in html
    context["dashboard"] = {"campaign": None}
    assert "data-next-steps" not in render_to_string(
        "stewardship/home-next-steps.html", context
    )
