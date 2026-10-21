"""pk-local's census publication writes reach the fake ParishSoft only (#1884).

The fake answers the one Member contact PUT, records it as an ordinary
``member`` edit in its configuration file, and serves it back to the
gateway's read and to a refresh. The gateway end-to-end case runs the real
gateway, publication session and helper request check, with only the
helper's process exchange replaced by a call into the fake.
"""

import json
from datetime import date
from unittest.mock import Mock

import pytest

from parishkit import parishsoft_http_worker as helper
from parishkit import parishsoft_transport as transport
from parishkit.parishsoft import DEFAULT_API_BASE_URL
from parishkit.parishsoft_http_worker import LOCAL_SOURCE_BASE_URL
from parishkit.stewardship.local import LOCAL_PARISHSOFT_KEY
from parishkit.stewardship.local.fake_contact import (
    READABLE,
    SMOKE_WRITABLE,
    WRITABLE,
    contact_record,
)
from parishkit.stewardship.local.fake_edits import edit_document
from parishkit.stewardship.local.fake_parishsoft import (
    BASE_PATH,
    FakeConfiguration,
    FakeParishSoft,
    file_recorder,
)
from parishkit.stewardship.responses.capabilities import MEMBER_CAPABILITIES
from parishkit.stewardship.responses.contact_smoke import MARKERS
from parishkit.stewardship.responses.publication import (
    MEMBER_CONTACT_FIELDS,
    encode_member_value,
    payload_digest,
)
from parishkit.stewardship.responses.publication_gateway import (
    ContactGatewayError,
    ParishSoftContactGateway,
)

KEY = {"x-api-key": LOCAL_PARISHSOFT_KEY}
DOCUMENT = {"seed": 1, "families": 40, "anchor_date": "2026-09-16", "release_at": None}


@pytest.fixture
def stored(tmp_path):
    """A fake started from a configuration file it records its writes in."""
    path = tmp_path / "fake-parishsoft.json"
    path.write_text(json.dumps(DOCUMENT))
    fake = FakeParishSoft(FakeConfiguration.read(path), record=file_recorder(path))
    return fake, path


def member_id(fake):
    """A Member the fake serves (never the held-back late-added Family's)."""
    late = fake.parish.late_family_id
    return next(m["memberDUID"] for m in fake.parish.members if m["familyDUID"] != late)


def put(fake, identifier, body):
    """One contact PUT through the fake's request handling."""
    return fake.handle("PUT", f"{BASE_PATH}members/{identifier}/contact", body, KEY)


def member(fake, identifier):
    """The Member object the fake now serves."""
    status, body = fake.handle("GET", f"{BASE_PATH}members/{identifier}", None, KEY)
    assert status == 200
    return json.loads(body)


def test_the_writable_fields_are_the_publishers_api_fields():
    """The fake takes exactly the Member fields census publication may write,
    and reads back only PUT schema names."""
    api = {
        capability.provider_fields[0]
        for capability in MEMBER_CAPABILITIES.values()
        if capability.provider_fields
    }
    assert set(WRITABLE) == api
    assert set(READABLE) <= set(MEMBER_CONTACT_FIELDS)
    assert set(MEMBER_CONTACT_FIELDS) - set(READABLE) == {"fax", "pager"}
    # Beyond those, the PUT takes only the smoke check's marker fields.
    assert set(SMOKE_WRITABLE) == set(MARKERS) == {"fax", "pager"}


def test_a_put_is_a_partial_update_recorded_in_the_file(stored):
    """Written fields change, others (and nulls) are left, and the edit is in
    the file, so a restarted fake serves it as well."""
    fake, path = stored
    identifier = member_id(fake)
    before = member(fake, identifier)
    body = {
        "nickName": "Sunny",
        "cellPhone": "(513) 555-0100",
        "dateOfBirth": "1980-02-03",
        "emailAddress": "a@example.test; b@example.test",
        "lastName": None,
    }
    assert put(fake, identifier, body) == (200, b"")
    after = member(fake, identifier)
    assert after["nickName"] == "Sunny"
    assert after["mobilePhone"] == "(513) 555-0100"
    assert after["birthdate"] == "1980-02-03T00:00:00"
    assert after["lastName"] == before["lastName"]
    assert after["firstName"] == before["firstName"]
    document = json.loads(path.read_text())
    assert {k: v for k, v in document.items() if k != "edits"} == DOCUMENT
    assert document["edits"] == [
        {
            "kind": "member",
            "id": identifier,
            "set": {
                "nickName": "Sunny",
                "mobilePhone": "(513) 555-0100",
                "birthdate": "1980-02-03T00:00:00",
                "emailAddress": "a@example.test; b@example.test",
            },
        }
    ]
    # The contact list a refresh reads is re-derived from the edited Member.
    _, raw = fake.handle(
        "POST",
        f"{BASE_PATH}members/contact/list",
        {"organizationIDs": [fake.organization_id], "limit": 500, "offset": 0},
        KEY,
    )
    contact = next(r for r in json.loads(raw) if r["memberDUID"] == identifier)
    assert contact["cellPhone"] == "(513) 555-0100"
    restarted = FakeParishSoft(FakeConfiguration.read(path))
    assert member(restarted, identifier) == after
    # The operator's undo drops it like any other edit.
    assert "edits" not in edit_document(["undo"], document)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"lastName": None},
        {"suffix": "Jr."},
        {"pager": ""},
        {"gender": "F"},
        {"nickName": ""},
        {"dateOfBirth": "19800203"},
        {"dateOfBirth": 1980},
        {"nickName": "x" * 201},
        ["nickName"],
    ],
)
def test_a_refused_put_changes_nothing(stored, body):
    """A body the fake cannot take is a 400, with no change served or stored."""
    fake, path = stored
    identifier = member_id(fake)
    before = member(fake, identifier)
    assert put(fake, identifier, body) == (400, b"")
    assert member(fake, identifier) == before
    assert json.loads(path.read_text()) == DOCUMENT


def test_unknown_members_unstored_writes_and_the_bound(stored, monkeypatch, caplog):
    """404 for a Member the fake does not serve; 400 without a recorder; 503
    when the recorder fails or at the edits bound, with nothing changed, and
    the bound logs how to free edits."""
    fake, path = stored
    identifier = member_id(fake)
    assert put(fake, 999999, {"nickName": "Sunny"}) == (404, b"")
    late = next(
        m["memberDUID"]
        for m in fake.parish.members
        if m["familyDUID"] == fake.parish.late_family_id
    )
    assert put(fake, late, {"nickName": "Sunny"}) == (404, b"")
    unrecorded = FakeParishSoft(FakeConfiguration.read(path))
    assert put(unrecorded, identifier, {"nickName": "Sunny"}) == (400, b"")
    before = member(fake, identifier)
    fake.record = Mock(side_effect=OSError("disk full"))
    assert put(fake, identifier, {"nickName": "Sunny"}) == (503, b"")
    assert member(fake, identifier) == before
    assert fake.configuration.edits == ()
    from parishkit.stewardship.local import fake_parishsoft

    monkeypatch.setattr(fake_parishsoft, "MAXIMUM_EDITS", 0)
    fake.record = Mock()
    assert put(fake, identifier, {"nickName": "Sunny"}) == (503, b"")
    fake.record.assert_not_called()
    assert "'fake-parishsoft undo' or 'fake-parishsoft clear'" in caplog.text


def test_only_put_reaches_the_contact_route(stored):
    """Other methods on the write path are 405; the key is still required."""
    fake, _ = stored
    identifier = member_id(fake)
    path = f"{BASE_PATH}members/{identifier}/contact"
    assert fake.handle("GET", path, None, KEY) == (405, b"")
    assert fake.handle("POST", path, {}, KEY) == (405, b"")
    assert fake.handle("PUT", path, {"nickName": "S"}, {}) == (401, b"")


def test_contact_record_maps_the_member_object():
    """Dates become civil dates; missing fields stay missing; bad input refused."""
    record = contact_record(
        {"firstName": "Pat", "birthdate": "1980-02-03T00:00:00", "mobilePhone": None}
    )
    expected = {"firstName": "Pat", "dateOfBirth": "1980-02-03", "cellPhone": None}
    assert record == expected
    for value in ([], {"birthdate": 5}):
        with pytest.raises(ValueError):
            contact_record(value)


def fake_exchange(fake, monkeypatch):
    """Route the session's helper exchange into ``fake``, after the helper's
    own request check, so nothing leaves the process."""

    def exchange(payload, *, seconds, check, helper, on_timeout=None):
        frame = json.loads(payload)
        helper_frame = frame | {"api_key": LOCAL_PARISHSOFT_KEY}
        transport.validate_request(helper_frame, contact_writes=helper.contact_writes)
        path = frame["url"][len(LOCAL_SOURCE_BASE_URL) :]
        body = frame["parameters"] if frame["method"] != "GET" else None
        status, content = fake.handle(
            frame["method"], "/api/v2" + path, body, {"x-api-key": helper.key}
        )
        return b"%d\n" % status + content

    monkeypatch.setattr(transport, "_exchange", exchange)


def test_the_gateway_reads_and_writes_the_fake_end_to_end(stored, monkeypatch):
    """LOCAL's gateway reads the fake's record and its PUT lands there, so a
    read after the write is the record read with the writes applied (the
    publish worker's read-back check)."""
    fake, path = stored
    fake_exchange(fake, monkeypatch)
    identifier = member_id(fake)
    gateway = ParishSoftContactGateway.open_operator(
        Mock(api_key=LOCAL_PARISHSOFT_KEY),
        profile="local",
        base_url=LOCAL_SOURCE_BASE_URL,
        on_timeout=Mock(),
    )
    before = gateway.read_member_contact(identifier, timeout=30)
    assert set(before) == set(READABLE)
    writes = {"nickName": "Sunny", "workPhone": "(513) 555-0199"}
    gateway.write_member_contact(identifier, writes, timeout=30)
    assert gateway.read_member_contact(identifier, timeout=30) == before | writes
    assert len(json.loads(path.read_text())["edits"]) == 1
    assert gateway.read_member_contact(999999, timeout=30) is None
    with pytest.raises(ContactGatewayError) as error:
        gateway.write_member_contact(identifier, {"gender": "F"}, timeout=30)
    assert error.value.kind == "write_rejected"


def test_the_contact_smoke_round_trip_runs_against_the_fake(tmp_path, monkeypatch):
    """pk-local's Member contact smoke check (#1399) runs Production's probe
    and round trip through the real gateway, session and helper check
    against the fake: once an operator edit gives the Member a pager, the
    marker is written, read back and restored, and both writes are recorded
    as ordinary edits. Without a pager the round trip writes nothing."""
    from parishkit.stewardship.responses import contact_smoke

    path = tmp_path / "fake-parishsoft.json"
    path.write_text(json.dumps(DOCUMENT))
    identifier = member_id(FakeParishSoft(FakeConfiguration.read(path)))

    def round_trip():
        """One smoke run with ``--send`` against a fake started from ``path``."""
        fake = FakeParishSoft(FakeConfiguration.read(path), record=file_recorder(path))
        fake_exchange(fake, monkeypatch)
        gateway = ParishSoftContactGateway.open_operator(
            Mock(api_key=LOCAL_PARISHSOFT_KEY),
            profile="local",
            base_url=LOCAL_SOURCE_BASE_URL,
            on_timeout=Mock(),
        )
        try:
            return fake, contact_smoke.run(gateway, identifier, send=True)
        finally:
            gateway.close()

    _, unset = round_trip()
    assert unset["write"] == "refused_marker_field_unread"
    assert "edits" not in json.loads(path.read_text())
    words = ["member", str(identifier), "pager=555-010-0100"]
    path.write_text(json.dumps(edit_document(words, json.loads(path.read_text()))))
    fake, result = round_trip()
    assert result["write"] == "accepted" and result["marker_read_back"] is True
    assert result["also_changed"] == [] and result["restored"] is True
    assert result["confirmed"] is True
    edits = json.loads(path.read_text())["edits"]
    assert [edit["set"] for edit in edits[1:]] == [
        {"pager": contact_smoke.MARKERS["pager"]},
        {"pager": "555-010-0100"},
    ]
    assert member(fake, identifier)["pager"] == "555-010-0100"


def test_the_publishers_encoded_values_read_back_as_planned(stored):
    """What ``encode_member_value`` sends for a date, an email list and a
    phone is what the read returns after the PUT, so the read-back digest
    equals the planned one (read with the writes applied) and the publish
    worker confirms the write."""
    fake, _ = stored
    identifier = member_id(fake)
    before = contact_record(member(fake, identifier))
    writes = {
        "dateOfBirth": encode_member_value("birth_date", date(1980, 2, 3)),
        "emailAddress": encode_member_value("email", "a@example.test, b@example.test"),
        "cellPhone": encode_member_value(
            "mobile_phone", {"display": "(513) 555-0100", "e164": "+15135550100"}
        ),
    }
    assert writes == {
        "dateOfBirth": "1980-02-03",
        "emailAddress": "a@example.test; b@example.test",
        "cellPhone": "(513) 555-0100",
    }
    assert put(fake, identifier, writes) == (200, b"")
    after = contact_record(member(fake, identifier))
    assert payload_digest(after) == payload_digest({**before, **writes})


def test_the_real_apis_read_stays_unconfirmed(monkeypatch):
    """Production's gateway never reaches a read, so never its PUT."""
    reached = Mock(side_effect=AssertionError("no request may be made"))
    monkeypatch.setattr(transport, "_exchange", reached)
    gateway = ParishSoftContactGateway.open_operator(
        Mock(api_key="SYNTHETIC-PRIVATE-KEY"),
        profile="production",
        base_url=DEFAULT_API_BASE_URL,
        on_timeout=Mock(),
    )
    with pytest.raises(ContactGatewayError) as error:
        gateway.read_member_contact(5, timeout=30)
    assert error.value.kind == "read_unconfirmed"
    assert helper.contact_put_admitted("production", DEFAULT_API_BASE_URL)
