"""New Family/Member answers end to end: limitations, retired talents, guards.

The Family form no longer offers talents (#970). Each new entry still
carries a ``talents`` map, which the SQL guard requires to be empty since
migration 0056.
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


def test_the_retired_talent_functions_are_gone():
    """Migration 0056 dropped the talent defaults and the talent report."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regprocedure('stewardship_talent_defaults_v1()'),"
            "to_regprocedure('stewardship_talent_report_v1(uuid,jsonb)'),"
            "to_regprocedure('stewardship_limitations_report_v1(uuid,jsonb)') IS NULL"
        )
        assert cursor.fetchone() == (None, None, False)


def test_limitations_round_trip_and_no_talents_are_offered(response_service):
    """The form offers no talents (#970); the limitations persist and prefill."""
    harness = response_service
    form = start(harness)
    assert form["cannot_attend"] is False
    # Kept empty for a tab loaded before #970, whose script reads it.
    assert form["service"]["talent_options"] == []
    assert form["service"]["members"]["3"] == {"cannot_serve": False}
    answers = answers_for(form)
    answers["cannot_attend"] = True
    answers["service"] = {
        "members": {"3": {"cannot_serve": False}},
        "proposed_members": {},
    }
    first = respond(harness, form, answers)
    assert first.answers["cannot_attend"] is True
    # Stored with the empty talents map the installed guard requires.
    assert first.answers["service"]["members"]["3"] == {
        "cannot_serve": False,
        "talents": {},
    }
    form = revisit(harness)
    assert form["cannot_attend"] is True
    assert form["service"]["members"]["3"] == {"cannot_serve": False}


def test_cannot_serve_requires_leaving_every_current_ministry(response_service):
    """The lock is enforced on the server, not only by the form."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["service"] = {
        "members": {"3": {"cannot_serve": True}},
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


def test_a_form_opened_before_the_deploy_still_submits(response_service):
    """A tab opened before #970 deployed still submits; its talents are dropped.

    The pre-deploy form offered the configured talents, so a Family's open
    tab may send some. Its baseline holds the same definition digest a new
    one does (the digest still hashes the configured list), so this sends
    talents on a fresh baseline exactly as that tab would. Refusing them
    would lose the whole response mid-campaign; instead the response is
    stored with an empty talents map, the only one the SQL guard accepts.
    A talents value that is not a map was never sent by any form.
    """
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["service"] = {
        "members": {"3": {"cannot_serve": False, "talents": "Organ"}},
        "proposed_members": {},
    }
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert result.status_code == 422
    assert "service.members.3" in result.json()["fields"]
    answers["service"]["members"]["3"]["talents"] = {PAINTER: "", OTHER: "Organ"}
    submission = respond(harness, form, answers)
    assert submission.answers["service"]["members"]["3"] == {
        "cannot_serve": False,
        "talents": {},
    }
    assert revisit(harness)["service"]["members"]["3"] == {"cannot_serve": False}


@pytest.mark.parametrize(
    "path,value",
    [
        # Every talent is refused now, even one the old form offered (#970).
        (("service", "members", "3", "talents"), {PAINTER: ""}),
        (("service", "members", "3", "talents"), {"foreign": ""}),
        (("service", "members", "3", "talents"), []),
        (("service", "members", "3", "talents"), {PAINTER: "text"}),
        (("service", "members", "3", "talents"), {OTHER: ""}),
        (("service", "members", "3", "cannot_serve"), "no"),
        (("service", "members", "3", "extra"), True),
        (("service", "members", "99"), {"cannot_serve": False, "talents": {}}),
        (("service", "members", "3", "cannot_serve"), True),
    ],
)
def test_sql_rejects_forged_talents_and_lock(response_service, path, value):
    """Bypassing Python still cannot store a talent or a false lock."""
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
    """Talents, module off, a non-object section, a proposed join."""
    harness = response_service
    form = start(harness)
    submission = respond(harness, form, answers_for(form))
    answers = json.loads(json.dumps(submission.answers))
    guard(submission, answers)
    # A new response may not carry talents (#970, migration 0056), nor omit
    # the empty map its entries keep.
    stored = json.loads(json.dumps(answers))
    stored["service"]["members"]["3"]["talents"] = {PAINTER: "", OTHER: "Organ"}
    with pytest.raises(IntegrityError, match="no talents"), transaction.atomic():
        guard(submission, stored)
    del stored["service"]["members"]["3"]["talents"]
    with pytest.raises(IntegrityError, match="no talents"), transaction.atomic():
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
