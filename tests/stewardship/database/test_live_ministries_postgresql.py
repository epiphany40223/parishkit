"""Changing a live campaign's Ministries (#342): guard, answers, reports, audit."""

import re
from html import unescape
from uuid import uuid4

import pytest
from django.db import DatabaseError

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns import admission, live_ministries
from parishkit.stewardship.workflows.models import MinistryRequest

from .auth_builders import signed_in
from .campaign_builders import change, close_campaign
from .test_ministry_followup_postgresql import read as followup
from .test_ministry_packets_postgresql import packet, sections
from .test_ministry_reports_postgresql import page, setup
from .test_ministry_responses_postgresql import respond, revisit
from .test_policy_postgresql import user
from .test_response_http_postgresql import answers_for

pytestmark = pytest.mark.django_db(transaction=True)


def select(harness, duids, *, patches=(), **values):
    """Request and install one live campaign change; return the receipt or error."""
    store = harness.service.store
    try:
        return change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(harness.campaign.pk),
                    "values": {"ministry_duids": list(duids), **values},
                },
                *patches,
            ],
        )
    except (ConfigError, DatabaseError) as error:
        return error


def selected(harness):
    """The current campaign's applied Ministry selections."""
    harness.campaign.refresh_from_db()
    return harness.campaign.active_configuration.values["ministry_duids"]


def applied(result):
    """Whether a live change actually applied."""
    return getattr(result, "state", None) == "applied"


def open_requests(duid):
    """The current (not replaced or withdrawn) requests for one Ministry."""
    return MinistryRequest.objects.filter(ministry_duid=duid).exclude(
        state__in=("superseded", "cancelled")
    )


def summaries(harness):
    """Report summary rows keyed by DUID."""
    return {row["duid"]: row for row in page(harness)["summaries"]}


def test_removal_keeps_answers_marks_reports_and_readding_restores(
    response_service, google
):
    """Removing never deletes or hides answers; adding back shows them again."""
    harness = setup(response_service)
    assert harness.campaign.structural_locked and harness.campaign.state == "active"
    request = open_requests(9).get()
    assert applied(select(harness, [4]))
    assert selected(harness) == [4]
    request.refresh_from_db()
    assert request.state == "new" and open_requests(9).count() == 1
    rows = summaries(harness)
    assert rows[9]["in_campaign"] is False and rows[9]["joining"] == 1
    assert rows[4]["in_campaign"] is True
    # Staff follow-up keeps the request, marked, until staff close it.
    admin = user("admin@example.org")
    staff = Principal(admin.pk, frozenset({"staff"}), frozenset())
    queue = followup(harness, staff)
    assert {row["ministry_duid"]: row["in_campaign"] for row in queue["rows"]} == {
        9: False,
        4: True,
    }
    assert {row["duid"]: row["in_campaign"] for row in queue["ministries"]}[9] is False
    # The packet still offers the removed Ministry, marked.
    _, captured = sections(packet(harness, admin.pk, (9,)))
    assert captured[9]["in_campaign"] is False and len(captured[9]["rows"]) == 1
    # The Family no longer sees it; resubmitting does not withdraw the request.
    form = revisit(harness)
    assert [option["id"] for option in form["ministries"]["options"]] == [4]
    respond(harness, form, answers_for(form))
    assert open_requests(9).get().state == "new"
    # Adding it back restores the answer on the form and in reports.
    assert applied(select(harness, [4, 9]))
    assert summaries(harness)[9]["in_campaign"] is True
    form = revisit(harness)
    assert form["ministries"]["members"]["3"]["join"] == [9]


@pytest.mark.parametrize("bypass", [False, True])
def test_guard_allows_only_selection_changes_on_a_live_campaign(
    response_service, monkeypatch, bypass
):
    """Every other structural field stays locked, beside a selection or alone.

    With the whole Python campaign admission bypassed, SQL's own structural
    lock still refuses the other field, so the exemption covers only
    ``ministry_duids``.
    """
    harness = setup(response_service)
    if bypass:
        monkeypatch.setattr(admission, "validate_installation", lambda *a, **k: None)
    result = select(harness, [4], additional_information=False)
    assert not applied(result) and selected(harness) == [4, 9]
    if bypass:
        assert "Live structural settings are locked" in str(result)
        return
    assert "structural settings are locked" in str(
        admission_error(harness, [4], start_date="2026-01-02")
    )
    result = select(harness, [4, 9], modules=["ministry"])
    assert not applied(result) and selected(harness) == [4, 9]


def admission_error(harness, duids, **values):
    """Python admission's refusal of one candidate, without installing it."""
    document = harness.service.store.active().document()
    row = next(
        item
        for item in document["sections"]["campaigns"]
        if item["id"] == str(harness.campaign.pk)
    )
    row["values"].update(ministry_duids=list(duids), **values)
    with pytest.raises(ConfigError) as error:
        admission.validate_installation(document)
    return error.value


SQL_REFUSAL = "Live Ministry selections can only add current active Ministries"


def refused(harness, result, refusals, expected=(4,), *, message):
    """A refused change leaves selections alone, refused by the expected layer.

    Python's refusal is a failed request (``refusals`` records the error the
    admission check raised). With that check bypassed, the installer reaches
    the SQL guard, whose refusal stops it; such a database then needs operator
    recovery, so each bypassed case is a separate test.
    """
    assert not applied(result), result
    assert selected(harness) == list(expected)
    if refusals is None:
        assert SQL_REFUSAL in str(result)
    else:
        assert result.state == "failed"
        assert result.failure_code == "invalid_candidate"
        assert [str(error) for error in refusals] == [message]


def checking(monkeypatch, *, bypass):
    """Bypass the Python admission check, or record what it refuses."""
    if bypass:
        monkeypatch.setattr(live_ministries, "check_live_selection", lambda *a: None)
        return None
    refusals, original = [], live_ministries.check_live_selection

    def spy(*args):
        """Record the Python refusal before the installer reports it."""
        try:
            return original(*args)
        except ConfigError as error:
            refusals.append(error)
            raise

    monkeypatch.setattr(live_ministries, "check_live_selection", spy)
    return refusals


@pytest.mark.parametrize("bypass", [False, True])
@pytest.mark.parametrize("case", ["missing", "inactive"])
def test_added_ministry_must_be_current_and_active(
    response_service, monkeypatch, bypass, case
):
    """Python refuses first; with Python bypassed, the SQL guard still refuses."""
    harness = setup(response_service)
    assert applied(select(harness, [4]))
    refusals = checking(monkeypatch, bypass=bypass)
    message = "Only current, active Ministries can be added."
    if case == "missing":
        # DUID 123 is not in the ParishSoft catalog.
        refused(harness, select(harness, [4, 123]), refusals, message=message)
    else:
        # In the catalog, but marked inactive in the same change.
        inactive = {
            "operation": "add",
            "section": "ministries",
            "id": str(uuid4()),
            "values": {"organization_id": 12345, "ministry_duid": 9, "active": False},
        }
        result = select(harness, [4, 9], patches=[inactive])
        refused(harness, result, refusals, message=message)
    if not bypass:
        # The current, active Ministry can be added.
        assert applied(select(harness, [4, 9]))


@pytest.mark.parametrize("bypass", [False, True])
def test_closed_campaign_selections_cannot_change(
    response_service, monkeypatch, bypass
):
    """Once closed, even removal is refused, in Python and in SQL."""
    harness = setup(response_service)
    close_campaign(harness.campaign, uuid4())
    assert harness.campaign.state == "closed"
    refusals = checking(monkeypatch, bypass=bypass)
    refused(
        harness,
        select(harness, [4]),
        refusals,
        (4, 9),
        message="Campaign structural settings are locked.",
    )


def post(browser, url, values):
    """A CSRF-protected Admin form post."""
    return browser.post(
        url, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def test_editor_previews_impact_and_audits_the_change(response_service, google):
    """The review counts what removal touches; confirming records who and what."""
    harness = setup(response_service)
    browser, _ = signed_in()
    url = f"/admin/campaign/{harness.campaign.pk}/ministries"
    settings = browser.get(f"/admin/campaign/{harness.campaign.pk}/settings")
    assert url.encode() in settings.content
    form = browser.get(url)
    assert form.status_code == 200, form.content
    digest = re.search(r'name="base_digest" value="([0-9a-f]+)"', form.content.decode())
    review = post(
        browser,
        url,
        {"action": "preview", "ministry_duids": "4", "base_digest": digest.group(1)},
    )
    assert review.status_code == 200, review.content
    body = unescape(review.content.decode())
    assert "Ministries to remove" in body and "Food pantry" in body
    # One submitted join request for Ministry 9, still awaiting follow-up.
    assert re.search(r"Food pantry \(DUID 9\)</td><td>1</td><td>1</td>", body)
    assert "Families with a form open now" in body
    token = re.search(r'name="preview" value="([^"]+)"', review.content.decode())
    before = set(ConfigurationChangeRequest.objects.values_list("pk", flat=True))
    confirmed = post(
        browser, url, {"action": "confirm", "preview": unescape(token.group(1))}
    )
    assert confirmed.status_code == 302, confirmed.content
    request = ConfigurationChangeRequest.objects.exclude(pk__in=before).get()
    audit = AuditContext.objects.get(event__event_type="campaign_ministries_requested")
    assert audit.event.subject_id == request.pk
    assert audit.event.actor_id == request.actor_id
    assert audit.event.campaign_reference == harness.campaign.pk
    assert audit.context == {
        "previous_ministry_duids": [4, 9],
        "ministry_duids": [4],
        "added_ministry_duids": [],
        "removed_ministry_duids": [9],
    }
    receipt = install_request(
        harness.service.store, request_id=request.pk, correlation_id=uuid4()
    )
    assert receipt.state == "applied" and selected(harness) == [4]
    # Adding a Ministry that is not in the catalog is refused at review.
    form = browser.get(url)
    digest = re.search(r'name="base_digest" value="([0-9a-f]+)"', form.content.decode())
    refused = post(
        browser,
        url,
        {
            "action": "preview",
            "ministry_duids": ["4", "123"],
            "base_digest": digest.group(1),
        },
    )
    assert refused.status_code == 400
