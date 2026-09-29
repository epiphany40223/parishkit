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
            {"annual_pledge": "0", "frequency": "", "shares": {}, "cannot_give": False},
            options,
        )


@pytest.mark.parametrize("frequency", FREQUENCIES)
def test_positive_pledge_requires_a_share_method(frequency):
    """A positive pledge needs a frequency and at least one offered share method."""
    with pytest.raises(InvalidFinancialAnswers) as failure:
        validate_financial_answers(
            {
                "annual_pledge": "10",
                "frequency": frequency,
                "shares": {},
                "cannot_give": False,
            },
            OPTIONS,
        )
    assert failure.value.fields == {
        "financial.shares": (
            "Choose at least one way you would like to share your pledge."
        )
    }
    assert validate_financial_answers(
        {
            "annual_pledge": "10",
            "frequency": frequency,
            "shares": {CHECK: ""},
            "cannot_give": False,
        },
        OPTIONS,
    ) == {
        "annual_pledge": "10.00",
        "frequency": frequency,
        "shares": {CHECK: ""},
        "cannot_give": False,
    }


def test_positive_pledge_needs_no_share_method_when_none_are_offered():
    """A campaign offering no share methods cannot require one."""
    assert (
        validate_financial_answers(
            {
                "annual_pledge": "10",
                "frequency": "annual",
                "shares": {},
                "cannot_give": False,
            },
            (),
        )["shares"]
        == {}
    )


@pytest.mark.parametrize("pledge", ["0", "0.00"])
def test_zero_pledge_needs_neither_frequency_nor_share_method(pledge):
    """The form hides both for a zero pledge, so the server requires neither."""
    assert validate_financial_answers(
        {"annual_pledge": pledge, "frequency": "", "shares": {}, "cannot_give": False},
        OPTIONS,
    ) == {"annual_pledge": "0.00", "frequency": "", "shares": {}, "cannot_give": False}


def test_zero_pledge_allows_optional_frequency_and_non_cash_intent():
    """A zero annual amount does not discard supplied valid sharing choices."""
    assert (
        validate_financial_answers(
            {"annual_pledge": "0", "frequency": "", "shares": {}, "cannot_give": False},
            OPTIONS,
        )["frequency"]
        == ""
    )
    answer = validate_financial_answers(
        {
            "annual_pledge": "0",
            "frequency": "annual",
            "shares": {OTHER: "  Cafe\u0301 gift  ", CHECK: ""},
            "cannot_give": False,
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
    payload = {
        "annual_pledge": "1",
        "frequency": "annual",
        "shares": {CHECK: ""},
        "cannot_give": False,
    } | patch
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
        "cannot_attend": False,
        "service": {},
    }
    if include:
        payload["financial"] = {
            "annual_pledge": "12.3",
            "frequency": "annual",
            "shares": {},
            "cannot_give": False,
        }
    if enabled != include:
        with pytest.raises(InvalidAnswers):
            validate_answers(
                payload,
                inputs,
                additional_enabled=False,
                today=date(2026, 10, 1),
            )
    else:
        result = validate_answers(
            payload,
            inputs,
            additional_enabled=False,
            today=date(2026, 10, 1),
        )
        assert result["financial"]["annual_pledge"] == "12.30"


def test_cannot_give_records_a_zero_pledge_without_fields():
    """ "Cannot contribute" hides the pledge fields; the answer is a bare zero."""
    for pledge in ("", "0", "0.00"):
        assert validate_financial_answers(
            {
                "annual_pledge": pledge,
                "frequency": "",
                "shares": {},
                "cannot_give": True,
            },
            OPTIONS,
        ) == {
            "annual_pledge": "0.00",
            "frequency": "",
            "shares": {},
            "cannot_give": True,
        }


@pytest.mark.parametrize(
    "patch",
    [
        {"annual_pledge": "10"},
        {"frequency": "annual"},
        {"shares": {CHECK: ""}},
        {"cannot_give": "yes"},
        {"annual_pledge": ["0"]},
        {"annual_pledge": {"0": 0}},
        {"annual_pledge": None},
        {"frequency": []},
        {"shares": []},
    ],
)
def test_cannot_give_rejects_any_pledge_detail(patch):
    """A hidden pledge, frequency or share method cannot be smuggled through."""
    payload = {"annual_pledge": "", "frequency": "", "shares": {}, "cannot_give": True}
    with pytest.raises(InvalidFinancialAnswers):
        validate_financial_answers(payload | patch, OPTIONS)


@pytest.mark.parametrize(
    ("pledge", "message"),
    [
        ("", "Enter an annual pledge."),
        ("  ", "Enter an annual pledge."),
        ("abc", "Enter a dollar amount, like 1200 or 1200.50."),
        ("1.001", "Enter a dollar amount, like 1200 or 1200.50."),
        ("-5", "Enter a dollar amount, like 1200 or 1200.50."),
        ([], "Enter a dollar amount, like 1200 or 1200.50."),
        ("12,34", "Enter a dollar amount, like 1200 or 1200.50."),
        ("1000000000", "Enter an annual pledge under $1,000,000,000."),
        ("1,000,000,000.00", "Enter an annual pledge under $1,000,000,000."),
        ("01000000000", "Enter a dollar amount, like 1200 or 1200.50."),
        # A forged pledge longer than int()'s digit limit is still a 422.
        ("1" * 5000, "Enter an annual pledge under $1,000,000,000."),
    ],
)
def test_pledge_errors_ask_plainly_when_blank(pledge, message):
    """A blank pledge is asked for; other bad input shows the amount format."""
    with pytest.raises(InvalidFinancialAnswers) as failure:
        validate_financial_answers(
            {
                "annual_pledge": pledge,
                "frequency": "",
                "shares": {},
                "cannot_give": False,
            },
            OPTIONS,
        )
    assert failure.value.fields["financial.annual_pledge"] == message
    assert "$0.00" not in message
