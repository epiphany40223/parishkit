"""New Family/Member answers end to end: talents, limitations and their guards."""

import json
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection

from parishkit.stewardship.responses.service import (
    DEFAULT_TALENTS,
    default_talent_options,
)

from .campaign_builders import change as change_configuration
from .test_ministry_responses_postgresql import (
    respond,
    revisit,
    start,
)
from .test_response_http_postgresql import answers_for, post

pytestmark = pytest.mark.django_db(transaction=True)

PAINTER, OTHER = DEFAULT_TALENTS[0].id, DEFAULT_TALENTS[-1].id


def test_sql_default_talents_match_the_application_defaults():
    """Configurations without a list resolve to the same options in both places."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_talent_defaults_v1()::text")
        assert json.loads(cursor.fetchone()[0]) == default_talent_options()


def test_talents_and_limitations_round_trip_through_a_real_submission(
    response_service,
):
    """The form offers talents, the answers persist, and a revisit prefills them."""
    harness = response_service
    form = start(harness)
    assert form["cannot_attend"] is False
    assert [row["label"] for row in form["service"]["talent_options"]][0] == "Painter"
    assert form["service"]["members"]["3"] == {"cannot_serve": False, "talents": {}}
    answers = answers_for(form)
    answers["cannot_attend"] = True
    answers["service"] = {
        "members": {
            "3": {"cannot_serve": False, "talents": {PAINTER: "", OTHER: " Organ "}}
        },
        "proposed_members": {},
    }
    first = respond(harness, form, answers)
    assert first.answers["cannot_attend"] is True
    assert first.answers["service"]["members"]["3"] == {
        "cannot_serve": False,
        "talents": {PAINTER: "", OTHER: "Organ"},
    }
    form = revisit(harness)
    assert form["cannot_attend"] is True
    assert form["service"]["members"]["3"]["talents"] == {PAINTER: "", OTHER: "Organ"}


def test_cannot_serve_requires_leaving_every_current_ministry(response_service):
    """The lock is enforced on the server, not only by the form."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["service"] = {
        "members": {"3": {"cannot_serve": True, "talents": {}}},
        "proposed_members": {},
    }
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert result.status_code == 422
    assert "service.members.3" in result.json()["fields"]
    answers["ministries"]["members"]["3"] = {"join": [], "leave": [4]}
    submission = respond(harness, form, answers)
    assert submission.answers["service"]["members"]["3"]["cannot_serve"] is True


@pytest.mark.parametrize(
    "path,value",
    [
        (("service", "members", "3", "talents"), {"foreign": ""}),
        (("service", "members", "3", "talents"), {PAINTER: "text"}),
        (("service", "members", "3", "talents"), {OTHER: ""}),
        (("service", "members", "3", "cannot_serve"), "no"),
        (("service", "members", "3", "extra"), True),
        (("service", "members", "99"), {"cannot_serve": False, "talents": {}}),
        (("service", "members", "3", "cannot_serve"), True),
    ],
)
def test_sql_rejects_forged_talents_and_lock(response_service, path, value):
    """Bypassing Python still cannot store an invalid talent or a false lock."""
    harness = response_service
    form = start(harness)
    submission = respond(harness, form, answers_for(form))
    with pytest.raises(IntegrityError), connection.cursor() as cursor:
        answers = json.loads(json.dumps(submission.answers))
        target = answers
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        cursor.execute(
            "SELECT stewardship_service_answers_guard_v1("
            "jsonb_populate_record(s, jsonb_build_object('answers', %s::jsonb)),c) "
            "FROM stewardship_submission s "
            "JOIN stewardship_campaign_configuration c "
            "  ON c.configuration_id=s.configuration_id AND c.record_id=s.campaign_id "
            "WHERE s.id=%s",
            [json.dumps(answers), submission.pk],
        )


def test_edited_talent_list_is_offered_and_enforced(response_service):
    """An Admin-edited list replaces the defaults for new forms."""
    harness = response_service
    start(harness)
    musician = "7f000000-0000-4000-8000-000000000001"
    store = harness.service.store
    result = change_configuration(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(harness.campaign.pk),
                "values": {
                    "talent_options": [
                        {"id": musician, "label": "Musician", "free_text": False}
                    ]
                },
            }
        ],
    )
    assert result.state == "applied"
    form = revisit(harness)
    assert [row["id"] for row in form["service"]["talent_options"]] == [musician]
    answers = answers_for(form)
    answers["service"] = {
        "members": {"3": {"cannot_serve": False, "talents": {PAINTER: ""}}},
        "proposed_members": {},
    }
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert result.status_code == 422
    answers["service"]["members"]["3"]["talents"] = {musician: ""}
    assert respond(harness, form, answers).answers["service"]["members"]["3"][
        "talents"
    ] == {musician: ""}
