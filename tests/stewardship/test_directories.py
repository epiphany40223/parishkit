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


def _table(rows):
    """The shared POST report table the view builds around one SQL page."""
    from parishkit.stewardship.reports.directories import DIRECTORY_SORTING
    from parishkit.stewardship.web.tables import report_table

    return report_table(
        rows,
        number=1,
        size=50,
        total=len(rows),
        carry=(("search", "private"),),
        sorting=DIRECTORY_SORTING,
        sort="name",
        action="/admin/directory/",
        sizes=(50,),
    )


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
            "table": _table(
                [
                    {
                        "family_name": "Example",
                        "display_name": "Example",
                        "family_duid": 1,
                        "address": address,
                        "address_lines": lines,
                    }
                ]
            ),
            "query": DirectoryQuery(),
        },
    )
    assert label in html and ">None<" not in html


@pytest.mark.parametrize("testing", [False, True])
def test_open_form_link_follows_the_mode_and_keeps_the_code_in_the_fragment(testing):
    """Live rows link to the Family sign-in with the code only in the fragment.

    A fragment never reaches the server or its logs. In Testing mode live codes
    are refused, so the note replaces the links; rows without a code never link.
    """
    from uuid import UUID

    html = render_to_string(
        "stewardship/directory.html",
        {
            "campaign_id": UUID(int=80),
            "metadata": {"source_generation": 1},
            "total": 2,
            "testing_codes": testing,
            "table": _table(
                [
                    {
                        "family_name": "Example",
                        "display_name": "Example, Anna and John",
                        "family_duid": 1,
                        "code": "ABCD-EFGH",
                    },
                    {
                        "family_name": "Codeless",
                        "display_name": "Codeless",
                        "family_duid": 2,
                        "code": None,
                    },
                ]
            ),
            "query": DirectoryQuery(),
        },
    )
    link = 'href="/#code=ABCD-EFGH" target="_blank" rel="noopener" data-open-form>'
    assert html.count(link) == (0 if testing else 1)
    # The Name cell and the contact-details summary show the surname and heads;
    # a live Open form link names the Family for screen readers too.
    assert "<td>Example, Anna and John</td>" in html
    assert html.count("Example, Anna and John") == (2 if testing else 3)
    assert ("data-open-form-notice" in html) is not testing
    assert ("appear next to the codes once the campaign is live" in html) is testing


def test_directory_headings_and_pages_post_private_filters():
    """Family and DUID headings sort through the installed selection's closed
    vocabulary; every control is a POST form, so the private search never
    reaches a URL, and the page shows "Page N of M"."""
    from uuid import UUID

    from parishkit.stewardship.reports.report_paging import pop_page_size

    html = render_to_string(
        "stewardship/directory.html",
        {
            "campaign_id": UUID(int=80),
            "metadata": {"source_generation": 1},
            "total": 1,
            "table": _table(
                [{"family_name": "A", "display_name": "A", "family_duid": 1}]
            ),
            "query": DirectoryQuery(search="private"),
            "csrf_token": "token",
        },
    )
    assert 'aria-sort="ascending"' in html and "Page 1 of 1" in html
    assert '<input type="hidden" name="sort" value="name_desc">' in html
    assert '<input type="hidden" name="sort" value="duid">' in html
    assert "private" not in "".join(
        part.split('"')[0] for part in html.split("href=")[1:]
    )
    assert html.count("aria-sort") == 1
    for size in ("25", "5x", "050"):
        with pytest.raises(ValueError):
            pop_page_size(QueryDict(f"size={size}").copy(), (50,))
    assert pop_page_size(QueryDict("size=50").copy(), (50,)) == 50
