"""The Family timeline over HTTP, read as the restricted web login (#477, PR 6).

The campaign is the response-metrics ``funnel`` fixture: five eligible
Families, the corpus Family (DUID 1) signed in through its rehearsal
credential. In Testing it opens the form, gets past the first step and
submits before the invitations go out, so its invitation is skipped; the
others are invited. After the real activation it signs in live. The page is
checked for Administrators (full timeline, both modes) and Staff (summary
only, Production only), with ``no-store``, the denials and the audit.
"""

import re
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import family_timeline_views
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.source.snapshot_names import snapshot_family_names

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
    """Full timeline in both modes for Admins; the reduced view for Staff."""
    harness, _epoch = request.getfixturevalue("funnel")
    families = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    base = f"/admin/reports/{harness.campaign.pk}/families/"
    corpus, invited = base + f"{families[1]}/", base + f"{families[11]}/"
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
    response, body = as_web(admin, corpus + "?mode=testing")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    snapshot = SourceCurrent.objects.get().snapshot_id
    name = snapshot_family_names(snapshot, [1], "Family")[1]
    assert f"<strong>{name}</strong>".encode() in body
    # The name is never in the title, so browser history does not keep it.
    assert name.encode() not in body.split(b"</title>")[0]
    assert b"never counted in Production" in body
    shown = lines(body)
    for expected in (
        "Signed in",
        "Opened the form",
        "Got past the first step",
        "Reached the furthest step so far",
        "Submitted a response",
        "Invitation not sent",
    ):
        assert expected in shown, (expected, shown)
    # Newest first by default: the submission is listed above the sign-in.
    assert shown.index("Submitted a response") < shown.index("Signed in")
    # Choosing When lists it oldest first.
    _, oldest = as_web(admin, corpus + "?mode=testing&size=all&sort=when")
    oldest = lines(oldest)
    assert sorted(oldest) == sorted(shown)
    assert oldest.index("Signed in") < oldest.index("Submitted a response")
    # The skipped invitation's cancelled email is not listed twice: its
    # "already responded" line stands for it, and it is never the last email.
    message = rehearsal[families[1]]
    assert f"/admin/deliveries/{message.pk}".encode() not in body
    assert b"Not sent (cancelled)" not in body
    # The last email sent is the submission's receipt, still on its way.
    assert re.search(rb"Submission receipt, <time[^>]*>[^<]*</time>: <strong>", body)
    assert b"Census, <time" in body
    # Testing mode: the live code is shown, but Open form is unavailable.
    family = FamilyCampaign.objects.get(pk=families[1])
    code = harness.rings.general.decrypt(
        family.code_ciphertext, context=code_context(family.pk)
    ).decode()
    assert f"<code>{code}</code>".encode() in body
    assert b"accepts only Testing codes" in body and b"data-open-form" not in body
    # Administrators may open the chosen-Family test page, so they get its link.
    assert b"Try the Family form as a chosen Family</a>" in body
    # Production has none of it: the sign-in happened in Testing mode.
    _, body = as_web(admin, corpus)
    assert b"Not yet" in body and b"None sent yet" in body
    assert b"Nothing recorded for this Family yet." in body
    # An invited Family: its delivered invitation is the last email.
    _, body = as_web(admin, invited + "?mode=testing")
    assert re.search(rb"Invitation, <time[^>]*>[^<]*</time>: <strong>Delivered", body)
    assert b"Not yet" in body
    for invalid in (
        "?mode=live",
        "?mode=testing&mode=production",
        "?name=x",
        "?sort=what",
        "?size=25",
    ):
        assert as_web(admin, corpus + invalid)[0].status_code == 400
    # An unknown Family, or a Family under another campaign, is refused and
    # leaves no audit row (checked below).
    unknown, elsewhere = uuid4(), uuid4()
    assert as_web(admin, base + f"{unknown}/")[0].status_code == 403
    other = f"/admin/reports/{elsewhere}/families/{families[1]}/"
    assert as_web(admin, other)[0].status_code == 403
    # A role without Family codes gets no code and no Open form. No real role
    # has CAMPAIGN_REPORT without FAMILY_CODES, so the view's check is
    # narrowed for one request.
    real = family_timeline_views.allows
    monkeypatch.setattr(
        family_timeline_views,
        "allows",
        lambda principal, capability, **scope: (
            capability != Capability.FAMILY_CODES
            and real(principal, capability, **scope)
        ),
    )
    _, body = as_web(admin, corpus)
    assert f"<code>{code}</code>".encode() not in body
    assert b"Open form" not in body and b"Family code" not in body
    monkeypatch.setattr(family_timeline_views, "allows", real)

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

    # Production: after the real activation the corpus Family signs in live.
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    live = live_login(harness, 1)
    open_form(live)
    _, body = as_web(admin, corpus)
    shown = lines(body)
    # Two live sign-ins (the activation's own, as in the funnel test, and
    # this one); the Testing sign-in before activation is not among them.
    assert shown.count("Signed in") == 2 and "Opened the form" in shown
    assert "Submitted a response" not in shown
    # The live code opens the form, carried only in the URL fragment.
    assert f'href="/#code={code}" target="_blank"'.encode() in body
    # The rehearsal is over, so Testing has nothing to show.
    _, body = as_web(admin, corpus + "?mode=testing")
    assert b"no Testing rehearsal now" in body

    # Staff get the summary only, in Production only (signing in afresh).
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = as_web(browser, corpus)
    assert response.status_code == 200
    assert f"<code>{code}</code>".encode() in body and b"data-open-form" in body
    for absent in (
        b"Timeline</h2>",
        b"/admin/deliveries/",
        b"data-in-place=",
        b"Signed in",
    ):
        assert absent not in body, absent
    assert as_web(browser, corpus + "?mode=testing")[0].status_code == 403

    # Every view is audited: the Family viewed is the subject (its opaque
    # record id), the campaign is kept, and no name or value is copied.
    events = AuditEvent.objects.filter(event_type="family_timeline_viewed")
    viewed = events.filter(
        subject_id=families[1], campaign_reference=harness.campaign.pk
    )
    # Eight pages shown: the Administrator's Testing and Production views
    # before and after activation, the oldest-first view, the no-codes view,
    # and Staff's two.
    # Refusals before admission (400s, Staff Testing) are not audited, and
    # neither is a failed Family lookup: no row pairs a campaign with a
    # Family that is not in it.
    assert viewed.count() == 8
    assert not events.exclude(campaign_reference=harness.campaign.pk).exists()
    assert not events.filter(subject_id=unknown).exists()
    assert not events.filter(campaign_reference=elsewhere).exists()
    assert events.filter(subject_id=families[11]).count() == 1
    assert not events.filter(subject_id=harness.campaign.pk).exists()
    assert set(events.values_list("auditcontext__context", flat=True).first()) <= {
        "outcome"
    }


def test_a_family_is_refused_under_another_real_campaign(
    response_service, auth_service, google
):
    """The page names a Family and a campaign; a mismatch is refused.

    A real successor campaign (created after the first is archived and the
    deployment returns to Testing, the only way a second campaign exists) is
    itself reportable, and the first campaign's Family opens under its own
    campaign, but not under the successor: the ``campaign_id`` filter refuses.
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
        successor = row["id"]
        admin, login = signed_in()
        assert login.status_code == 302
        assert (
            as_web(admin, f"/admin/reports/{successor}/responses/")[0].status_code
            == 200
        )
        own = f"/admin/reports/{harness.campaign.pk}/families/{family.pk}/"
        assert as_web(admin, own)[0].status_code == 200
        other = f"/admin/reports/{successor}/families/{family.pk}/"
        assert as_web(admin, other)[0].status_code == 403
        # The refused pairing leaves no audit row; the real view leaves one.
        events = AuditEvent.objects.filter(event_type="family_timeline_viewed")
        assert not events.filter(campaign_reference=successor).exists()
        assert (
            events.filter(
                subject_id=family.pk, campaign_reference=harness.campaign.pk
            ).count()
            == 1
        )
