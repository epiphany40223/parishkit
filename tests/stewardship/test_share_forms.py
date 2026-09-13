"""Share-option forms keep IDs stable and treat management data as untrusted."""

from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
    share_action,
)


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
