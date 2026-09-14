"""Real CSRF/session HTTP entry, final submission and safe revisit projection."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.models import (
    FamilyFormBaseline,
    ProposedChange,
    Submission,
)

from .campaign_builders import campaign_clock
from .campaign_builders import change as change_configuration
from .response_builders import response_source
from .test_family_auth_postgresql import login
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def post(client, path, payload):
    """Use the real disjoint cookie and CSRF token, not a bypassed request."""
    return client.post(
        path,
        payload,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )


def load_form(harness):
    """The baseline request contains only entry consent, never response fields."""
    response = post(harness.client, "/family/form", {"testing_acknowledged": True})
    assert response.status_code == 200, response.content
    return response.json()["form"]


def answers_for(form):
    """Construct the complete declared aggregate exactly as the in-memory UI does."""
    return {
        "family": {
            **{field["name"]: field["value"] for field in form["household"]["fields"]},
            "mailing_same_as_home": form["household"]["mailing_same_as_home"],
        }
        if form["household"]
        else {},
        "members": {
            member["id"]: member["request"]
            or {field["name"]: field["value"] for field in member["fields"]}
            for member in form["members"]
        },
        "proposed_members": {
            member["id"]: {field["name"]: field["value"] for field in member["fields"]}
            for member in form["proposed_members"]
        },
        "additional_information": form["additional_information"],
        "ministries": {
            group: {
                key: {
                    action: value
                    for action, value in entry.items()
                    if action != "current"
                }
                for key, entry in form["ministries"][group].items()
                if group == "proposed_members"
                or not next(
                    member["request"]
                    for member in form["members"]
                    if member["id"] == key
                )
            }
            for group in ("members", "proposed_members")
        }
        if form["ministries"] is not None
        else {},
        "testing_acknowledged": form["testing"],
        **(
            {"financial": form["financial"]["answers"]} if form.get("financial") else {}
        ),
    }


def test_census_submit_recipient_change_requires_review(response_service):
    """The displayed recipient is pinned even without financial share wording."""
    harness = response_service
    form = load_form(harness)
    store = harness.service.store
    version = store.active()
    assert (
        form["parish_name"]
        == version.document()["sections"]["parish"][0]["values"]["name"]
    )
    changed = change_configuration(
        store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": version.document()["sections"]["parish"][0]["id"],
                "values": {"name": "Renamed Sample Parish"},
            }
        ],
    )
    assert changed.state == "applied"
    with web_login():
        response = post(
            harness.client,
            "/family/submit",
            {
                "baseline": form["baseline"],
                "answers": answers_for(form),
            },
        )
        assert response.status_code == 409, response.content
        fresh = response.json()["form"]
        assert fresh["parish_name"] == "Renamed Sample Parish"
        assert not Submission.objects.exists()
        result = post(
            harness.client,
            "/family/submit",
            {
                "baseline": fresh["baseline"],
                "answers": answers_for(fresh),
            },
        )
        assert result.status_code == 200, result.content


def test_disabled_additional_field_does_not_replay_prior_text(response_service):
    """A real configuration change suppresses hidden text and permits Submit."""
    harness = response_service
    form = load_form(harness)
    answers = answers_for(form)
    answers["additional_information"] = "Previously requested note"
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert result.status_code == 200, result.content
    store = harness.service.store
    changed = change_configuration(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(harness.campaign.pk),
                "values": {"additional_information": False},
            }
        ],
    )
    assert changed.state == "applied"
    harness.client, response = login(harness.code)
    assert response.status_code == 302
    form = load_form(harness)
    assert form["additional_enabled"] is False
    assert form["additional_information"] == ""
    assert "Previously requested note" not in str(form)
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers_for(form)},
    )
    assert result.status_code == 200, result.content
    assert (
        Submission.objects.order_by("-family_version")
        .first()
        .answers["additional_information"]
        == ""
    )


def test_shell_and_testing_ack_reveal_no_household_data(response_service):
    harness = response_service
    response = harness.client.get("/family/")
    assert response.status_code == 200
    assert b"Testing mode" in response.content
    assert b"valid@example.org" not in response.content
    assert not FamilyFormBaseline.objects.exists()
    response = post(harness.client, "/family/form", {"testing_acknowledged": False})
    assert response.status_code == 409
    assert response.json() == {"error": "testing_acknowledgment"}
    assert not FamilyFormBaseline.objects.exists()
    form = load_form(harness)
    assert form["last_submitted_at"] is None
    assert answers_for(form)["members"]["3"]["email"] == "valid@example.org"
    assert not Submission.objects.exists()


@pytest.mark.parametrize("path", ["/family/form", "/family/submit"])
@pytest.mark.parametrize(
    "loss", ["before", "ended", "eligibility", "rehearsal", "unconfigured"]
)
def test_each_private_form_boundary_rechecks_current_admission(
    response_service, settings, path, loss
):
    """A previously issued baseline cannot authorize data after current access loss."""
    from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal

    harness = response_service
    form = load_form(harness)
    instant = harness.campaign.active_configuration.starts_at
    if loss == "before":
        instant -= timedelta(seconds=1)
    elif loss == "ended":
        instant = harness.campaign.active_configuration.ends_at
    elif loss == "eligibility":
        corpus = response_source()
        corpus.members[3]["memberStatus"] = "Inactive"
        snapshot, claim = prepare(corpus)
        promote(snapshot, claim, harness.campaign, harness.rings)
    elif loss == "rehearsal":
        invalidate_rehearsal(campaign_id=harness.campaign.pk, admit=lambda *args: True)
    else:
        settings.STEWARDSHIP_AUTH_RUNTIME = replace(
            settings.STEWARDSHIP_AUTH_RUNTIME, setup_complete=lambda: False
        )
    body = (
        {"testing_acknowledged": True}
        if path == "/family/form"
        else {"baseline": form["baseline"], "answers": answers_for(form)}
    )
    with campaign_clock(instant):
        result = post(harness.client, path, body)
    assert result.status_code == (503 if loss == "unconfigured" else 403), (
        result.content
    )
    assert b"valid@example.org" not in result.content
    assert not Submission.objects.exists()


@pytest.mark.parametrize("boundary", ["before", "ended"])
def test_public_dates_explain_availability_without_showing_login_or_household(
    response_service, boundary
):
    """Civil parish dates do not become the previous day in a western browser zone."""
    campaign = response_service.campaign.active_configuration
    instant = (
        campaign.starts_at - timedelta(seconds=1)
        if boundary == "before"
        else campaign.ends_at
    )
    with campaign_clock(instant):
        response = response_service.client.get("/")
    assert response.status_code == 200
    assert b"family-code" not in response.content
    assert b"valid@example.org" not in response.content
    assert (b"starts on" if boundary == "before" else b"has ended") in response.content


def test_minimal_family_acceptance_from_source_to_no_change_change_and_revisit(
    live_response_service,
):
    """Compose the real source, login, submission and overlay for DOM-05."""
    harness = live_response_service
    first = load_form(harness)
    assert (
        post(
            harness.client,
            "/family/submit",
            {"baseline": first["baseline"], "answers": answers_for(first)},
        ).status_code
        == 200
    )
    assert not ProposedChange.objects.exists()
    harness.client, _ = login(harness.code)
    second = load_form(harness)
    answers = answers_for(second)
    answers["members"]["3"]["first_name"] = "Updated Family name"
    assert (
        post(
            harness.client,
            "/family/submit",
            {"baseline": second["baseline"], "answers": answers},
        ).status_code
        == 200
    )
    assert harness.client.get("/family/").status_code == 302
    harness.client, _ = login(harness.code)
    third = load_form(harness)
    assert answers_for(third)["members"]["3"]["first_name"] == "Updated Family name"
    assert next(
        field
        for field in third["members"][0]["fields"]
        if field["name"] == "first_name"
    )["changed"]
    assert Submission.objects.count() == 2 and ProposedChange.objects.count() == 1
    original, updated = Submission.objects.order_by("family_version")
    assert original.answers["members"]["3"]["first_name"] == "Member"
    updated.family.refresh_from_db()
    assert updated.family.first_live_submission_id == original.pk
    assert updated.family.effective_submission_id == updated.pk


@pytest.mark.parametrize("restricted", [False, True])
def test_live_http_no_change_submit_logs_out_and_revisit_shows_status(
    live_response_service, restricted
):
    harness = live_response_service
    with web_login() if restricted else nullcontext():
        form = load_form(harness)
        response = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers_for(form)},
        )
        assert response.status_code == 200, response.content
        assert response.json() == {"accepted": True}
        assert harness.client.get("/family/").status_code == 302
        assert not ProposedChange.objects.exists()
        harness.client, response = login(harness.code, harness.client)
        assert response.status_code == 302
        revisited = load_form(harness)
        assert (
            revisited["last_submitted_at"]
            == Submission.objects.get().submitted_at.isoformat()
        )
        assert answers_for(revisited) == answers_for(form)


def test_csrf_and_intermediate_answer_injection_cannot_persist_drafts(response_service):
    harness = response_service
    assert (
        harness.client.post(
            "/family/form",
            {"testing_acknowledged": True},
            content_type="application/json",
        ).status_code
        == 403
    )
    result = post(
        harness.client,
        "/family/form",
        {"testing_acknowledged": True, "answers": "private draft"},
    )
    assert result.status_code == 422 and b"private draft" not in result.content
    assert not FamilyFormBaseline.objects.exists()
    assert not Submission.objects.exists()


def test_invalid_submit_keeps_session_and_returns_static_errors(response_service):
    harness = response_service
    form = load_form(harness)
    answers = answers_for(form)
    answers["members"]["3"]["email"] = "private-invalid-email"
    response = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert response.status_code == 422
    assert "members.3.email" in response.json()["fields"]
    assert b"private-invalid-email" not in response.content
    assert not Submission.objects.exists()
    assert harness.client.get("/family/").status_code == 200


def test_source_change_refresh_requires_new_definitive_http_submit(response_service):
    harness = response_service
    form = load_form(harness)
    answers = answers_for(form)
    answers["members"]["3"]["first_name"] = "Unsaved tab edit"
    data = response_source()
    data.members[3]["firstName"] = "Fresh source"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    response = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "review_required"
    refreshed = response.json()["form"]
    assert refreshed["baseline"] != form["baseline"]
    assert answers_for(refreshed)["members"]["3"]["first_name"] == "Fresh source"
    assert b"Unsaved tab edit" not in response.content
    assert not Submission.objects.exists()
    response = post(
        harness.client,
        "/family/submit",
        {"baseline": refreshed["baseline"], "answers": answers},
    )
    assert response.status_code == 200
    assert (
        Submission.objects.get().answers["members"]["3"]["first_name"]
        == "Unsaved tab edit"
    )


def test_revisit_never_serializes_hidden_conflicting_source_value(
    live_response_service,
):
    harness = live_response_service
    form = load_form(harness)
    answers = answers_for(form)
    answers["members"]["3"]["first_name"] = "Family chosen name"
    assert (
        post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers},
        ).status_code
        == 200
    )
    data = response_source()
    data.members[3]["firstName"] = "Hidden competing value"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    harness.client, _ = login(harness.code)
    response = post(harness.client, "/family/form", {"testing_acknowledged": False})
    assert response.status_code == 200
    assert b"Hidden competing value" not in response.content
    field = next(
        field
        for field in response.json()["form"]["members"][0]["fields"]
        if field["name"] == "first_name"
    )
    assert field["value"] == "Family chosen name" and field["conflict"]
    assert set(field) == {
        "name",
        "label",
        "required",
        "max_length",
        "kind",
        "choices",
        "value",
        "available",
        "changed",
        "conflict",
    }


def test_multiple_source_addresses_submit_unchanged_and_revisit(live_response_service):
    """R1-01/02: source and saved comma-joined email prefill remain usable."""
    harness = live_response_service
    data = response_source()
    data.members[3]["emailAddress"] = "second@example.org; first@example.org"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    form = load_form(harness)
    answers = answers_for(form)
    assert answers["members"]["3"]["email"] == "first@example.org, second@example.org"
    response = post(
        harness.client,
        "/family/submit",
        {
            "baseline": form["baseline"],
            "answers": answers,
        },
    )
    assert response.status_code == 200
    assert not ProposedChange.objects.exists()
    harness.client, _ = login(harness.code)
    assert answers_for(load_form(harness)) == answers
