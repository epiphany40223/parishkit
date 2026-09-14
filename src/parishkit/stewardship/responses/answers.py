"""Complete final-answer validation for the versioned census vertical slice.

This is deliberately a pure function. Neither malformed nor valid input can
create a draft; the final-submit owner calls it inside its locked transaction.
Unknown fields and household identities are rejected rather than silently
discarded, so a forged/stale browser cannot choose the scope of validation.
"""

from datetime import date

from .census import HOUSEHOLD_FIELDS, InvalidHousehold, validate_household
from .census import clean_text as _text
from .financial import InvalidFinancialAnswers, validate_financial_answers
from .inputs import ADDITIONAL_MAX_LENGTH, FORM_SCHEMA, CensusInputs
from .member_requests import (
    MAX_PROPOSED_MEMBERS,
    InvalidMemberAnswers,
    existing_answers,
    local_member_id,
    ordinary_answers,
)
from .ministry import InvalidMinistryAnswers, validate_ministry_answers


class InvalidAnswers(ValueError):
    """Static messages keyed only by server-known fields, never raw private input."""

    def __init__(self, fields):
        """Keep private input out of exception strings, logs and traceback arguments."""
        self.fields = dict(fields)
        super().__init__("Please review the indicated Family form fields.")


def validate_answers(
    payload,
    inputs,
    *,
    additional_enabled,
    testing,
    today,
    retained_terminal_members=frozenset(),
):
    """Validate all active Members and explicit test consent without persisting.

    Text limits are part of the server-owned form definition. Known source
    multiple-address email fields remain usable; every nonblank address must
    pass the shared syntax validator. No browser-supplied Family identity,
    source field map, ignored key or disabled-module answer is accepted.
    """
    if (
        not isinstance(inputs, CensusInputs)
        or type(additional_enabled) is not bool
        or type(testing) is not bool
        or type(today) is not date
        or type(retained_terminal_members) is not frozenset
        or not retained_terminal_members <= {str(key) for key in inputs.member_duids}
    ):
        raise TypeError("Trusted form definition and namespace are required.")
    expected_fields = {
        "family",
        "members",
        "proposed_members",
        "ministries",
        "additional_information",
        "testing_acknowledged",
    }
    financial_enabled = "financial" in inputs.modules
    if financial_enabled:
        expected_fields.add("financial")
    if type(payload) is not dict or set(payload) != expected_fields:
        raise InvalidAnswers(
            {"form": "Reload the authorized form and review its fields."}
        )
    members = payload["members"]
    if type(members) is not dict or set(members) != {
        str(identifier) for identifier in inputs.member_duids
    }:
        raise InvalidAnswers({"members": "Review the current household members."})
    proposed = payload["proposed_members"]
    census = "census" in inputs.modules
    if (
        type(proposed) is not dict
        or len(proposed) > MAX_PROPOSED_MEMBERS
        or any(not local_member_id(key) for key in proposed)
        or (not census and bool(proposed))
    ):
        raise InvalidAnswers(
            {"proposed_members": "Review the added household members."}
        )
    errors, normalized = {}, {}
    try:
        household = (
            validate_household(
                payload["family"],
                {
                    field.field: field.source
                    for field in inputs.fields
                    if field.entity == "family" and field.field in HOUSEHOLD_FIELDS
                },
            )
            if census
            else {}
        )
        if not census and (type(payload["family"]) is not dict or payload["family"]):
            errors["family"] = "Census editing is not enabled."
    except InvalidHousehold as error:
        errors.update(
            {
                "family" if field == "family" else f"family.{field}": message
                for field, message in error.fields.items()
            }
        )
    source_fields = {
        (item.identity, item.field): item.source
        for item in inputs.fields
        if item.entity == "member"
    }
    if (
        type(payload["testing_acknowledged"]) is not bool
        or payload["testing_acknowledged"] != testing
    ):
        errors["testing_acknowledged"] = (
            "Confirm the displayed response mode before submitting."
        )
    for identifier in inputs.member_duids:
        key, member = str(identifier), members[str(identifier)]
        if not census:
            if type(member) is not dict or member:
                errors[f"members.{key}"] = "Census editing is not enabled."
            normalized[key] = {}
            continue
        try:
            normalized[key] = existing_answers(
                member,
                {
                    name: value
                    for (duid, name), value in source_fields.items()
                    if duid == identifier
                },
                today=today,
            )
        except InvalidMemberAnswers as error:
            errors.update(
                {
                    f"members.{key}" + (f".{name}" if name else ""): message
                    for name, message in error.fields.items()
                }
            )
    proposed_normalized = {}
    for key, member in proposed.items():
        try:
            proposed_normalized[key] = ordinary_answers(member, {}, today=today)
        except InvalidMemberAnswers as error:
            errors.update(
                {
                    f"proposed_members.{key}" + (f".{name}" if name else ""): message
                    for name, message in error.fields.items()
                }
            )
    try:
        ministries = validate_ministry_answers(
            payload["ministries"],
            inputs.ministries,
            terminal_members=(retained_terminal_members if not census else frozenset())
            | frozenset(
                key
                for key, values in normalized.items()
                if values.get("moved_household") or values.get("deceased_status")
            ),
            proposed_members=frozenset(proposed_normalized),
        )
    except InvalidMinistryAnswers as error:
        errors.update(error.fields)
    financial = None
    if financial_enabled:
        if inputs.financial is None:
            raise TypeError("Trusted financial inputs are required.")
        try:
            financial = validate_financial_answers(
                payload["financial"], inputs.financial.definition.options
            )
        except InvalidFinancialAnswers as error:
            errors.update(error.fields)
    additional = _text(payload["additional_information"], ADDITIONAL_MAX_LENGTH)
    if additional is None or (not additional_enabled and additional):
        errors["additional_information"] = (
            "Review the additional information field and its length limit."
        )
    if errors:
        raise InvalidAnswers(errors)
    result = {
        "schema": FORM_SCHEMA,
        "family": household,
        "members": normalized,
        "proposed_members": proposed_normalized,
        "ministries": ministries,
        "additional_information": additional,
    }
    if financial_enabled:
        result["financial"] = financial
    return result
