"""Private directory parsing and native rendering without service startup."""

from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.audit.schemas import (
    DIRECTORY_RESPONSES,
    DIRECTORY_SORTS,
    ContextKind,
    sanitize,
)
from parishkit.stewardship.reports.directories import (
    DIRECTORY_SORTING,
    MISSING_NAME,
    RESPONSE_COLUMN_SETS,
    RESPONSE_COLUMNS,
    RESPONSES,
    DirectoryQuery,
    add_responses,
    address_lines,
    head_email_groups,
    head_emails_text,
    response_columns,
    row_name,
    selection_parameters,
)


def test_directory_filters_preserve_private_post_state():
    """Friendly codes canonicalize; page state never becomes identifying URLs."""
    query = DirectoryQuery.parse(
        QueryDict(
            "search=Example&exact_code=abcd-efgh&phone=yes&response=never-opened"
            "&check=envelope&responses=yes&sort=invited_desc&page=2"
        )
    )
    assert query.search == "Example" and query.exact_code == "ABCDEFGH"
    assert query.page == 2 and "page" not in query.form_values()
    assert "Example" not in repr(query) and "ABCDEFGH" not in repr(query)
    audit = sanitize(ContextKind.ACTION, query.audit_values())
    assert audit["search_used"] and audit["exact_code_used"]
    assert audit["directory_phone"] == "yes" and audit["page"] == 2
    # The Response, data check, response columns and order are closed (#933).
    assert audit["directory_response"] == "never-opened"
    assert audit["directory_data_check"] == "envelope"
    assert audit["directory_response_columns"] is True
    assert audit["directory_sort"] == "invited_desc"
    assert "Example" not in str(audit) and "ABCDEFGH" not in str(audit)
    # The response columns choose columns, not rows: they leave the filters
    # and travel beside them, like the mailing columns.
    parameters = selection_parameters(uuid4(), DirectoryQuery(), postal=False, mac=None)
    assert parameters["responses"] is False and "responses" not in parameters["filters"]
    assert parameters["filters"]["check"] == "any"
    on = DirectoryQuery(responses="yes", response="submitted")
    parameters = selection_parameters(uuid4(), on, postal=True, mac=None)
    assert parameters["responses"] is True and parameters["postal"] is True
    assert parameters["filters"]["response"] == "submitted"


@pytest.mark.parametrize(
    "response, dates, counts",
    [
        ("any", ["invited", "opened", "submitted"], ["submissions"]),
        ("submitted", ["submitted"], ["submissions"]),
        ("more-than-once", ["submitted", "last"], ["submissions"]),
        ("started", ["opened", "progressed"], []),
        ("opened-only", ["opened", "progressed"], []),
        ("never-opened", ["invited", "link"], []),
        ("link-not-followed", ["invited", "link"], []),
        ("not-submitted", ["invited", "opened", "progressed"], []),
        ("not-invited", ["link", "opened"], []),
    ],
)
def test_response_columns_follow_the_lists(response, dates, counts):
    """Each Response shows the columns its old list had, dates in event order."""
    shown = response_columns(response)
    assert [column.key for column in shown[0]] == dates
    assert [column.key for column in shown[1]] == counts
    assert all(not column.count for column in shown[0])


@pytest.mark.parametrize(
    "values, sort",
    [
        # Shown: the columns are on and the choice has the column.
        ("responses=yes&response=submitted&sort=submitted_desc", "submitted_desc"),
        ("responses=yes&sort=invited", "invited"),
        # Hidden: the columns are off, or the choice does not show it.
        ("response=submitted&sort=submitted_desc", "name"),
        ("sort=invited_desc", "name"),
        ("responses=yes&response=submitted&sort=invited_desc", "name"),
        ("responses=yes&response=not-invited&sort=submissions", "name"),
        # Family and DUID always show.
        ("sort=duid", "duid"),
        ("sort=duid_desc", "duid_desc"),
        ("response=submitted&sort=name_desc", "name_desc"),
    ],
)
def test_a_hidden_response_column_falls_back_to_the_name_order(values, sort):
    """The page, the export and the audit never order by an unseen column."""
    query = DirectoryQuery.parse(QueryDict(values))
    assert query.sort == sort
    assert query.audit_values()["directory_sort"] == sort
    assert (
        selection_parameters(uuid4(), query, postal=False, mac=None)["filters"]["sort"]
        == sort
    )


def test_active_only_responses():
    """Any, Not submitted and No invitation delivered list active Families only."""
    choices = ("any", *RESPONSES)
    active = {key for key in choices if DirectoryQuery(response=key).active_only()}
    assert active == {"any", "not-submitted", "not-invited"}


def test_every_response_and_sort_is_a_closed_audit_value():
    """Python's vocabularies are exactly what the audit allowlist admits."""
    assert {"any", *RESPONSES} | {"yes", "no"} == DIRECTORY_RESPONSES
    assert set(DIRECTORY_SORTING.tokens) == DIRECTORY_SORTS
    assert set(RESPONSE_COLUMN_SETS) == {"any", *RESPONSES}
    # Every response column sorts both ways, newest or largest first.
    for key in RESPONSE_COLUMNS:
        assert DIRECTORY_SORTING.tokens[f"{key}_desc"] == (key, True)
        assert DIRECTORY_SORTING.tokens[key] == (key, False)


def test_response_rows_read_instants_checks_and_inactive_families():
    """ISO instants become datetimes; old captures read as nothing reached."""
    rows = [
        {
            "invited_at": "2026-10-01T09:00:00+00:00",
            "submitted_at": None,
            "submissions": 2,
            "active": False,
            "mailing_name_blank": True,
            "envelope_zero": True,
        },
        {},
    ]
    add_responses(rows)
    assert rows[0]["invited_at"] == datetime(2026, 10, 1, 9, tzinfo=UTC)
    assert rows[0]["submitted_at"] is None and rows[0]["submissions"] == 2
    assert rows[0]["checks"] == ["Blank mailing name", "Envelope number 0"]
    assert rows[0]["active"] is False
    assert rows[1]["active"] is True and rows[1]["checks"] == []
    assert rows[1]["link_at"] is None and rows[1]["submissions"] == 0
    assert row_name({"family_name": None, "heads": []}) == MISSING_NAME


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
        # The older Campaign response values are no longer choices (#933).
        "response=yes",
        "check=maybe",
        "responses=on",
        "sort=-submitted",
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
        {"directory_data_check": "Private Family"},
        {"directory_response_columns": "yes"},
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
def test_open_form_follows_the_mode_and_keeps_the_code_out_of_urls(testing):
    """Live rows post Open form's hand-off (#529); no URL carries the code.

    In Testing mode live codes are refused, so the note replaces the buttons;
    rows without a code never offer one.
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
    action = (
        f'action="/admin/reports/families/{UUID(int=81)}/open-form/"'
        ' target="_blank" rel="noopener" data-open-form data-submit-repeatable>'
    )
    assert html.count(action) == (0 if testing else 1)
    assert "#code=" not in html
    # The Name cell and the contact-details summary show the surname and heads;
    # a live Open form link names the Family for screen readers too.
    # A Family's name opens its timeline by its opaque campaign record id;
    # a row without one (none in practice) stays plain text. The Family cell
    # heads its row (#932).
    assert (
        '<th scope="row"><a href="'
        f'{reverse("admin:family_timeline", args=[UUID(int=81)])}">'
        "Example, Anna and John</a></th>"
    ) in html
    assert '<th scope="row">Codeless</th>' in html
    assert html.count("Example, Anna and John") == (2 if testing else 3)
    assert ("data-open-form-notice" in html) is not testing
    assert ("appear next to the codes once the campaign is live" in html) is testing


@pytest.mark.parametrize("administrator", [False, True])
def test_testing_notice_links_the_test_page_only_when_given(administrator):
    """Staff get the notice without the Administrator-only test link (#591).

    The view passes the link only to Administrators; without it the notice
    keeps its explanation and tells Staff whom to ask instead.
    """
    from uuid import UUID

    html = render_to_string(
        "stewardship/directory.html",
        {
            "campaign_id": UUID(int=80),
            "metadata": {"source_generation": 1},
            "total": 0,
            "testing_codes": True,
            "testing_codes_administrator": administrator,
            "family_test_url": "/admin/test-send/" if administrator else None,
            "table": _table([]),
            "query": DirectoryQuery(),
        },
    )
    assert "work only after go-live" in html
    assert ('<a href="/admin/test-send/">' in html) is administrator
    assert ("Try the Family form as a chosen Family" in html) is administrator
    assert ("send yourself a test invitation" in html) is administrator
    assert ("ask an Administrator for a test invitation" in html) is not administrator


@pytest.mark.parametrize(
    "response, active",
    [
        ("any", True),
        ("not-submitted", True),
        ("not-invited", True),
        ("submitted", False),
    ],
)
def test_population_header_follows_the_response_choice(response, active):
    """The header drops "Active registered" when inactive Families may be listed.

    It names neither the campaign (#1125) nor the internal data load number.
    """
    from uuid import UUID

    html = render_to_string(
        "stewardship/directory.html",
        {
            "campaign_id": UUID(int=80),
            "metadata": {"name": "Campaign", "source_generation": 3},
            "total": 0,
            "table": _table([]),
            "query": DirectoryQuery(response=response),
        },
    )
    header = html.split('<p id="directory-population" data-table-sync>')[1]
    header = header.split("</p>")[0]
    assert ("Active registered Families from ParishSoft data as of" in header) is active
    assert "Families from ParishSoft data as of" in header
    assert "Campaign" not in header and "data load" not in header


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
            "Anna Example: family@example.org; Dan Example: (Not in ParishSoft)",
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


@pytest.mark.parametrize("reach", ["any", "email", "mail", "neither"])
def test_mail_merge_always_lists_by_postal_mail_only(reach):
    """Postal invitations are only for Families without deliverable email (#951).

    Whatever reach is asked for, mailing columns use "By postal mail only"
    and keep every other filter; the code list keeps the asked-for reach.
    """
    from uuid import uuid4

    query = DirectoryQuery(
        reach=reach, phone="yes", response="not-submitted", sort="duid"
    )
    assert query.postal() == DirectoryQuery(
        reach="mail", phone="yes", response="not-submitted", sort="duid"
    )
    campaign = uuid4()
    postal = selection_parameters(campaign, query, postal=True, mac=None)
    listed = selection_parameters(campaign, query, postal=False, mac=None)
    assert postal["filters"] == listed["filters"] | {"reach": "mail"}
    assert listed["filters"]["reach"] == reach and postal["postal"]


def test_page_address_keeps_only_link_safe_presets():
    """The address bar after an in-place answer carries closed presets only.

    A preset link's query would otherwise stay in the address bar, so a
    Reload or Back would bring it back after the reader changed a filter
    (#933). Search text or an exact code makes it the bare page; the page
    number is never carried.
    """
    from parishkit.stewardship.reports.directory_views import (
        LINK_PRESETS,
        page_address,
    )
    from parishkit.stewardship.reports.response_dashboard import DIRECTORY_PRESETS

    route = reverse("admin:family_directory")
    assert page_address(route, DirectoryQuery(), postal=False) == route
    # Each dashboard link's own query comes back unchanged.
    for preset in DIRECTORY_PRESETS.values():
        query = DirectoryQuery.parse(QueryDict(urlencode(preset)))
        address = page_address(route, query, postal=False)
        assert QueryDict(address.partition("?")[2]).dict() == preset
    query = DirectoryQuery(response="started", reach="mail", page=3)
    address = page_address(route, query, postal=True)
    assert address == route + "?response=started&reach=mail&mailing=yes"
    assert set(QueryDict(address.partition("?")[2])) <= LINK_PRESETS
    for private in (
        DirectoryQuery(response="started", search="Adams"),
        DirectoryQuery(response="started", exact_code="ABCDEFGH"),
    ):
        assert page_address(route, private, postal=True) == route


def test_directory_tags_a_current_response_staff_entered():
    """Responded says "entered by Staff" only for the rows the view marks (#529).

    The view marks a Family whose effective (latest) response Staff entered
    through Open form; every other responded row just says Yes.
    """
    from uuid import UUID

    rows = [
        {
            "family_name": name,
            "display_name": name,
            "family_duid": duid,
            "family_id": str(UUID(int=duid)),
            "code": None,
            "responded": True,
        }
        for name, duid in (("Assisted", 1), ("Own", 2))
    ]
    html = render_to_string(
        "stewardship/directory.html",
        {
            "campaign_id": UUID(int=80),
            "metadata": {"source_generation": 1},
            "total": 2,
            "table": _table(rows),
            "query": DirectoryQuery(),
            "staff_entered": frozenset({str(UUID(int=1))}),
        },
    )
    assert html.count("(entered by Staff)") == 1
    assert html.index("Assisted") < html.index("(entered by Staff)")
    assert html.index("(entered by Staff)") < html.index(">Own<")


def test_testing_mode_is_a_closed_link_preset_admitted_for_administrators():
    """The Testing switch (#933): a closed value, audited, Administrators only.

    ``mode`` travels in links (the dashboard's Testing presets), in the
    address bar after an in-place answer and in the audit; any other value
    is refused, and a Staff member asking for Testing is refused too.
    """
    from types import SimpleNamespace

    from parishkit.stewardship.reports.directories import admit_mode
    from parishkit.stewardship.reports.directory_views import (
        LINK_PRESETS,
        page_address,
    )

    assert "mode" in LINK_PRESETS
    assert DirectoryQuery.parse(QueryDict("")).mode == "production"
    query = DirectoryQuery.parse(QueryDict("response=submitted&mode=testing"))
    assert query.mode == "testing"
    with pytest.raises(ValueError):
        DirectoryQuery.parse(QueryDict("mode=live"))
    with pytest.raises(ValueError):
        DirectoryQuery.parse(QueryDict("mode=testing&mode=testing"))
    values = query.audit_values()
    assert values["report_mode"] == "testing"
    assert sanitize(ContextKind.ACTION, values)["report_mode"] == "testing"
    assert DirectoryQuery().audit_values()["report_mode"] == "production"
    route = reverse("admin:family_directory")
    assert page_address(route, query, postal=False) == (
        route + "?response=submitted&mode=testing"
    )
    assert page_address(route, DirectoryQuery(mode="testing"), postal=False) == (
        route + "?mode=testing"
    )
    # Search text still makes it the bare page, mode or not.
    private = DirectoryQuery(mode="testing", search="Adams")
    assert page_address(route, private, postal=False) == route
    administrator = SimpleNamespace(roles=frozenset({"administrator"}))
    staff = SimpleNamespace(roles=frozenset({"staff"}))
    admit_mode(administrator, "testing")
    admit_mode(staff, "production")
    with pytest.raises(PermissionError):
        admit_mode(staff, "testing")


def test_testing_selection_pins_the_active_rehearsal(monkeypatch):
    """A Testing selection names its rehearsal; Production keeps its shape.

    The page and the export capture read the same parameters, so one result
    never mixes modes (#933). With no active rehearsal there is nothing to
    read, and the selection is refused rather than shown as Production.
    """
    from uuid import UUID

    from parishkit.stewardship.reports import response_dashboard
    from parishkit.stewardship.reports.directories import NoRehearsal

    epoch = UUID(int=7)
    monkeypatch.setattr(response_dashboard, "rehearsal_epoch", lambda campaign: epoch)
    campaign = uuid4()
    production = selection_parameters(
        campaign, DirectoryQuery(response="submitted"), postal=False, mac=None
    )
    assert "mode" not in production and "epoch" not in production
    assert "mode" not in production["filters"]
    testing = selection_parameters(
        campaign,
        DirectoryQuery(response="submitted", mode="testing"),
        postal=False,
        mac=None,
    )
    assert testing == production | {"mode": "testing", "epoch": str(epoch)}
    # An export form names the rehearsal its page showed: that one, or none.
    testing_query = DirectoryQuery(response="submitted", mode="testing")
    assert (
        selection_parameters(
            campaign, testing_query, postal=False, mac=None, rehearsal=epoch
        )
        == testing
    )
    with pytest.raises(NoRehearsal):
        selection_parameters(
            campaign, testing_query, postal=False, mac=None, rehearsal=UUID(int=8)
        )
    monkeypatch.setattr(response_dashboard, "rehearsal_epoch", lambda campaign: None)
    with pytest.raises(NoRehearsal):
        selection_parameters(
            campaign, DirectoryQuery(mode="testing"), postal=False, mac=None
        )


@pytest.mark.parametrize("testing", [False, True])
def test_testing_view_is_labelled_and_leaves_out_production_responses(testing):
    """A Testing view says so and drops Responded, which reads Production.

    The switch itself is drawn only when offered (``can_test``): Staff and
    an Administrator with no rehearsal get a hidden Production field.
    """
    from uuid import UUID

    from parishkit.stewardship.reports.directories import MODES

    def page(**context):
        """The directory page with one Family row."""
        return render_to_string(
            "stewardship/directory.html",
            {
                "campaign_id": UUID(int=80),
                "metadata": {"source_generation": 1},
                "total": 1,
                "modes": MODES,
                "table": _table(
                    [
                        {
                            "family_name": "Example",
                            "display_name": "Example",
                            "family_duid": 1,
                            "responded": True,
                        }
                    ]
                ),
                "query": DirectoryQuery(mode="testing" if testing else "production"),
                "testing_responses": testing,
            }
            | context,
        )

    offered = page(can_test=True)
    assert '<select id="directory-mode" name="mode"' in offered
    assert 'id="directory-mode-tip"' in offered
    selected = "testing" if testing else "production"
    assert f'<option value="{selected}" selected>' in offered
    hidden = page(can_test=False)
    assert 'id="directory-mode"' not in hidden
    # No hidden stand-in either: an in-place answer drawn after the
    # rehearsal ended would add it next to a switch still on the page, and
    # every later Apply would send mode twice (400). Without one the form
    # sends nothing, which is Production.
    filters = hidden.split('id="table-filters"')[1].split("</form>")[0]
    assert 'name="mode"' not in filters
    assert ("data-testing-responses" in hidden) is testing
    assert (">Responded</th>" in hidden) is not testing


@pytest.mark.parametrize("mode", [None, "production", "testing"])
def test_directory_exports_audit_and_admit_their_mode(monkeypatch, mode):
    """Each directory export's audit names its mode; Testing is Admin only.

    Captures from before the Testing switch have no ``mode`` and are
    Production (#933). A Staff requester (or one who stopped being an
    Administrator) can no longer download a Testing export.
    """
    from types import SimpleNamespace

    from parishkit.stewardship.audit.schemas import Action
    from parishkit.stewardship.reports import export_services
    from parishkit.stewardship.reports.export_models import ExportRequest

    parameters = {"filters": {}, "postal": False, "exact": False, "family_id": None}
    if mode is not None:
        parameters["mode"] = mode
    requester = uuid4()
    request = ExportRequest(
        campaign_id=uuid4(),
        report="family_directory",
        format="csv",
        parameters=parameters,
        requester_id=requester,
    )
    recorded = []
    monkeypatch.setattr(
        export_services,
        "record_action",
        lambda action, **evidence: recorded.append(evidence["context"]),
    )
    export_services.audit(Action.EXPORT_REQUESTED, request, requester, outcome="x")
    shown = mode or "production"
    # The report kind and format too (#1398).
    assert recorded == [
        {"outcome": "x", "report": "family_directory", "format": "csv"}
        | {"report_mode": shown}
    ]
    assert sanitize(ContextKind.ACTION, {"report_mode": shown})
    monkeypatch.setattr(export_services, "allows", lambda principal, need: True)
    for roles, admitted in (({"administrator"}, True), ({"staff"}, shown != "testing")):
        principal = SimpleNamespace(identity=requester, roles=frozenset(roles))
        monkeypatch.setattr(
            export_services,
            "current_principal",
            lambda store, user, principal=principal: principal,
        )
        if admitted:
            assert export_services.authorize(None, requester, request=request)
        else:
            with pytest.raises(PermissionError):
                export_services.authorize(None, requester, request=request)


@pytest.mark.parametrize(
    "report, mode, name",
    [
        ("family_directory", None, "family_directory.csv"),
        ("family_directory", "production", "family_directory.csv"),
        ("family_directory", "testing", "family_directory-testing.csv"),
        ("postal_outreach", "testing", "postal_outreach-testing.csv"),
        ("ministry", "testing", "ministry.csv"),
    ],
)
def test_a_testing_export_says_so_in_its_file_name(report, mode, name):
    """A CSV has no report details, so its name carries the mode (#933)."""
    from parishkit.stewardship.reports.export_models import ExportRequest
    from parishkit.stewardship.reports.export_views import file_name

    parameters = {"filters": {}} | ({"mode": mode} if mode else {})
    job = ExportRequest(report=report, format="csv", parameters=parameters)
    assert file_name(job) == name
