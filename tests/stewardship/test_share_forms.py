"""Share-option forms keep IDs stable and treat management data as untrusted."""

from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.share_forms import (
    DUPLICATE_SHORT_LABEL,
    SHORT_LABEL_RULE,
    STRUCTURE_FIELDS,
    ShareOptions,
    default_share_options,
    share_action,
)
from parishkit.stewardship.campaigns.configuration import short_label_key


def data_for(previous, *, extra=None):
    """Post genuine Django formset names without assuming hidden values are trusted."""
    rows = [*previous, *([extra] if extra else [])]
    data = {
        "action": "preview",
        "base_digest": "a" * 64,
        "options-TOTAL_FORMS": str(len(rows)),
        "options-INITIAL_FORMS": str(len(previous)),
    }
    for index, row in enumerate(rows):
        for name, value in row.items():
            if value is False:
                continue
            data[f"options-{index}-{name}"] = "on" if value is True else str(value)
    return data


def form_for(previous, data=None):
    """Use actual immutable saved identities as the formset's authoritative input."""
    return ShareOptions(
        data_for(previous) if data is None else data,
        prefix="options",
        previous=previous,
    )


def test_default_labels_have_independent_campaign_identities():
    """The default options include Other text but no reused cross-campaign IDs."""
    first, second = default_share_options(), default_share_options()
    assert len(first) == 7
    assert {row["id"] for row in first}.isdisjoint(row["id"] for row in second)
    form = form_for(first)
    assert form.is_valid(), form.errors
    assert form.values() == first
    assert sum(row["free_text"] for row in first) == 1


def test_edit_order_delete_and_add_preserve_surviving_ids():
    """Explicit order/deletion controls do not reassign an old option's identity."""
    previous = default_share_options()[:3]
    data = data_for(
        previous, extra={"label": "Other gift", "free_text": True, "ORDER": 1}
    )
    data.update(
        {
            "options-0-ORDER": "4",
            "options-1-ORDER": "2",
            "options-2-DELETE": "on",
            "options-1-label": "Updated label",
        }
    )
    form = form_for(previous, data)
    assert form.is_valid(), (form.errors, form.non_form_errors())
    values = form.values()
    assert [row["label"] for row in values] == [
        "Other gift",
        "Updated label",
        previous[0]["label"],
    ]
    assert values[0]["id"] not in {row["id"] for row in previous}
    assert values[1]["id"] == previous[1]["id"] and values[2]["id"] == previous[0]["id"]


@pytest.mark.parametrize(
    "label",
    [
        "{{ family_code }}",
        "{{ unknown }}",
        "{% logic %}",
        "{{ parish_name",
        "bad\x00label",
        "bad\tlabel",
        "bad\x85label",
        "bad\x7flabel",
        "x" * 1025,
    ],
)
def test_invalid_or_private_placeholders_are_rejected(label):
    """Share labels cannot become another channel for private Family credentials."""
    form = form_for([], data_for([], extra={"label": label}))
    assert not form.is_valid()
    with pytest.raises(ValueError):
        form.values()


@pytest.mark.parametrize(
    "change",
    [
        {"options-INITIAL_FORMS": "0"},
        {"options-0-id": ""},
        {"options-0-id": str(uuid4())},
        {"options-TOTAL_FORMS": "0"},
        {"options-TOTAL_FORMS": "99999"},
        {"options-0-id": "malformed"},
    ],
)
def test_forged_management_and_identity_fields_are_not_admitted(change):
    """An omitted saved row is not an implicit delete, regardless of browser counts."""
    previous = default_share_options()[:1]
    form = form_for(previous, data_for(previous) | change)
    assert not form.is_valid()
    assert len(form.forms) <= 101


def test_duplicate_identity_and_explicit_all_delete():
    """Deleting all is explicit; copying a surviving ID onto another row is invalid."""
    previous = default_share_options()[:2]
    duplicate = form_for(
        previous, data_for(previous) | {"options-1-id": previous[0]["id"]}
    )
    assert not duplicate.is_valid()
    deleted = form_for(
        previous,
        data_for(previous) | {"options-0-DELETE": "on", "options-1-DELETE": "on"},
    )
    assert deleted.is_valid(), deleted.errors
    assert deleted.values() == []
    blank = form_for([], data_for([]))
    assert blank.is_valid() and blank.values() == []


@pytest.mark.parametrize(
    "raw",
    [
        "action=preview&options-TOTAL_FORMS=01",
        "action=preview&options-TOTAL_FORMS=-1",
        "action=preview&options-TOTAL_FORMS=102",
        "action=preview&options-TOTAL_FORMS=x",
        "action=preview&options-TOTAL_FORMS=0&options-0-label=ignored",
        "action=preview&options-TOTAL_FORMS=1&options-0-label=a&options-0-label=b",
        "action=confirm&preview=x&options-TOTAL_FORMS=1",
        "action=preview",
    ],
)
def test_action_parser_rejects_hidden_out_of_range_and_duplicate_values(raw):
    """Django's tolerant formset binding does not silently drop stray posted values."""
    with pytest.raises(ValueError):
        share_action(QueryDict(raw))


def test_action_parser_preserves_bounded_preview_and_confirm():
    """A normal post is admitted without demanding irrelevant confirmation fields."""
    assert (
        share_action(
            QueryDict("action=preview&options-TOTAL_FORMS=0&options-INITIAL_FORMS=0")
        )
        == "preview"
    )
    assert share_action(QueryDict("action=confirm&preview=opaque")) == "confirm"


def test_a_direct_save_posts_the_rows_with_its_request_key():
    """Inside the campaign wizard Save and continue posts ``save`` (#1404 PR 4).

    It sends the same rows a Review sends, plus the page's request key, and
    is held to the same bounded fields.
    """
    rows = "options-TOTAL_FORMS=1&options-INITIAL_FORMS=1&options-0-label=A"
    saved = f"action=save&base_digest=x&request_key=k&{rows}"
    assert share_action(QueryDict(saved)) == "save"
    with pytest.raises(ValueError):
        share_action(QueryDict(f"{saved}&options-1-label=B"))
    with pytest.raises(ValueError):
        share_action(QueryDict(f"action=preview&request_key=k&{rows}"))


def test_default_short_labels_fit_and_are_distinct():
    """Every default option has a short label within the limit (#1569)."""
    from parishkit.stewardship.campaigns.configuration import SHORT_LABEL_LIMIT

    shorts = [row["short_label"] for row in default_share_options()]
    assert all(1 <= len(short) <= SHORT_LABEL_LIMIT for short in shorts)
    assert len({short_label_key(short) for short in shorts}) == len(shorts)


def test_a_blank_short_label_is_not_stored():
    """A blank short label leaves the key out, as before short labels."""
    form = form_for([], data_for([], extra={"label": "By check", "short_label": " "}))
    assert form.is_valid(), form.errors
    assert form.values()[0].keys() == {"id", "label", "free_text"}


# Short labels the server reads as one (short_label_key), and ones it
# keeps apart; browser/test_share_options_dialog.py runs the same pairs
# through the page's live check.
SAME_SHORT_LABELS = [
    ("Bank check", "BANK check"),
    ("Online", "  online\t"),
    ("Straße", "STRASSE"),
    ("Ελλάς", "ΕΛΛΆΣ"),
    ("\u00a0IRA", "ira\u3000"),
]
OTHER_SHORT_LABELS = [("Bank check", "Bank  check"), ("IRA", "IRAs"), ("e", "é")]


@pytest.mark.parametrize(("first", "second"), SAME_SHORT_LABELS)
def test_short_labels_alike_but_for_case_and_spaces_are_one(first, second):
    """Spaces around a short label and its case do not tell it apart; upper
    then lower case is the mapping the page script applies too."""
    assert short_label_key(first) == short_label_key(second)


@pytest.mark.parametrize(("first", "second"), OTHER_SHORT_LABELS)
def test_short_labels_differing_inside_stay_apart(first, second):
    """Inner spaces, letters and accents still tell short labels apart."""
    assert short_label_key(first) != short_label_key(second)


def test_a_repeated_short_label_is_refused_at_every_field_with_it():
    """Two kept options cannot share a short label, even in another case;
    both are marked, as the page's live check marks them, and a deleted
    option's no longer counts."""
    previous = default_share_options()[:3]
    data = data_for(previous) | {"options-1-short_label": "BANK check"}
    form = form_for(previous, data)
    assert not form.is_valid()
    for row in (0, 1):
        assert [
            error.code for error in form.forms[row].errors.as_data()["short_label"]
        ] == ["duplicate_short_label"]
        assert form.forms[row].errors["short_label"] == [
            "Another option already has this short label."
        ]
    assert form.forms[2].errors == {}
    deleted = form_for(previous, data | {"options-0-DELETE": "on"})
    assert deleted.is_valid(), deleted.errors
    assert [row["short_label"] for row in deleted.values()] == [
        "BANK check",
        "New e-giving",
    ]


@pytest.mark.parametrize(
    ("short", "code"),
    [("A" * 13, "max_length"), ("{{ year }}", "invalid_short_label")],
)
def test_a_long_or_templated_short_label_is_refused(short, code):
    """At most 12 characters of plain text, refused with a closed code."""
    form = form_for([], data_for([], extra={"label": "Gift", "short_label": short}))
    assert not form.is_valid()
    assert [error.code for error in form.forms[0].errors.as_data()["short_label"]] == [
        code
    ]


def test_the_short_label_draws_its_rule_group_and_reserved_line():
    """The short label is one data-distinct group with the server's words,
    carries its format rule, and keeps an empty reserved error line, so the
    page's message coming or going moves nothing (#1569)."""
    previous = default_share_options()[:1]
    form = form_for(previous).forms[0]
    attrs = form.fields["short_label"].widget.attrs
    assert attrs["data-distinct"] == "short-label"
    assert attrs["data-distinct-message"] == DUPLICATE_SHORT_LABEL
    assert attrs["data-field-rule"] == SHORT_LABEL_RULE.name
    assert attrs["maxlength"] == "12"
    html = str(form["short_label"].as_field_group())
    line = '<ul class="errorlist" id="id_options-0-short_label_error" hidden>'
    assert f"{line}<li></li></ul>" in html
    assert 'class="field-error-line" role="status"' in html


def test_the_short_label_greys_with_its_rows_delete_box():
    """The short label is disabled in place while Delete is ticked (#563)."""
    previous = default_share_options()[:1]
    form = form_for(previous)
    field = form.forms[0].fields["short_label"]
    assert field.widget.attrs["data-enabled-when"] == "options-0-DELETE!=on"
    deleted = form_for(previous, data_for(previous) | {"options-0-DELETE": "on"})
    assert deleted.forms[0].fields["short_label"].disabled


def test_a_locked_campaigns_form_changes_only_short_labels():
    """``short_only`` (#1569) disables every field but the short label and
    draws no blank row: posted labels, order or Delete are ignored, so only
    short labels change, and an added row is refused."""
    previous = default_share_options()[:2]
    form = ShareOptions(prefix="options", previous=previous, short_only=True)
    assert len(form.forms) == 2
    fields = form.forms[0].fields
    assert all(fields[name].disabled for name in ("label", "free_text", "ORDER"))
    assert fields["DELETE"].disabled and not fields["short_label"].disabled
    assert "data-enabled-when" not in fields["short_label"].widget.attrs
    # Each locked field gives the reason in its tip, as Campaign settings
    # does (ui-conventions); the open short label keeps its own help.
    reason = "This campaign is locked, so only the short labels can change."
    for name in STRUCTURE_FIELDS:
        assert str(fields[name].tip) == reason
    assert not hasattr(fields["short_label"], "tip")
    html = str(form.forms[0])
    assert html.count(reason) == len(STRUCTURE_FIELDS)
    data = data_for(previous) | {
        "options-0-short_label": "Check",
        "options-1-label": "Forged",
        "options-1-ORDER": "1",
        "options-0-DELETE": "on",
    }
    bound = ShareOptions(data, prefix="options", previous=previous, short_only=True)
    assert bound.is_valid(), bound.errors
    assert bound.values() == [previous[0] | {"short_label": "Check"}, previous[1]]
    extra = data_for(previous, extra={"label": "New", "short_label": "New"})
    added = ShareOptions(extra, prefix="options", previous=previous, short_only=True)
    assert not added.is_valid()
    with pytest.raises(ValueError):
        added.values()
