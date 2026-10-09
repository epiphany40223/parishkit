"""Private directory parsing and native rendering without service startup."""

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.reports.directories import (
    DirectoryQuery,
    address_lines,
    head_email_groups,
    head_emails_text,
)


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
        {"directory_reach": "postal"},
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
                        "family_id": str(UUID(int=81)),
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
    # A Family's name opens its timeline by its opaque campaign record id;
    # a row without one (none in practice) stays plain text.
    assert (
        f'<td><a href="{reverse("admin:family_timeline", args=[UUID(int=81)])}">'
        "Example, Anna and John</a></td>"
    ) in html
    assert "<td>Codeless</td>" in html
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


def _email(value, valid=True):
    """One canonical contact email entry."""
    return {"value": value, "valid": valid}


ANNA = {"name": "Anna Example", "emails": [_email("family@example.org")]}
BEN = {"name": "Ben Example", "emails": [_email(" FAMILY@Example.org ")]}
CARA = {"name": "Cara Example", "emails": [_email("cara@example.org")]}
DAN = {"name": "Dan Example", "emails": []}


@pytest.mark.parametrize(
    "heads, expected",
    [
        # A shared address once, with both heads.
        (
            [ANNA, ANNA | {"name": "Ben Example"}],
            "Anna Example and Ben Example: family@example.org",
        ),
        # Mixed case and stray spaces are the same address; the first
        # spelling seen is shown.
        ([ANNA, BEN], "Anna Example and Ben Example: family@example.org"),
        # One shared and one distinct address, in head order.
        (
            [ANNA, BEN | {"emails": [*BEN["emails"], _email("ben@example.org")]}],
            "Anna Example and Ben Example: family@example.org; "
            "Ben Example: ben@example.org",
        ),
        # Heads without an email are still listed, in their place.
        (
            [DAN, ANNA, DAN | {"name": "Eve Example"}, CARA],
            "Dan Example: (no email); Anna Example: family@example.org; "
            "Eve Example: (no email); Cara Example: cara@example.org",
        ),
        # Two case variants held by one head name that head once.
        (
            [{"name": "Gus Example", "emails": [_email("G@x"), _email("g@x")]}],
            "Gus Example: G@x",
        ),
        (
            [{"name": "Hal Example", "emails": [_email("hal at home", False)]}],
            "Hal Example: hal at home (not a valid address; fix in ParishSoft)",
        ),
        # An export's current data no longer has this head at all.
        (
            [ANNA, DAN | {"missing": True}],
            "Anna Example: family@example.org; "
            "Dan Example: (not in current ParishSoft data)",
        ),
        ([], ""),
    ],
)
def test_head_emails_are_listed_once_per_address(heads, expected):
    """Shared head addresses are de-duplicated case-insensitively (#604)."""
    assert head_emails_text(heads) == expected


def test_contact_details_list_each_address_with_its_heads():
    """The pane folds emails into the heads entry: one line per address (#604).

    Valid addresses are mailto links; a head without one reads "No email on
    file"; source text that is not a valid address is shown for correction
    but never linked; a local part that would start a mailto query is
    percent-encoded.
    """
    from uuid import UUID

    def pane(heads):
        """Render one row's heads-and-emails entry for the given heads."""
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
                            "heads": heads,
                            "head_emails": head_email_groups(heads),
                        }
                    ]
                ),
                "query": DirectoryQuery(),
            },
        )
        assert html.count("Active Family heads") == 1
        return html.split('<dd class="head-emails">')[1].split("</dd>")[0]

    html = pane(
        [
            ANNA | {"emails": [*ANNA["emails"], _email("not-an-address", False)]},
            BEN,
            {"name": "Cara Example", "emails": [_email("a?b@example.org")]},
            DAN,
        ]
    )
    assert html == (
        'Anna Example and Ben Example — <a href="mailto:family@example.org">'
        "family@example.org</a>"
        "<br>Anna Example — not-an-address "
        "<small>(not a valid address; fix in ParishSoft)</small>"
        '<br>Cara Example — <a href="mailto:a%3Fb@example.org">a?b@example.org</a>'
        "<br>Dan Example — No email on file"
    )
    assert pane([]) == "No active head"


def test_directory_audit_records_every_reach_choice():
    """Each reach word is audited and admitted by the closed schema (#388 L1)."""
    for reach in ("any", "email", "mail", "neither"):
        values = DirectoryQuery(reach=reach).audit_values()
        assert values["directory_reach"] == reach
        assert sanitize(ContextKind.ACTION, values)["directory_reach"] == reach
