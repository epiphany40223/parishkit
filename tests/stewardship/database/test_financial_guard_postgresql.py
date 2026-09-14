"""The database independently rejects forged or noncanonical financial answers."""

import json
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from ..test_financial_answers import CHECK, OPTIONS, OTHER

pytestmark = pytest.mark.django_db(transaction=True)


def guard(answer, *, annual=Decimal("12.30")):
    """Exercise the exact helper invoked by the immutable Submission INSERT trigger."""
    options = [
        {"id": row.id, "label": row.label, "free_text": row.free_text}
        for row in OPTIONS
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_response_financial_guard_v1("
            "%s::jsonb,%s::jsonb,%s::numeric)",
            [json.dumps(answer), json.dumps(options), annual],
        )


def answer(**changes):
    """A complete normalized financial response, never raw browser input."""
    return {
        "annual_pledge": "12.30",
        "frequency": "annual",
        "shares": {CHECK: ""},
        **changes,
    }


@pytest.mark.parametrize("frequency", ["weekly", "monthly", "quarterly", "annual"])
def test_sql_accepts_exact_annual_and_known_share_options(frequency):
    """The guard accepts canonical normalized data, including text-bearing options."""
    guard(answer(frequency=frequency, shares={CHECK: "", OTHER: "Café gift"}))
    guard(answer(annual_pledge="0.00", frequency="", shares={}), annual=Decimal(0))


@pytest.mark.parametrize(
    "changes,annual",
    [
        ({"annual_pledge": "12.3"}, Decimal("12.30")),
        ({"annual_pledge": 12.3}, Decimal("12.30")),
        ({"annual_pledge": "-1.00"}, Decimal("-1.00")),
        ({"annual_pledge": "012.30"}, Decimal("12.30")),
        ({"annual_pledge": "1000000000.00"}, Decimal("1000000000.00")),
        ({}, None),
        ({}, Decimal("12.31")),
        ({"frequency": ""}, Decimal("12.30")),
        ({"frequency": None}, Decimal("12.30")),
        ({"frequency": "daily"}, Decimal("12.30")),
        ({"shares": []}, Decimal("12.30")),
        ({"shares": {"foreign": ""}}, Decimal("12.30")),
        ({"shares": {OTHER: ""}}, Decimal("12.30")),
        ({"shares": {OTHER: " Cafe\u0301 "}}, Decimal("12.30")),
        ({"shares": {OTHER: "\x01"}}, Decimal("12.30")),
        ({"shares": {OTHER: "a" * 2001}}, Decimal("12.30")),
        ({"shares": {CHECK: "unexpected"}}, Decimal("12.30")),
        ({"shares": {OTHER: True}}, Decimal("12.30")),
        ({"payment": "not-allowed"}, Decimal("12.30")),
    ],
    ids=[
        "fraction",
        "float",
        "negative",
        "leading-zero",
        "maximum",
        "missing-scalar",
        "mismatched-scalar",
        "missing-frequency",
        "null-frequency",
        "unknown-frequency",
        "shares-type",
        "foreign-option",
        "missing-other",
        "not-normalized",
        "control",
        "long-text",
        "unexpected-text",
        "text-type",
        "extra-key",
    ],
)
def test_sql_rejects_forged_financial_values(changes, annual):
    """Even bypassing the Python validator cannot persist these financial values."""
    with pytest.raises(IntegrityError, match="Financial"), transaction.atomic():
        guard(answer(**changes), annual=annual)


@pytest.mark.parametrize("missing", ["annual_pledge", "frequency", "shares"])
def test_sql_rejects_incomplete_financial_aggregate(missing):
    """Every enabled financial submission must contain all normalized fields."""
    value = answer()
    del value[missing]
    with pytest.raises(IntegrityError, match="Financial"), transaction.atomic():
        guard(value)
