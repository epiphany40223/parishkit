"""The pre-launch scan finds every Member value that would refuse a Family form."""

import json
from copy import deepcopy

import pytest

from parishkit.stewardship import source_form_check
from parishkit.stewardship.cli import main
from parishkit.stewardship.responses.inputs import (
    MemberSourceUnavailable,
    census_inputs,
)

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
    assert findings(inputs) == [raised.value.context]


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
        {"family_duid": 1, "member_duid": member, "field": field}
        for member in (10, 12)
        for field in ("birth_date", "first_name")
    ]
    assert SENTINEL not in json.dumps(result)


def test_without_census_only_the_names_are_read(inputs):  # noqa: F811
    inputs["members"][0].update(birthdate="not-a-date")
    assert findings(inputs, census=False) == []
    inputs["members"][0].update(lastName="x" * 101)
    assert findings(inputs, census=False) == [
        {"family_duid": 1, "member_duid": 10, "field": "last_name"}
    ]


def test_unusable_member_record_is_reported_not_skipped(inputs):  # noqa: F811
    inputs["members"][0]["memberDUID"] = SENTINEL
    assert findings(inputs) == [
        {"family_duid": 1, "member_duid": None, "field": "member_record"}
    ]


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
