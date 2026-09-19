"""Private directory parsing and native rendering without service startup."""

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.reports.directories import DirectoryQuery, address_lines


def test_directory_filters_preserve_private_post_state():
    """Friendly codes canonicalize; page state never becomes identifying URLs."""
    query = DirectoryQuery.parse(
        QueryDict("search=Example&exact_code=abcd-efgh&phone=yes&response=no&page=2")
    )
    assert query.search == "Example" and query.exact_code == "ABCDEFGH"
    assert query.page == 2 and "page" not in query.form_values()
    assert "Example" not in repr(query) and "ABCDEFGH" not in repr(query)
    audit = sanitize(ContextKind.ACTION, query.audit_values())
    assert audit["search_used"] and audit["exact_code_used"]
    assert audit["directory_phone"] == "yes" and audit["page"] == 2
    assert "Example" not in str(audit) and "ABCDEFGH" not in str(audit)


@pytest.mark.parametrize(
    "values",
    [
        "search=a&search=b",
        "secret=x",
        "page=0",
        "page=01",
        "page=10001",
        "exact_code=short",
        "exact_code=12345678",
        "exact_code=ＡＢＣＤＥＦＧＨ",
        "reason=private",
        "phone=maybe",
        "response=maybe",
        "sort=sql",
        "search=" + "x" * 201,
        "search=%00",
    ],
)
def test_invalid_directory_filters_are_value_free(values):
    """Duplicate, unknown, malformed and oversized filters have safe errors."""
    with pytest.raises(ValueError):
        DirectoryQuery.parse(QueryDict(values))


@pytest.mark.parametrize(
    "context",
    [
        {"directory_reason": "private-address@example.org"},
        {"directory_phone": "202-555-0123"},
        {"directory_response": "ABCDEFGH"},
        {"directory_sort": "Private Family"},
        {"search_used": "Private Family"},
        {"exact_code_used": "ABCDEFGH"},
        {"matching_count": "Private Family"},
    ],
)
def test_directory_audit_rejects_private_filter_values(context):
    """New audit dimensions remain a closed enum/boolean/count contract."""
    with pytest.raises(ValueError):
        sanitize(ContextKind.ACTION, context)


@pytest.mark.parametrize(
    "address, expected, label",
    [
        ({}, (), "Unavailable"),
        ({"primaryAddress1": None, "primaryCity": None}, (), "No address supplied"),
        ({"primaryAddress1": "  ", "primaryCity": ""}, (), "No address supplied"),
        (
            {
                "primaryAddress1": "1 Example St",
                "primaryAddress2": None,
                "primaryCity": "Town",
                "primaryState": "KY",
                "primaryPostalCode": "40000",
                "primaryZipPlus": "1234",
            },
            ("1 Example St", "Town KY 40000-1234"),
            "Town KY 40000-1234",
        ),
    ],
)
def test_primary_address_nulls_and_unavailable_state(address, expected, label):
    """Production HTML distinguishes a missing record from a known blank one."""
    from uuid import UUID

    lines = address_lines(address)
    assert lines == expected
    html = render_to_string(
        "stewardship/directory.html",
        {
            "campaign_id": UUID(int=80),
            "metadata": {"source_generation": 1},
            "total": 1,
            "rows": [
                {
                    "family_name": "Example",
                    "family_duid": 1,
                    "address": address,
                    "address_lines": lines,
                }
            ],
            "query": DirectoryQuery(),
        },
    )
    assert label in html and ">None<" not in html
