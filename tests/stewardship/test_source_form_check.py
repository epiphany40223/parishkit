"""The pre-launch scan finds every Member value that would refuse a Family form."""

import json
from copy import deepcopy

import pytest

from parishkit.stewardship import source_form_check
from parishkit.stewardship.cli import main
from parishkit.stewardship.responses.inputs import (
    MEMBER_FIELDS,
    FormInputsUnavailable,
    MemberSourceUnavailable,
    census_inputs,
    member_source_fields,
)
from parishkit.stewardship.responses.member_requests import REQUEST_FIELDS

from .test_response_inputs import inputs  # noqa: F401 - shared fixture

SENTINEL = "private-source-value"


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch):
    """Console tests must not install the process-wide stderr log handler."""
    monkeypatch.setattr(source_form_check, "configure_logging", lambda: None)


def findings(inputs, *, census=True):  # noqa: F811 - the fixture's value
    """Scan the fixture Family exactly as the command scans a campaign."""
    return source_form_check.member_source_findings(
        [inputs["family"]["familyDUID"]],
        inputs["members"],
        inputs["contacts"],
        census=census,
    )


def test_clean_family_has_no_findings_and_its_form_loads(inputs):  # noqa: F811
    assert findings(inputs) == []
    census_inputs(**inputs)


@pytest.mark.parametrize(
    "change,field",
    [
        (lambda i: i["members"][0].update(firstName="x" * 101), "first_name"),
        (lambda i: i["members"][0].update(lastName="Fam\tily"), "last_name"),
        (lambda i: i["members"][0].update(birthdate="2025-02-29"), "birth_date"),
        (
            lambda i: i["contacts"]["10"]["emails"][0].update(
                value="x" * 250 + "@example.org"
            ),
            "email",
        ),
        (lambda i: i["contacts"]["10"]["phones"].update(home="x" * 101), "home_phone"),
    ],
)
def test_each_refused_value_matches_what_the_form_raises(
    inputs,  # noqa: F811
    change,
    field,
):
    """The scan reports the same identifiers the form's refusal would carry."""
    change(inputs)
    with pytest.raises(MemberSourceUnavailable) as raised:
        census_inputs(**inputs)
    assert raised.value.context == {"family_duid": 1, "member_duid": 10, "field": field}
    assert findings(inputs) == [raised.value.context | {"kind": "value"}]


def test_every_bad_value_is_listed_without_any_value(inputs):  # noqa: F811
    """Unlike the form, the scan does not stop at the first refused field."""
    inputs["members"][0].update(firstName=SENTINEL + "\nname", birthdate=SENTINEL)
    second = deepcopy(inputs["members"][0]) | {"memberDUID": 12}
    inactive = deepcopy(second) | {"memberDUID": 13, "active": False}
    deceased = deepcopy(second) | {"memberDUID": 14, "deceased": True}
    other_family = deepcopy(second) | {"memberDUID": 15, "family_key": "2"}
    inputs["members"] += [second, inactive, deceased, other_family]
    result = findings(inputs)
    assert result == [
        {"family_duid": 1, "member_duid": member, "field": field, "kind": "value"}
        for member in (10, 12)
        for field in ("birth_date", "first_name")
    ]
    assert SENTINEL not in json.dumps(result)


def test_without_census_only_the_names_are_read(inputs):  # noqa: F811
    inputs["members"][0].update(birthdate="not-a-date")
    assert findings(inputs, census=False) == []
    inputs["members"][0].update(lastName="x" * 101)
    assert findings(inputs, census=False) == [
        {"family_duid": 1, "member_duid": 10, "field": "last_name", "kind": "value"}
    ]


@pytest.mark.parametrize(
    "change,member",
    [
        (lambda i: i["members"][0].update(memberDUID=SENTINEL), None),
        (lambda i: i["members"][0].update(active=SENTINEL), 10),
        (lambda i: i["members"][1].update(memberDUID=10), 10),
    ],
)
def test_unusable_member_record_is_reported_not_skipped(
    inputs,  # noqa: F811
    change,
    member,
):
    """A record the form refuses outright is a finding, and the scan goes on."""
    change(inputs)
    with pytest.raises(FormInputsUnavailable):
        census_inputs(**inputs)
    result = findings(inputs)
    assert {
        "family_duid": 1,
        "member_duid": member,
        "field": "member",
        "kind": "record",
    } in result
    assert SENTINEL not in json.dumps(result)


@pytest.mark.parametrize(
    "change",
    [
        lambda contact: contact.update(owner_key="99"),
        lambda contact: contact.pop("emails"),
        lambda contact: contact.update(phones=SENTINEL),
    ],
)
def test_malformed_contact_is_a_finding_not_an_aborted_scan(
    inputs,  # noqa: F811
    change,
):
    """A contact the form cannot read is reported per field; others still scan."""
    change(inputs["contacts"]["10"])
    inputs["members"].append(
        deepcopy(inputs["members"][0]) | {"memberDUID": 12, "lastName": "x" * 101}
    )
    result = findings(inputs)
    assert {
        "family_duid": 1,
        "member_duid": 12,
        "field": "last_name",
        "kind": "value",
    } in result
    broken = [item for item in result if item["member_duid"] == 10]
    assert broken and all(item["kind"] == "record" for item in broken)
    assert SENTINEL not in json.dumps(result)


def test_scan_reads_the_fields_census_inputs_reads():
    """Both use one helper; pin its two shapes so a change is deliberate."""
    census = [field.name for field, _ in member_source_fields(True)]
    assert census == [field.name for field in (*MEMBER_FIELDS, *REQUEST_FIELDS)]
    assert all(contact for _, contact in member_source_fields(True))
    assert member_source_fields(False) == tuple(
        (field, False)
        for field in MEMBER_FIELDS
        if field.name in {"first_name", "last_name"}
    )


def test_cli_prints_the_document_and_exit_code_follows_findings(monkeypatch, capsys):
    document = {"result": "clean", "findings": []}
    monkeypatch.setattr(source_form_check, "load_deployment", lambda path: object())
    monkeypatch.setattr(
        source_form_check, "source_form_check_command", lambda config: document
    )
    assert main(["source-form-check", "--config", "private-input.yaml"]) == 0
    assert json.loads(capsys.readouterr().out) == document
    document["result"] = "findings"
    assert main(["source-form-check", "--config", "private-input.yaml"]) == 1


@pytest.mark.parametrize(
    "error,line",
    [
        (None, "source form check refused"),
        (source_form_check.ScanRefused(SENTINEL), "source form check refused"),
        (ValueError(SENTINEL), "unexpected error"),
    ],
)
def test_cli_failures_print_one_fixed_line(monkeypatch, capsys, error, line):
    """Missing configuration, refusals and faults never echo private detail."""
    monkeypatch.setattr(source_form_check, "load_deployment", lambda path: object())
    monkeypatch.setattr(source_form_check, "emit_failure", lambda err, *, event: None)

    def fail(config):
        raise error

    monkeypatch.setattr(source_form_check, "source_form_check_command", fail)
    argv = ["source-form-check"] + ([] if error is None else ["--config", "c"])
    assert main(argv) == 2
    output = capsys.readouterr()
    assert output.out == "" and line in output.err
    assert output.err.count("ERROR:") == 1 and SENTINEL not in output.err
