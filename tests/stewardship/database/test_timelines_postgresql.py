"""A Family's Timeline over HTTP, read as the restricted web login (#477, #1570).

The campaign is the response-metrics ``funnel`` fixture: five eligible
Families, the corpus Family (DUID 1) signed in through its rehearsal
credential. In Testing it opens the form, gets past the first step and
submits before the invitations go out, so its invitation is skipped; the
others are invited. After the real activation it signs in live, opens the
form and signs out. The page shows Production records only (#1570); it is
checked for Administrators (the full timeline and its filters) and Staff
(the summary only), with ``no-store``, the denials and the audit.
"""

import re
from datetime import UTC, timedelta
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.engagement_models import FamilyEngagement
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import timeline_views
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_report_workspace_postgresql import read as get
from .test_response_metrics_postgresql import (
    dispatch_all,
    funnel,  # noqa: F401 (the fixture)
    live_login,
    open_form,
    prepare_all,
    progress_to,
    respond,
)

pytestmark = pytest.mark.django_db(transaction=True)


def lines(body):
    """The timeline's "What happened" cells, in row order, links unwrapped."""
    return [
        re.sub(r"<[^>]+>", "", cell)
        for cell in re.findall(r'<th scope="row">(.*?)</th>', body.decode())
    ]


def as_web(client, path):
    """GET ``path`` as the restricted web login, through the real read guard."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return get(client, path)


def test_timeline_for_administrators_and_staff(
    request, auth_service, google, monkeypatch
):
    """Production records only (#1570); the full view for Admins, Staff's summary."""
    harness, _epoch = request.getfixturevalue("funnel")
    families = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    corpus, invited = (
        reverse("admin:timeline", args=[families[1]]),
        reverse("admin:timeline", args=[families[11]]),
    )
    due = ScheduleDefinition.objects.get(kind="initial").current_revision.due_at

    # Testing: the corpus Family opens the form, gets past the first step
    # and submits before dispatch; the others are invited.
    with campaign_clock(due):
        rehearsal = prepare_all(harness, "testing")
    open_form(harness)
    progress_to(harness, "census")
    respond(harness)
    states = dispatch_all(harness, rehearsal, families, due)
    assert states[1] == "cancelled" and states[11] == "delivered"

    admin, login = signed_in()
    assert login.status_code == 302
    response, body = as_web(admin, corpus)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    snapshot = SourceCurrent.objects.get().snapshot_id
    name = snapshot_family_names(snapshot, [1], NAME_NOT_RECORDED)[1]
    # The heading names the Family alone, the hub's (#1678); the title never
    # does, so browser history does not keep it. The old page name is gone.
    assert f"<h1>{name}</h1>".encode() in body
    assert b'role="tablist" aria-label="Family information"' in body
    assert name.encode() not in body.split(b"</title>")[0]
    assert b"<title>Timeline" in body and b"Family timeline" not in body
    # Production only: every record so far is the Testing rehearsal's, and
    # the Testing sign-in is left out with them. No mode control is drawn.
    assert b"Not yet" in body and b"None sent yet" in body
    assert b"Nothing recorded for this Family yet." in body
    assert b"mode-testing" not in body and b'name="mode"' not in body
    # Testing mode: the live code is shown, but Open form is unavailable.
    family = FamilyCampaign.objects.get(pk=families[1])
    code = harness.rings.general.decrypt(
        family.code_ciphertext, context=code_context(family.pk)
    ).decode()
    assert f"<code>{code}</code>".encode() in body
    assert b"accepts only Testing codes" in body and b"data-open-form" not in body
    # Administrators may open the chosen-Family test page, so they get its link.
    assert b"Try the Family form as a chosen Family</a>" in body
    # The Testing invitation is not the Production last email either.
    _, body = as_web(admin, invited)
    assert b"None sent yet" in body
    for invalid in (
        # The Testing view was removed (#1570): any mode is an unknown filter.
        "?mode=testing",
        "?mode=production",
        "?kind=everything",
        "?start=2026-10-01",
        "?start=2026-10-02&end=2026-10-01&zone=UTC",
        "?name=x",
        "?sort=what",
        "?size=25",
    ):
        assert as_web(admin, corpus + invalid)[0].status_code == 400, invalid
    # An unknown Family is refused, and leaves no audit row (checked below).
    unknown = uuid4()
    assert (
        as_web(admin, reverse("admin:timeline", args=[unknown]))[0].status_code == 403
    )
    # A role without Family codes gets no code and no Open form. No real role
    # has CAMPAIGN_REPORT without FAMILY_CODES, so the view's check is
    # narrowed for one request.
    real = timeline_views.allows
    monkeypatch.setattr(
        timeline_views,
        "allows",
        lambda principal, capability, **scope: (
            capability != Capability.FAMILY_CODES
            and real(principal, capability, **scope)
        ),
    )
    _, body = as_web(admin, corpus)
    assert f"<code>{code}</code>".encode() not in body
    assert b"Open form" not in body and b"Family code" not in body
    monkeypatch.setattr(timeline_views, "allows", real)

    # Staff, while the system is still in Testing mode: the code, but no link
    # to the chosen-Family test page they may not open.
    store = auth_service.store
    staff = address("staff@example.org", roles=("staff",))
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **staff}],
        ).state
        == "applied"
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = as_web(browser, corpus)
    assert response.status_code == 200 and f"<code>{code}</code>".encode() in body
    assert b"accepts only Testing codes" in body
    assert b"Try the Family form" not in body

    # Production: after the real activation the corpus Family signs in live,
    # opens the form and signs out.
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    live = live_login(harness, 1)
    open_form(live)
    token = live.client.cookies["pk_family_csrf"].value
    assert (
        live.client.post("/family/logout", {"csrfmiddlewaretoken": token}).status_code
        == 302
    )
    _, body = as_web(admin, corpus)
    shown = lines(body)
    # Two live sign-ins (the activation's own, as in the funnel test, and
    # this one) and this one's sign-out; the Testing sign-in, form open and
    # submission before activation are not among them.
    assert shown.count("Signed in") == 2 and shown.count("Signed out") == 1
    assert "Opened the form" in shown
    assert "Submitted a response" not in shown and "Invitation not sent" not in shown
    # Newest first: the sign-out leads; choosing When lists it oldest first.
    assert shown[0] == "Signed out"
    _, oldest = as_web(admin, corpus + "?size=all&sort=when")
    assert lines(oldest) == shown[::-1]
    # Open form is a POST hand-off (#529); the code is in no URL.
    assert f'action="{corpus}open-form/" target="_blank"'.encode() in body
    assert b"#code=" not in body

    # The filters narrow the lines; the summary still covers everything.
    _, body = as_web(admin, corpus + "?kind=sessions")
    assert set(lines(body)) == {"Signed in", "Signed out"}
    _, body = as_web(admin, corpus + "?kind=form")
    assert lines(body) == ["Opened the form"]
    today = database_now().astimezone(UTC).date()
    days = f"&start={today}&end={today + timedelta(days=1)}&zone=UTC"
    _, body = as_web(admin, corpus + "?kind=all" + days)
    assert sorted(lines(body)) == sorted(shown)
    later = today + timedelta(days=2)
    _, body = as_web(admin, corpus + f"?start={later}&zone=UTC&sort=when&size=all")
    assert lines(body) == [] and b"No lines match these filters." in body
    # The When heading keeps the filters.
    assert f"start={later}&amp;zone=UTC&amp;size=all&amp;sort=-when".encode() in body

    # Staff get the summary only (signing in afresh): no table, no filters.
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = as_web(browser, corpus)
    assert response.status_code == 200
    assert f"<code>{code}</code>".encode() in body and b"data-open-form" in body
    # Staff see the Last portal action too (Administrator decision,
    # 2026-10-18): the live sign-out above, from the same records.
    assert b"Last portal action: Signed out, <time" in body
    for absent in (
        b"Timeline</h2>",
        b"/admin/mail/sent/",
        b"table-filters",
        b"Signed in",
    ):
        assert absent not in body, absent
    assert as_web(browser, corpus + "?mode=testing")[0].status_code == 400

    # A Ministry leader may not open a Timeline at all, and the refusal is
    # not audited as a view.
    leader = address("leader@example.org", roles=("ministry_leader",))
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **leader}],
        ).state
        == "applied"
    )
    google[0].update(email="leader@example.org", sub="synthetic-leader")
    browser, login = signed_in()
    assert login.status_code == 302
    assert as_web(browser, corpus)[0].status_code == 403
    assert as_web(browser, corpus + "?kind=emails")[0].status_code == 403

    # Every view is audited: the Family viewed is the subject (its opaque
    # record id), the campaign is kept, and no name, filter or value is
    # copied. Ten pages of the corpus Family shown: the Administrator's
    # in Testing mode, the no-codes view, Staff's in Testing mode, and after
    # activation the Administrator's, oldest first, the four filtered views
    # and Staff's. Refusals (the 400s, the leader's 403s) are not audited, and
    # neither is a failed Family lookup: no row pairs a campaign with a
    # Family that is not in it.
    events = AuditEvent.objects.filter(event_type="family_timeline_viewed")
    viewed = events.filter(
        subject_id=families[1], campaign_reference=harness.campaign.pk
    )
    assert viewed.count() == 10
    assert not events.exclude(campaign_reference=harness.campaign.pk).exists()
    assert not events.filter(subject_id=unknown).exists()
    assert events.filter(subject_id=families[11]).count() == 1
    assert not events.filter(subject_id=harness.campaign.pk).exists()
    assert set(events.values_list("auditcontext__context", flat=True).first()) <= {
        "outcome"
    }


def test_last_portal_action_ignores_email_reader_fetches(request, auth_service, google):
    """The hub's Last portal action counts only the Family's own steps (#1678).

    An email reader fetching the personal link signs the Family in and opens
    its form exactly as the Family would (``live_login`` and ``open_form``),
    so neither counts, before or after a real action. Moving through the
    form does, and so does Sign out; each is read from the real records the
    Timeline reads, through the restricted web login.
    """
    from datetime import datetime

    harness, _epoch = request.getfixturevalue("funnel")
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    page = reverse("admin:timeline", args=[family.pk])
    admin, login = signed_in()
    assert login.status_code == 302

    def shown():
        """The header strip's Last portal action: (words, instant or None)."""
        response, body = as_web(admin, page)
        assert response.status_code == 200
        text = body.decode()
        value = text[text.index("Last portal action: ") :].split("</p>")[0]
        words = value.removeprefix("Last portal action: ").split(", <time")[0]
        at = re.search(r'<time datetime="([^"]+)"', value)
        return words, datetime.fromisoformat(at.group(1)) if at else None

    # An email reader's fetch: a sign-in, a form open and, when the reader
    # runs the page's script, the first step's report. None is an action.
    reader = live_login(harness, 1)
    open_form(reader)
    progress_to(reader, "welcome")
    assert (
        FamilyEngagement.objects.get(family_id=family.pk, mode="live").furthest_section
        == "welcome"
    )
    assert shown() == ("None recorded", None)
    # The Family itself gets past the first step to the Census step.
    live = live_login(harness, 1)
    open_form(live)
    progress_to(live, "census")
    words, at = shown()
    assert words == "Reached the furthest step so far"
    assert at == (
        FamilyEngagement.objects.get(family_id=family.pk, mode="live").furthest_at
    )
    # It signs out: that is its newest action.
    token = live.client.cookies["pk_family_csrf"].value
    assert (
        live.client.post("/family/logout", {"csrfmiddlewaretoken": token}).status_code
        == 302
    )
    out = AuditEvent.objects.get(event_type="family_logout", actor_id=family.pk)
    assert shown() == ("Signed out", out.created_at)
    # Another fetch after it, and one more form open: still the sign-out.
    later = live_login(harness, 1)
    open_form(later)
    progress_to(later, "welcome")
    assert shown() == ("Signed out", out.created_at)
    assert AuditEvent.objects.filter(
        event_type="family_login", actor_id=family.pk, created_at__gt=out.created_at
    ).exists()


def test_a_family_is_refused_under_another_real_campaign(
    response_service, auth_service, google
):
    """The page names a Family and a campaign; a mismatch is refused.

    A real successor campaign (created after the first is archived and the
    deployment returns to Testing, the only way a second campaign exists) is
    itself reportable. The first campaign's Family does not open under the
    successor (the ``campaign_id`` filter refuses), and, until the
    single-campaign change (#145), not under its own retained campaign either:
    reports show the current campaign only, so that is gone (410).
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.lifecycle import Action
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.campaigns.runtime import return_to_testing
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from ..campaign_factory import campaign as campaign_record
    from .campaign_builders import add_draft, admit_test_work, close_campaign, command
    from .test_taskrun_postgresql import act

    harness = activate_response_service(response_service)
    family = FamilyCampaign.objects.filter(campaign=harness.campaign).first()
    actor = uuid4()
    close_campaign(harness.campaign, actor)
    for row in TaskRun.objects.filter(state="running"):
        act(_status(row), "permanent_failure")
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        command(harness.campaign, actor, Action.ARCHIVE)
        return_to_testing(
            campaign_id=harness.campaign.pk,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        store = auth_service.store
        result, row, _ = add_draft(
            store, store.active(), actor, campaign_record(name="Successor")
        )
        assert result.state == "applied" and Campaign.objects.count() == 2
        # The successor is the current campaign, which reports now show.
        current = SystemConfiguration.objects.get().current_campaign_id
        assert str(current) == str(row["id"])
        admin, login = signed_in()
        assert login.status_code == 302
        assert as_web(admin, reverse("admin:response_dashboard"))[0].status_code == 200
        # The Family's address (which now means the current campaign, the
        # successor) is refused, and the refusal leaves no audit row.
        other = reverse("admin:timeline", args=[family.pk])
        assert as_web(admin, other)[0].status_code == 403
        events = AuditEvent.objects.filter(event_type="family_timeline_viewed")
        assert not events.filter(subject_id=family.pk).exists()


def test_session_ends_follow_their_sign_ins_mode():
    """An end is kept exactly when its session's sign-in is a Production one.

    The switch to Testing ends a Production session at the switch instant,
    which already counts as Testing, so the end must be paired with its
    sign-in by Family session, not attributed by its own instant; a Testing
    session's end is dropped. A Sign out in a session Staff opened through
    Open form says so, as its sign-in does, so it is no portal action of
    the Family's (#1678). The rows are seeded past the append-only
    guards (``unguarded``) so the instants are exact.
    """
    from types import SimpleNamespace

    from parishkit.stewardship.campaigns.runtime_models import RuntimeTransition
    from parishkit.stewardship.reports.timelines import read_sessions

    from .auth_builders import unguarded

    family, production, testing, assisted = uuid4(), uuid4(), uuid4(), uuid4()
    start = database_now() + timedelta(days=1)
    switch, signed_in_testing = start + timedelta(hours=1), start + timedelta(hours=2)
    staff_in, staff_out = start + timedelta(minutes=10), start + timedelta(minutes=20)
    seeded = [
        (start, production, "family_login"),
        (staff_in, assisted, "family_login"),
        (staff_in, assisted, "family_assisted_login"),
        (staff_out, assisted, "family_logout"),
        (switch, production, "family_session_ended"),
        (signed_in_testing, testing, "family_login"),
        (signed_in_testing + timedelta(minutes=5), testing, "family_session_ended"),
    ]
    # Production just before the first sign-in, whatever came earlier, then
    # the switch to Testing.
    modes = [
        (start - timedelta(minutes=1), "testing", "production"),
        (switch, "production", "testing"),
    ]
    with unguarded():
        for number, (at, before, after) in enumerate(modes):
            RuntimeTransition.objects.create(
                created_at=at,
                request_id=uuid4(),
                expected_version=10**12 + number,
                action="test",
                before_mode=before,
                after_mode=after,
            )
        for at, subject, kind in seeded:
            AuditEvent.objects.create(
                created_at=at, event_type=kind, subject_id=subject, actor_id=family
            )
    scope = SimpleNamespace(mode="production", rehearsal_epoch_id=None)
    sign_ins, ends = read_sessions(family, scope, "testing", (None, None))
    assert sign_ins == [(start, False), (staff_in, True)]
    assert ends == [
        (staff_out, "family_logout", True),
        (switch, "family_session_ended", False),
    ]
