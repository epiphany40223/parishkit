"""A user's Timeline (#1866): lines, summary, filters and page, without a database.

The view's admission, reads, recheck and audit, the index and the command
line are covered against PostgreSQL in
``database/test_user_timelines_postgresql.py``.
"""

import re
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.accounts.user_timeline_views import (
    PAGE_SIZE,
    UserTimelineQuery,
    page_context,
    parse_query,
)
from parishkit.stewardship.accounts.user_timelines import (
    EMAIL_NAMES,
    KINDS,
    SESSION_WORDS,
    SIGNED_OUT_OTHER,
    FamilyPart,
    Person,
    UserTimeline,
    audit_lines,
    email_lines,
    family_lines,
    ordered,
    role_changes,
    rule_role,
    summarize,
)
from parishkit.stewardship.audit.log_descriptions import describe
from parishkit.stewardship.audit.log_rows import ACTIVITY_TYPES, PAGE_READS
from parishkit.stewardship.jobs.outbox_models import DELIVERY_PURPOSES
from parishkit.stewardship.reports.timeline_views import Identity
from parishkit.stewardship.reports.timelines import (
    Email,
    Engagement,
    Submitted,
    Timeline,
    events,
    summary,
)
from parishkit.stewardship.web.dates import using

from .test_response_metrics import START

USER = UUID(int=1866)
MINUTE = timedelta(minutes=1)
PERSON = Person(USER, "ann@example.org", "Adams, Ann", "staff")
# Three activated configurations: the user was added as a Ministry leader,
# made Staff, then removed.
CONFIGURATIONS = [UUID(int=number) for number in (1, 2, 3, 4)]
ACTIVATIONS = [
    (configuration, START + number * MINUTE)
    for number, configuration in enumerate(CONFIGURATIONS)
]


def email(number, purpose, state, *, mode="production", finished=True):
    """One outbox row as the email read gives it."""
    created = START + number * MINUTE
    return {
        "id": UUID(int=number),
        "purpose": purpose,
        "mode": mode,
        "state": state,
        "created_at": created,
        "finished_at": created + MINUTE if finished else None,
    }


def test_session_words_cover_the_sign_in_group():
    """Every Sign-in activity type has a line, except signing another out."""
    assert set(SESSION_WORDS) == set(ACTIVITY_TYPES["sign_in"]) - {SIGNED_OUT_OTHER}
    assert SIGNED_OUT_OTHER in ACTIVITY_TYPES["sign_in"]
    # Every outbox purpose is named, never "Email".
    assert set(EMAIL_NAMES) == set(DELIVERY_PURPOSES)
    # Page views are a closed list System logs also leaves ungrouped.
    assert "user_timeline_viewed" in PAGE_READS
    assert "family_timeline_viewed" in PAGE_READS


def test_role_changes_follow_each_activation():
    """Became a user, a change of role, an unchanged version and removal."""
    roles = {
        CONFIGURATIONS[0]: "ministry_leader",
        CONFIGURATIONS[1]: "ministry_leader",
        CONFIGURATIONS[2]: "staff",
    }
    lines = role_changes(ACTIVATIONS, roles)
    assert [(line.at, str(line.what), str(line.detail)) for line in lines] == [
        (START, "Became a user", "Ministry leader"),
        (START + 2 * MINUTE, "Role changed", "Ministry leader to Staff"),
        (START + 3 * MINUTE, "Removed as a user", ""),
    ]
    assert {line.kind for line in lines} == {"roles"}
    # Added again later: a second Became a user.
    again = role_changes(
        [*ACTIVATIONS, (UUID(int=5), START)], roles | {UUID(int=5): "staff"}
    )
    assert str(again[-1].what) == "Became a user"
    assert role_changes(ACTIVATIONS, {}) == []


def test_an_older_rule_counts_by_its_highest_role():
    """Before the users upgrade a rule's highest role stands for it."""
    assert rule_role("staff", ["staff", "ministry_leader"]) == "staff"
    assert rule_role(None, ["ministry_leader", "staff"]) == "staff"
    assert rule_role(None, []) is None


def test_audit_lines_are_sessions_or_actions():
    """Sign-ins read in plain words; every other record in System logs' words.

    Each row carries only its recorded outcome, never the whole context.
    """
    rows = [
        (START, "admin_login", "succeeded"),
        (START + MINUTE, "admin_revoked", None),
        (START + 2 * MINUTE, "user_change_requested", "succeeded"),
        (START + 3 * MINUTE, "family_form_opened", "denied"),
        (START + 4 * MINUTE, SIGNED_OUT_OTHER, None),
        (START + 5 * MINUTE, "export_requested", "failed"),
    ]
    lines = audit_lines(rows)
    assert [line.kind for line in lines] == [
        "sessions",
        "sessions",
        "actions",
        "actions",
        "actions",
        "actions",
    ]
    assert str(lines[0].what) == "Signed in" and lines[0].detail == ""
    assert str(lines[1].what) == "Session ended"
    assert "an Administrator signed it out" in str(lines[1].detail)
    assert lines[2].what == describe("user_change_requested")
    assert lines[2].detail == ""
    # A refused Open form has its own System logs sentence, and is Refused.
    assert lines[3].what == describe("family_form_opened", {"outcome": "denied"})
    assert str(lines[3].detail) == "Refused"
    assert lines[4].what == describe(SIGNED_OUT_OTHER)
    assert str(lines[5].detail) == "Failed"


def test_an_earlier_sessions_end_comes_before_the_sign_in_at_its_instant():
    """A sign-in ends the earlier session in its own transaction (one instant);
    the end is listed first whatever order the records were read in."""
    for rows in (
        [(START, "admin_login", None), (START, "admin_reauthenticated", None)],
        [(START, "admin_reauthenticated", None), (START, "admin_login", None)],
    ):
        lines = ordered(audit_lines(rows))
        assert [str(line.what) for line in lines] == [
            "Earlier session ended",
            "Signed in",
        ]
        assert str(lines[0].detail) == "replaced by a new sign-in"


def test_email_lines_name_tag_and_link():
    """Outcome in words, a Testing tag, and a link only where Sent mail has one."""
    rows = [
        email(1, "daily_digest", "delivered"),
        email(2, "leader_email", "pending", finished=False),
        email(3, "initial", "permanent_failure", mode="testing"),
    ]
    lines = email_lines(rows)
    assert [str(line.what) for line in lines] == [
        "Daily Administrator report",
        "Ministry leader email",
        "Invitation",
    ]
    assert lines[0].at == START + 2 * MINUTE and lines[0].detail == "Delivered"
    assert lines[0].message_id == UUID(int=1)
    # Still on its way: listed at the time it was planned. Sent mail lists
    # leader emails (#1562), so this one links its Mail message page too.
    assert lines[1].at == START + 2 * MINUTE
    assert lines[1].message_id == UUID(int=2)
    assert lines[2].detail == "Failed · Testing"
    assert {line.kind for line in lines} == {"emails"}


def test_ordered_ranks_lines_oldest_first():
    """Lines at one instant keep their built order, ranked for the sort."""
    lines = ordered(
        [
            *email_lines([email(5, "operational", "delivered")]),
            *role_changes(ACTIVATIONS[:1], {CONFIGURATIONS[0]: "staff"}),
            *audit_lines([(START, "admin_login", None)]),
        ]
    )
    assert [str(line.what) for line in lines] == [
        "Became a user",
        "Signed in",
        "Operational notice",
    ]
    assert [line.rank for line in lines] == [0, 1, 2]


def test_the_summary_skips_cancelled_email():
    """The newest sign-in, the newest email sent, Ministries in order."""
    rows = [
        (START, "admin_login", None),
        (START + 9 * MINUTE, "admin_logout", None),
        (START + 5 * MINUTE, "admin_login", None),
    ]
    emails = [
        email(1, "daily_digest", "delivered"),
        email(7, "weekly_digest", "cancelled"),
        email(4, "operational", "delivery_unknown"),
    ]
    summary = summarize(rows, emails, ("choir", "Altar servers"))
    # Planned later but finished earlier: the newest line names the email.
    late = email(2, "weekly_digest", "delivered") | {"finished_at": START + 9 * MINUTE}
    assert str(summarize([], [*emails, late]).last_email.what) == (
        "Weekly Administrator report"
    )
    assert summary.last_sign_in == START + 5 * MINUTE
    assert str(summary.last_email.what) == "Operational notice"
    assert str(summary.last_email.detail) == "Not sure it arrived"
    assert summary.ministries == ("Altar servers", "choir")
    nothing = summarize([], [email(7, "weekly_digest", "cancelled")])
    assert nothing.last_sign_in is None and nothing.last_email is None


def test_the_query_takes_only_this_pages_kinds():
    """The Family Timeline's day rules, with a user timeline's Kind choices."""
    assert UserTimelineQuery.parse({}).kind == "all"
    for kind in KINDS:
        assert UserTimelineQuery.parse({"kind": kind}).kind == kind
    for kind in ("reviews", "views", ""):
        with pytest.raises(ValueError):
            UserTimelineQuery.parse({"kind": kind})
    query = UserTimelineQuery.parse(
        {"kind": "emails", "start": "2026-10-04", "zone": "America/New_York"}
    )
    assert query.carry == [
        ("kind", "emails"),
        ("start", "2026-10-04"),
        ("zone", "America/New_York"),
    ]
    with pytest.raises(ValueError):
        UserTimelineQuery.parse({"start": "2026-10-04"})


def test_parse_query_refuses_anything_else():
    """Unknown names, a mode, an unknown sort or size: refused before reading."""
    query, table = parse_query(QueryDict("kind=roles&sort=when&page=2&size=100"))
    assert query.kind == "roles" and table == {
        "sort": "when",
        "page": "2",
        "size": "100",
    }
    for bad in ("mode=testing", "sort=what", "size=7", "user=x", "kind=views"):
        with pytest.raises(ValueError):
            parse_query(QueryDict(bad))


def timeline(count=3):
    """A timeline of ``count`` sign-ins plus one email and one role change."""
    lines = ordered(
        [
            *role_changes(ACTIVATIONS[:1], {CONFIGURATIONS[0]: "staff"}),
            *audit_lines(
                [
                    (START + number * MINUTE, "admin_login", None)
                    for number in range(count)
                ]
            ),
            *email_lines([email(9, "daily_digest", "delivered")]),
        ]
    )
    rows = [(line.at, "admin_login", None) for line in lines if line.kind == "sessions"]
    return UserTimeline(
        PERSON,
        summarize(rows, [email(9, "daily_digest", "delivered")], ("Choir",)),
        lines,
        ({"id": UUID(int=77), "disabled": False},),
    )


def render(records, query=None, values=None, *, show_codes=True):
    """The page as the view renders it, for an Administrator."""
    with using("us_long"):
        context = page_context(
            records,
            query or UserTimelineQuery(),
            values or {},
            START + timedelta(days=1),
            show_codes=show_codes,
        )
        # The admitted principal, which the Family name links read.
        context["request"] = SimpleNamespace(
            principal=Principal(UUID(int=1), frozenset({"administrator"}))
        )
        return render_to_string("stewardship/user-timeline.html", context)


def test_the_page_shows_summary_and_lines():
    """The Name heads the page, never the title; every summary field and line."""
    page = render(timeline())
    assert "Adams, Ann" not in page.split("</title>")[0]
    assert "<h1>Adams, Ann</h1>" in page
    assert "Staff" in page and "/admin/users/" in page and "Edit user" in page
    assert "ann@example.org" in page
    assert "Ministry leader of" in page and "Choir" in page
    assert "Daily Administrator report" in page
    assert f'href="/admin/mail/sent/{UUID(int=9)}/"' in page
    assert re.findall(r'<th scope="row">(?:<a [^>]*>)?([^<]+)', page)[:2] == [
        "Daily Administrator report",
        "Signed in",
    ]
    # One System logs control per recorded Google account, posting its id.
    assert f'name="actor" value="{UUID(int=77)}"' in page
    assert "entries in System logs" in page


def test_a_former_user_has_no_edit_link_and_no_ministries():
    """No longer a user since the removal, no Edit user; fields only if known."""
    former = Person(USER, "ann@example.org", None, None, START)
    records = UserTimeline(former, summarize([], []), (), ())
    page = render(records)
    assert "No longer a user since" in page
    assert "Edit user" not in page and "Ministry leader of" not in page
    # A blank Name: the heading is the address.
    assert "<h1>ann@example.org</h1>" in page
    assert "Never" in page and "None sent" in page
    assert "Nothing recorded for this user yet" in page


def test_the_table_pages_and_filters():
    """Fifty lines a page by default; Kind narrows; filtered out says so."""
    many = timeline(PAGE_SIZE + 10)
    page = render(many)
    assert "Showing 1–50 of 62" in page and "Page 1 of 2" in page
    assert "kind=" not in page.split('id="table"')[1].split("</nav>")[0]
    filtered = render(many, UserTimelineQuery("roles"), {})
    assert "Became a user" in filtered and "Signed in</th>" not in filtered
    nothing = render(
        many,
        UserTimelineQuery(start="2099-01-01", zone="America/New_York"),
        {},
    )
    assert "No lines match these filters" in nothing


# A Family the user belongs to (#1867): one invitation, one submission, and
# the engagement record, read as the Family Timeline reads them.
FAMILY_ID = UUID(int=6)
INVITATION = Email(UUID(int=31), "initial", "Invitation", "delivered", START, START)
SUBMITTED = Submitted(UUID(int=32), START + 30 * MINUTE, no_receipt=True)
ENGAGEMENT = Engagement(START + 25 * MINUTE, "ministry", START + 28 * MINUTE)
RECORDS = Timeline(
    summary([INVITATION], [SUBMITTED]),
    events([INVITATION], [SUBMITTED], engagement=ENGAGEMENT),
    ENGAGEMENT,
)
SHOWN = FamilyPart(
    1234,
    "Adams, Ann and Bob",
    FAMILY_ID,
    "shown",
    Identity(1234, "Adams, Ann and Bob", 101, "ABCD-EFGH", True, "deliverable"),
    RECORDS,
    (True, ""),
)


def with_families(*parts):
    """The sample timeline with ``parts`` and their tagged lines merged in."""
    base = timeline()
    lines = ordered([*base.events, *family_lines(parts)])
    return UserTimeline(base.person, base.summary, lines, base.accounts, parts)


def test_family_lines_are_tagged_and_keep_their_order():
    """Each shown Family's lines carry its name (or DUID) and DUID; others none."""
    gone = FamilyPart(77, None, None, "not_in_campaign")
    held = FamilyPart(78, "Held, Hal", UUID(int=8), "unavailable")
    unnamed = FamilyPart(79, None, FAMILY_ID, "shown", None, RECORDS)
    lines = family_lines([SHOWN, gone, held, unnamed])
    assert {line.family_duid for line in lines} == {1234, 79}
    assert {line.tag for line in lines if line.family_duid == 1234} == {
        "Adams, Ann and Bob"
    }
    assert {str(line.tag) for line in lines if line.family_duid == 79} == {"Family 79"}
    # In the Family's own order, so ties read as on its Timeline.
    assert [line.what for line in lines[: len(RECORDS.events)]] == [
        line.what for line in RECORDS.events
    ]
    assert SHOWN.reason_id != unnamed.reason_id


def test_a_family_member_sees_the_familys_summary_and_lines():
    """A panel per Family linking its Timeline, its fields and tagged lines."""
    page = render(with_families(SHOWN))
    assert "Family member of" in page
    assert (
        f'<a href="/admin/reports/families/{FAMILY_ID}/">Adams, Ann and Bob</a>' in page
    )
    assert "Not found in ParishSoft as a Family member" not in page
    for shown in (
        "Submitted",
        "Invitation, <time",
        "<code>ABCD-EFGH</code>",
        f'action="/admin/reports/families/{FAMILY_ID}/open-form/"',
        "Campaign email can reach this Family",
        "Furthest step reached",
        "Last seen",
    ):
        assert shown in page, shown
    # The Family's lines are tagged; Kind offers Family form activity.
    assert '<span class="status-badge">Adams, Ann and Bob</span>' in page
    assert '<option value="form">Family form activity</option>' in page
    # The Family email links its Mail message page.
    assert f'href="/admin/mail/sent/{INVITATION.id}/"' in page
    # Without the right to see codes: no code, no Open form.
    hidden = render(with_families(SHOWN), show_codes=False)
    assert "ABCD-EFGH" not in hidden and "Open form" not in hidden


def test_family_parts_without_records_say_why():
    """Not in the campaign, unavailable, or no Family at all."""
    gone = FamilyPart(77, "Gone, Gus")
    held = FamilyPart(78, "Held, Hal", UUID(int=8), "unavailable")
    page = render(with_families(gone, held))
    assert "Not in the current campaign, so there are no Family form records." in page
    assert "This Family's form records are unavailable right now" in page
    # Neither has lines, so Kind does not offer Family form activity.
    assert 'value="form"' not in page
    # No link without a campaign record; the held one still links.
    assert "Gone, Gus</h2>" in page
    assert f'href="/admin/reports/families/{UUID(int=8)}/"' in page
    alone = render(timeline())
    assert (
        "Not found in ParishSoft as a Family member, so there are no Family form "
        "records." in alone
    )
    assert "Family member of" not in alone and 'value="form"' not in alone


def test_family_form_activity_narrows_to_the_familys_lines():
    """Kind form keeps only the Family's form lines, tagged."""
    page = render(with_families(SHOWN), UserTimelineQuery("form"), {})
    rows = re.findall(r'<th scope="row">(?:<a [^>]*>)?([^<]+)', page)
    assert [row.strip() for row in rows] == [
        "Submitted a response",
        "Reached the furthest step so far",
        "Got past the first step",
    ]
    assert "Signed in</th>" not in page and "Became a user" not in page


def test_family_form_activity_is_offered_only_with_family_form_lines():
    """A Family with only an email offers no Family form activity, and an
    address that still names it reads as All (#1887 review)."""
    mailed = replace(
        SHOWN,
        records=Timeline(summary([INVITATION], []), events([INVITATION], []), None),
    )
    page = render(with_families(mailed))
    assert '<span class="status-badge">Adams, Ann and Bob</span>' in page
    assert 'value="form"' not in page
    fallen = render(with_families(mailed), UserTimelineQuery("form"), {})
    assert '<option value="all" selected>' in fallen
    assert "No lines match these filters" not in fallen
    assert "Signed in</th>" in fallen


def test_the_family_panel_carries_the_testing_test_link():
    """In Testing, Open form's reason links the chosen-Family test page, as
    on the Family's Timeline."""
    held = replace(SHOWN, open_form=(False, "testing"), test_url="/admin/test-here/")
    page = render(with_families(held))
    assert "Not available in Testing mode" in page
    assert '<a href="/admin/test-here/">Try the Family form as a chosen Family</a>' in (
        page
    )
    assert "Try the Family form" not in render(
        with_families(replace(held, test_url=None))
    )
