"""Private directory parsing and native rendering without service startup."""

import pytest
from django.http import QueryDict

from parishkit.stewardship.reports.directories import DirectoryQuery


def test_directory_filters_preserve_private_post_state():
    """Friendly codes canonicalize; page state never becomes identifying URLs."""
    query = DirectoryQuery.parse(
        QueryDict("search=Example&exact_code=abcd-efgh&phone=yes&response=no&page=2")
    )
    assert query.search == "Example" and query.exact_code == "ABCDEFGH"
    assert query.page == 2 and "page" not in query.form_values()
    assert "Example" not in repr(query) and "ABCDEFGH" not in repr(query)


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
