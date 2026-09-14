"""Complete final-answer validation for the versioned census vertical slice.

This is deliberately a pure function. Neither malformed nor valid input can
create a draft; the final-submit owner calls it inside its locked transaction.
Unknown fields and household identities are rejected rather than silently
discarded, so a forged/stale browser cannot choose the scope of validation.
"""

from datetime import date

from .census import HOUSEHOLD_FIELDS, InvalidHousehold, validate_household
from .census import clean_text as _text
from .inputs import ADDITIONAL_MAX_LENGTH, FORM_SCHEMA, MEMBER_FIELDS, CensusInputs
from .member_census import InvalidMemberValue, validate_member_value
from .merge import KnownValue


class InvalidAnswers(ValueError):
    """Static messages keyed only by server-known fields, never raw private input."""

    def __init__(self, fields):
        """Keep private input out of exception strings, logs and traceback arguments."""
        self.fields = dict(fields)
        super().__init__("Please review the indicated Family form fields.")


def validate_answers(payload, inputs, *, additional_enabled, testing, today):
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
    ):
        raise TypeError("Trusted form definition and namespace are required.")
    if type(payload) is not dict or set(payload) != {
        "family",
        "members",
        "additional_information",
        "testing_acknowledged",
    }:
        raise InvalidAnswers(
            {"form": "Reload the authorized form and review its fields."}
        )
    members = payload["members"]
    if type(members) is not dict or set(members) != {
        str(identifier) for identifier in inputs.member_duids
    }:
        raise InvalidAnswers({"members": "Review the current household members."})
    errors, normalized = {}, {}
    try:
        household = validate_household(
            payload["family"],
            {
                field.field: field.source
                for field in inputs.fields
                if field.entity == "family" and field.field in HOUSEHOLD_FIELDS
            },
        )
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
        if type(member) is not dict or set(member) != {
            field.name for field in MEMBER_FIELDS
        }:
            errors[f"members.{key}"] = "Review every field for this household member."
            continue
        normalized[key] = {}
        for field in MEMBER_FIELDS:
            path = f"members.{key}.{field.name}"
            try:
                normalized[key][field.name] = validate_member_value(
                    field,
                    member[field.name],
                    source_fields.get((identifier, field.name), KnownValue(False)),
                    today=today,
                )
            except InvalidMemberValue as error:
                errors[path] = str(error)
    additional = _text(payload["additional_information"], ADDITIONAL_MAX_LENGTH)
    if additional is None or (not additional_enabled and additional):
        errors["additional_information"] = (
            "Review the additional information field and its length limit."
        )
    if errors:
        raise InvalidAnswers(errors)
    return {
        "schema": FORM_SCHEMA,
        "family": household,
        "members": normalized,
        "additional_information": additional,
    }
