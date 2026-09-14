"""Pure financial intent validation: exact annual amount, frequency and sharing."""

from datetime import date
from decimal import Decimal, localcontext

import pytest

from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.responses.answers import InvalidAnswers, validate_answers
from parishkit.stewardship.responses.financial import (
    FREQUENCIES,
    InvalidFinancialAnswers,
    ShareOption,
    household_pronoun,
    installment,
    pledge_amount,
    validate_financial_answers,
)
from parishkit.stewardship.responses.financial_inputs import (
    financial_definition,
    financial_inputs,
)
from parishkit.stewardship.responses.inputs import CensusInputs

from .financial_factory import CAMPAIGN, configuration

CHECK = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
OPTIONS = (
    ShareOption(CHECK, "Bank check", False),
    ShareOption(OTHER, "Other", True),
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", "0.00"),
        (" 12.3 ", "12.30"),
        ("1,234.56", "1234.56"),
        ("999,999,999.99", "999999999.99"),
        ("000.01", "0.01"),
    ],
)
def test_pledge_normalizes_us_input_without_losing_cents(value, expected):
    """Only the normalized annual amount is authoritative for persistence."""
    assert pledge_amount(value).canonical == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        0,
        12.3,
        Decimal("12.30"),
        "",
        "-1",
        "+1",
        "1e2",
        "NaN",
        "1.001",
        "1,23",
        "1,2345.67",
        "1000000000",
        "0.١",
        "0.1\x00",
    ],
)
def test_invalid_pledge_input_is_rejected(value):
    """Empty is not a zero pledge and invalid monetary syntax is never rounded."""
    with pytest.raises(ValueError):
        pledge_amount(value)


@pytest.mark.parametrize(
    "frequency,expected",
    [
        ("weekly", "23.08"),
        ("monthly", "100.00"),
        ("quarterly", "300.00"),
        ("annual", "1200.00"),
    ],
)
def test_installments_use_fixed_periods_without_decimal_context_rounding(
    frequency, expected
):
    """Context precision cannot change exact annual or approximate installment."""
    with localcontext() as context:
        context.prec = 2
        annual = pledge_amount("1200")
        assert installment(annual, frequency).canonical == expected
        assert annual.canonical == "1200.00"
    assert installment(MoneyAmount(6), "monthly").canonical == "0.01"


@pytest.mark.parametrize(
    "annual,frequency",
    [
        (MoneyAmount(None), "annual"),
        (MoneyAmount(-1), "annual"),
        (MoneyAmount(100_000_000_000), "annual"),
        (MoneyAmount(0), ""),
        (MoneyAmount(0), "daily"),
        (MoneyAmount(0), None),
        ("1.00", "annual"),
    ],
)
def test_installment_rejects_invalid_internal_inputs(annual, frequency):
    """Optional zero-pledge frequency means no installment, not a guessed divisor."""
    with pytest.raises(ValueError, match="annual pledge and frequency"):
        installment(annual, frequency)


@pytest.mark.parametrize("options", [[], (object(),), (OPTIONS[0], OPTIONS[0])])
def test_financial_validator_requires_unambiguous_trusted_option_identities(options):
    """A malformed server catalog cannot silently overwrite a duplicate option."""
    with pytest.raises(TypeError):
        validate_financial_answers(
            {"annual_pledge": "0", "frequency": "", "shares": {}}, options
        )


@pytest.mark.parametrize("frequency", FREQUENCIES)
def test_positive_pledge_can_submit_without_selecting_a_share_method(frequency):
    """The specification requires frequency, but does not require a share method."""
    assert validate_financial_answers(
        {"annual_pledge": "10", "frequency": frequency, "shares": {}}, OPTIONS
    ) == {"annual_pledge": "10.00", "frequency": frequency, "shares": {}}


def test_zero_pledge_allows_optional_frequency_and_non_cash_intent():
    """A zero annual amount does not discard supplied valid sharing choices."""
    assert (
        validate_financial_answers(
            {"annual_pledge": "0", "frequency": "", "shares": {}}, OPTIONS
        )["frequency"]
        == ""
    )
    answer = validate_financial_answers(
        {
            "annual_pledge": "0",
            "frequency": "annual",
            "shares": {OTHER: "  Cafe\u0301 gift  ", CHECK: ""},
        },
        OPTIONS,
    )
    assert answer["shares"] == {CHECK: "", OTHER: "Café gift"}


@pytest.mark.parametrize(
    "patch,field",
    [
        ({"annual_pledge": ""}, "financial.annual_pledge"),
        ({"frequency": ""}, "financial.frequency"),
        ({"frequency": None}, "financial.frequency"),
        ({"frequency": "daily"}, "financial.frequency"),
        ({"shares": []}, "financial.shares"),
        ({"shares": {"foreign-private-id": "private-answer"}}, "financial.shares"),
        ({"shares": {CHECK: "unexpected"}}, f"financial.shares.{CHECK}"),
        ({"shares": {OTHER: "  "}}, f"financial.shares.{OTHER}"),
        ({"shares": {OTHER: "a" * 2001}}, f"financial.shares.{OTHER}"),
    ],
    ids=[
        "missing-pledge",
        "missing-frequency",
        "null-frequency",
        "unknown-frequency",
        "invalid-shares",
        "foreign-option",
        "unexpected-text",
        "missing-text",
        "long-text",
    ],
)
def test_final_financial_errors_use_only_server_known_field_keys(patch, field):
    """Validation reports trusted controls without echoing values or forged IDs."""
    payload = {"annual_pledge": "1", "frequency": "annual", "shares": {}} | patch
    with pytest.raises(InvalidFinancialAnswers) as failure:
        validate_financial_answers(payload, OPTIONS)
    assert field in failure.value.fields
    assert "private" not in str(failure.value.fields)


@pytest.mark.parametrize("payload", [None, {}, {"annual_pledge": "0"}, {"payment": {}}])
def test_closed_financial_payload_cannot_accept_payment_instructions(payload):
    """No bank/card/provider instruction pocket can be smuggled into the answer."""
    with pytest.raises(InvalidFinancialAnswers, match="financial stewardship"):
        validate_financial_answers(payload, OPTIONS)


@pytest.mark.parametrize("count,expected", [(0, "This household"), (1, "I"), (2, "We")])
def test_effective_household_wording(count, expected):
    """Empty effective households still have a usable Family-level financial form."""
    assert household_pronoun(count) == expected


@pytest.mark.parametrize("count", [True, -1, 1.5, "1"])
def test_household_wording_rejects_invalid_counts(count):
    """Do not reinterpret invalid counts as singular or plural."""
    with pytest.raises(ValueError):
        household_pronoun(count)


@pytest.mark.parametrize(
    "enabled,include", [(False, True), (True, False), (True, True)]
)
def test_complete_answer_owner_requires_financial_only_for_enabled_module(
    enabled, include
):
    """Reject omitted enabled and supplied disabled financial sections."""
    definition = financial_definition(configuration(), campaign_id=CAMPAIGN)
    money = financial_inputs(
        definition, None, family_duid=1, pledges=[], contributions=[]
    )
    inputs = CensusInputs(
        1,
        (3,),
        (),
        "d" * 64,
        modules=("financial",) if enabled else ("ministry",),
        financial=money if enabled else None,
    )
    payload = {
        "family": {},
        "members": {"3": {}},
        "proposed_members": {},
        "ministries": {},
        "additional_information": "",
        "testing_acknowledged": False,
    }
    if include:
        payload["financial"] = {
            "annual_pledge": "12.3",
            "frequency": "annual",
            "shares": {},
        }
    if enabled != include:
        with pytest.raises(InvalidAnswers):
            validate_answers(
                payload,
                inputs,
                additional_enabled=False,
                testing=False,
                today=date(2026, 10, 1),
            )
    else:
        result = validate_answers(
            payload,
            inputs,
            additional_enabled=False,
            testing=False,
            today=date(2026, 10, 1),
        )
        assert result["financial"]["annual_pledge"] == "12.30"
