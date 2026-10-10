"""New Family/Member answers end to end: limitations, retired talents, guards.

The Family form no longer offers talents (#970), but the installed SQL guard
still checks each stored ``talents`` map, so its cases stay.
"""

import json
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.responses.service import DEFAULT_TALENTS

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
    """The guard's defaults still match the stored-data compatibility list."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_talent_defaults_v1()::text")
        assert json.loads(cursor.fetchone()[0]) == [
            {"id": option.id, "label": option.label, "free_text": option.free_text}
            for option in DEFAULT_TALENTS
        ]


def test_limitations_round_trip_and_no_talents_are_offered(response_service):
    """The form offers no talents (#970); the limitations persist and prefill."""
    harness = response_service
    form = start(harness)
    assert form["cannot_attend"] is False
    assert form["service"]["talent_options"] == []
    assert form["service"]["members"]["3"] == {"cannot_serve": False, "talents": {}}
    answers = answers_for(form)
    answers["cannot_attend"] = True
    answers["service"] = {
        "members": {"3": {"cannot_serve": False, "talents": {PAINTER: ""}}},
        "proposed_members": {},
    }
    # A talent from a form opened before talents were retired is refused.
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert result.status_code == 422
    assert "service.members.3.talents" in result.json()["fields"]
    answers["service"]["members"]["3"]["talents"] = {}
    first = respond(harness, form, answers)
    assert first.answers["cannot_attend"] is True
    assert first.answers["service"]["members"]["3"] == {
        "cannot_serve": False,
        "talents": {},
    }
    form = revisit(harness)
    assert form["cannot_attend"] is True
    assert form["service"]["members"]["3"]["talents"] == {}


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


def test_an_edited_talent_list_is_not_offered(response_service):
    """A campaign's stored talent list offers nothing now (#970)."""
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
    assert form["service"]["talent_options"] == []
    respond(harness, form, answers_for(form))


def guard(submission, answers, *, modules=None):
    """Run the SQL service guard on a copy of a stored response."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_service_answers_guard_v1("
            "jsonb_populate_record(s, jsonb_build_object('answers', %s::jsonb)),"
            "CASE WHEN %s::jsonb IS NULL THEN c ELSE jsonb_populate_record(c,"
            " jsonb_build_object('values', c.values || jsonb_build_object("
            "'modules', %s::jsonb))) END) "
            "FROM stewardship_submission s "
            "JOIN stewardship_campaign_configuration c "
            "  ON c.configuration_id=s.configuration_id AND c.record_id=s.campaign_id "
            "WHERE s.id=%s",
            [
                json.dumps(answers),
                # SQL NULL (not JSON null) keeps the campaign's own modules.
                None if modules is None else json.dumps(modules),
                None if modules is None else json.dumps(modules),
                submission.pk,
            ],
        )


def test_sql_guard_edge_cases(response_service):
    """Stored talents, module off, a non-object section, a proposed join."""
    harness = response_service
    form = start(harness)
    submission = respond(harness, form, answers_for(form))
    answers = json.loads(json.dumps(submission.answers))
    guard(submission, answers)
    # An older response's stored talents still pass the installed guard (#970).
    stored = json.loads(json.dumps(answers))
    stored["service"]["members"]["3"]["talents"] = {PAINTER: "", OTHER: "Organ"}
    with transaction.atomic():
        guard(submission, stored)
    # Without the Ministry module only an empty section is allowed.
    with transaction.atomic():
        guard(submission, answers | {"service": {}}, modules=["census"])
    with pytest.raises(IntegrityError, match="Disabled"), transaction.atomic():
        guard(submission, answers, modules=["census"])
    for bad in ([], "x", None, {"members": {}}):
        with pytest.raises(IntegrityError), transaction.atomic():
            guard(submission, answers | {"service": bad})
    # A proposed Member who cannot participate may not join anything.
    local = "00000000-0000-4000-8000-00000000abcd"
    forged = json.loads(json.dumps(answers))
    forged["ministries"]["proposed_members"][local] = {"join": [9]}
    forged["service"]["proposed_members"][local] = {
        "cannot_serve": True,
        "talents": {},
    }
    with pytest.raises(IntegrityError, match="stop every"), transaction.atomic():
        guard(submission, forged)
    forged["ministries"]["proposed_members"][local] = {"join": []}
    with transaction.atomic():
        guard(submission, forged)
