"""An Administrator's edited census value, without a database (#528, ADM-09.04).

The service and the SQL guard are tested in
``database/test_census_edit_postgresql.py``; these tests cover the value
rules, the row's Edit value control and the page's form parsing.
"""

from datetime import date

import pytest
from django.http import QueryDict

from parishkit.stewardship.reports.census_change_views import (
    SORTING,
    _resolution,
    row_controls,
    row_dialogs,
)
from parishkit.stewardship.reports.census_changes import CensusQuery, shape
from parishkit.stewardship.responses.census_review import (
    InvalidEdit,
    edit_limit,
    edited_value,
)
from parishkit.stewardship.web.tables import paginate

from .test_census_changes import row

TODAY = date(2054, 10, 6)


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("first_name", "  Pat ", "Pat"),
        ("birth_date", "2000-02-29", "2000-02-29"),
        ("email", "B@Example.org; a@example.org", "a@example.org, b@example.org"),
        (
            "mobile_phone",
            "(317) 555-0199",
            {"normalized": "+13175550199", "display": "(317) 555-0199"},
        ),
    ],
)
def test_an_edit_is_read_as_the_family_form_reads_its_answer(field, raw, expected):
    """Stored in the shape of the Family's own answer for that field."""
    assert edited_value(field, raw, today=TODAY) == expected


@pytest.mark.parametrize(
    ("field", "raw", "message"),
    [
        ("first_name", "", "Enter the value to publish"),
        ("email", "not-an-address", "separated by commas or semicolons"),
        ("middle_name", "  ", "Enter the value to publish"),
        ("first_name", "x" * 31, "at most 30 characters"),
        ("email", f"{'a' * 70}@example.org; {'b' * 70}@example.org", "at most 150"),
        ("birth_date", "2054-10-07", "no later than today"),
        ("home_phone", "12", "complete phone number"),
    ],
)
def test_a_value_publication_would_refuse_is_refused(field, raw, message):
    """Each refusal says why, in static words that never echo the value."""
    with pytest.raises(InvalidEdit, match=message) as refused:
        edited_value(field, raw, today=TODAY)
    assert raw.strip() == "" or raw not in str(refused.value)


@pytest.mark.parametrize(
    "field",
    ["prefix", "suffix", "gender", "marital_status", "home_address", "death_date"],
)
def test_a_by_hand_field_takes_no_edit(field):
    """Only Automatic Member fields may be edited; a death date is By hand
    since the live API test (#1596), and gender since the gender test
    (#1603)."""
    with pytest.raises(ValueError) as refused:
        edited_value(field, "Value", today=TODAY)
    assert not isinstance(refused.value, InvalidEdit)


def test_the_limit_is_parishsofts_where_it_is_shorter():
    """The PUT schema's limit, unless the census field's own is shorter."""
    assert edit_limit("first_name") == 30
    assert edit_limit("email") == 150
    assert edit_limit("home_phone") == 25
    assert edit_limit("gender") is None and edit_limit("birth_date") is None
    assert edit_limit("prefix") is None


def test_rows_offer_edit_value_with_the_value_to_publish():
    """Open Automatic Member rows only, prefilled with the edit when set,
    else the Family's answer, in the form the Family form shows it."""
    phone = {"normalized": "+13175550199", "display": "(317) 555-0199"}
    plain = shape(row(submitted_value=phone))
    assert plain["edit"] == {
        "value": "(317) 555-0199",
        "type": "text",
        "choices": (),
        "maxlength": 25,
        "edited": False,
    }
    edited = shape(
        row(
            submitted_value=phone,
            admin_value_set=True,
            admin_value={"normalized": "+13175550100", "display": "(317) 555-0100"},
        )
    )
    assert edited["edited"] == "(317) 555-0100"
    assert edited["family_answer"] == "(317) 555-0199"
    assert edited["edit"]["value"] == "(317) 555-0100" and edited["edit"]["edited"]
    born = shape(row(field="birth_date", submitted_value="2000-01-02"))
    assert born["edit"]["type"] == "date" and born["edit"]["maxlength"] is None
    assert shape(row(field="gender"))["edit"]["choices"] == (
        "Male",
        "Female",
        "Unspecified",
    )
    for values in (
        {"execution": "queued"},
        {"execution": "published"},
        {"handling": "manual", "field": "suffix"},
        {"entity_kind": "family", "field": "home_address"},
    ):
        assert shape(row(**values))["edit"] is None, values
    # A Conflict and an ignored change may still be edited.
    assert shape(row(execution="conflict"))["edit"] is not None
    assert shape(row(decision="ignored"))["edit"] is not None


def test_edit_form_parsing():
    """Save value sends the value; Clear edit ignores it; no other form may."""
    proposal, key = (
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    )
    base = f"proposal={proposal}&version=3&request_key={key}&note=+Typo+"
    parsed = _resolution(QueryDict(f"resolve=edit&value=+Pat+&{base}", mutable=True))
    assert parsed["action"] == "edit" and parsed["value"] == " Pat "
    assert parsed["note"] == "Typo"
    cleared = _resolution(
        QueryDict(f"resolve=clear_edit&value=Pat&{base}", mutable=True)
    )
    assert cleared["action"] == "clear_edit" and cleared["value"] is None
    assert "value" not in _resolution(
        QueryDict(f"resolve=approve&{base}", mutable=True)
    )
    for broken in (
        f"resolve=edit&{base}",
        f"resolve=approve&value=Pat&{base}",
        f"resolve=edit&value=a&value=b&{base}",
        "value=Pat",
    ):
        with pytest.raises(ValueError):
            _resolution(QueryDict(broken, mutable=True))


def page(shown):
    """The page as an Administrator sees it, for these shaped rows."""
    from django.template.loader import render_to_string

    row_controls(shown, administrator=True, mutable=True)
    for item in shown:
        item["edit_key"] = "00000000-0000-4000-8000-00000000000e"
    return render_to_string(
        "stewardship/census-changes.html",
        {
            "table": paginate(shown, {}, sorting=SORTING),
            "query": CensusQuery(),
            "query_fields": {},
            "status_counts": {},
            "status_choices": [],
            "route_choices": [],
            "kind_choices": [],
            "administrator": True,
            "action": "/admin/reports/census/",
            "mutable": True,
            "edit_today": "2054-10-06",
            "row_dialogs": row_dialogs(True),
        },
    )


def test_edit_value_is_a_dialog_of_its_own_per_row():
    """Edit value is a row's pencil icon opening its own change-gated form
    and dialog (#879), with the PUT limit, a date capped at today; Clear
    edit is its own icon, only while edited."""
    html = page(
        [
            shape(row(id="plain", field="first_name", submitted_value="Pat")),
            shape(
                row(
                    id="dated",
                    field="birth_date",
                    submitted_value="2000-01-02",
                    admin_value_set=True,
                    admin_value="2000-01-03",
                )
            ),
            shape(row(id="sex", field="gender", submitted_value="Female")),
        ]
    )

    def form(name):
        """The row's Edit value form, its dialog included."""
        at = html.index(f'<form id="census-edit-{name}"')
        return html[at : html.index("</form>", at)]

    plain = form("plain")
    assert 'data-require-change="version" data-require-complete' in plain
    assert "data-in-place" not in plain and 'data-complete-hint="reserve"' in plain
    assert (
        'id="census-edit-dialog-plain"' in plain and "data-confirm-answer-page" in plain
    )
    assert 'data-confirm-form="census-edit-plain"' in plain
    assert 'aria-describedby="edit-complete-plain"' in plain  # Save value
    assert 'name="value" type="text" value="Pat" required maxlength="30"' in plain
    assert '<input type="hidden" name="resolve" value="edit">' in plain
    assert "data-unchanged-hint" in plain and "data-change-ignore" in plain
    assert 'id="edit-error-plain"' in plain
    assert "ParishSoft takes at most 30 characters here." in plain
    assert 'data-confirm-open="census-edit-dialog-plain"' in html
    assert 'aria-label="Edit value of First name for' in html
    assert html.count("resolve=clear_edit") == 1  # the edited row's icon only
    dated = form("dated")
    assert 'type="date" value="2000-01-03" required max="2054-10-06"' in dated
    sex = form("sex")
    assert '<option value="Female" selected>' in sex
    assert '<select id="edit-value-sex" name="value"' in sex
