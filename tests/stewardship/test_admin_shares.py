"""Share options commands: ``campaign shares-preview`` and ``shares-confirm``.

Pure tests: the catalog entries, the documents' member names, the ledger,
the change document's parser, the page's Review post the document becomes
(bound to the page's own formset) and usage errors. The commands against a
real database are in database/test_admin_shares_cli_postgresql.py.
"""

import io
import json
from uuid import uuid4

import pytest

from parishkit.stewardship import admin_cli, admin_shares
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)
from parishkit.stewardship.admin_changes import InvalidChange
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS

from .test_admin_reads import FORBIDDEN

VERSION = "b" * 64
CAMPAIGN = "00000000-0000-4000-8000-000000000001"


def test_the_catalog_entries():
    """Review and Apply: full scope, no prompt or recent sign-in; only the
    Apply records its own event; both take the campaign."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    preview = entries["campaign shares-preview"]
    confirm = entries["campaign shares-confirm"]
    for entry in (preview, confirm):
        assert entry["pr"] == 10 and entry["arguments"] == []
        assert entry["scope"] == "full"
        assert not entry["prompts"] and not entry["fresh_gated"]
    assert not preview["changes_state"] and preview["expected_version"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == list(admin_shares.SharesPreview.field_names())
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert options["--changes"]["required"]
    assert not options["--campaign"]["required"]
    assert confirm["changes_state"] and not confirm["expected_version"]
    assert confirm["audit_event"] == "admin_cmd_campaign_shares_confirm"
    assert "admin_cmd_campaign_shares_confirm" in DESCRIPTIONS
    assert confirm["result_fields"] == list(admin_shares.SharesConfirm.field_names())
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--token"]["required"]
    assert not options["--campaign"]["required"]


def test_the_documents_print_only_allowed_names():
    """The campaign's own options and the review: never a Family's identity."""
    assert set(admin_shares.SharesPreview.field_names()) == {
        "version",
        "campaign_id",
        "changes",
        "options",
        "preview",
    }
    assert set(admin_shares.SharesConfirm.field_names()) == {"created", "request"}
    for model in (admin_shares.SharesPreview, admin_shares.SharesConfirm):
        for name in model.field_names():
            assert FORBIDDEN.search(name) is None, name


def test_the_ledger_covers_the_page():
    """The page is covered by its read, Review and Apply; nothing is owed."""
    parity = LEDGER["share_settings"]
    assert parity.kind == "command" and parity.owed is None
    assert parity.detail == (
        "campaign shares",
        "campaign shares-preview",
        "campaign shares-confirm",
    )


def test_the_parser_canonicalizes_ids_and_defaults_delete():
    """Ids come back in their canonical spelling; ``delete`` defaults to none."""
    identifier = uuid4()
    options, delete = admin_shares.parse_changes(
        json.dumps({"options": [{"id": str(identifier).upper(), "name": "Gift"}]})
    )
    assert options == [{"id": str(identifier), "name": "Gift"}]
    assert delete == []


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        "{}",
        json.dumps({"options": {}}),
        json.dumps({"options": [], "delete": "x"}),
        json.dumps({"options": [], "extra": []}),
    ],
)
def test_the_parser_refuses_a_document_of_another_shape(text):
    """Anything but an options list and an optional delete list is invalid."""
    with pytest.raises(ValueError):
        admin_shares.parse_changes(text)


@pytest.mark.parametrize(
    ("document", "field"),
    [
        ({"options": ["Gift"]}, "options.0"),
        ({"options": [{"label": "Gift", "name": "Gift"}]}, "options.0.label"),
        ({"options": [{"free_text": True}]}, "options.0.name"),
        ({"options": [{"name": 7}]}, "options.0.name"),
        ({"options": [{"name": "Gift", "free_text": "yes"}]}, "options.0.free_text"),
        ({"options": [{"name": "Gift", "short_name": 7}]}, "options.0.short_name"),
        ({"options": [{"name": "Gift", "id": "nope"}]}, "options.0.id"),
        ({"options": [{"name": "Gift", "id": 7}]}, "options.0.id"),
        ({"options": [], "delete": ["nope"]}, "delete"),
        ({"options": [{"name": "Gift"}] * 102}, "options"),
    ],
)
def test_the_parser_refuses_a_bad_member_at_its_field(document, field):
    """Each bad member is an ``invalid`` problem at its own place."""
    with pytest.raises(InvalidChange) as caught:
        admin_shares.parse_changes(json.dumps(document))
    assert field in {problem["field"] for problem in caught.value.fields}
    assert {problem["code"] for problem in caught.value.fields} == {"invalid"}


def test_the_parser_bounds_the_document():
    """A document over the limit is refused before it is parsed."""
    with pytest.raises(ValueError):
        admin_shares.parse_changes(" " * (admin_shares.CHANGES_LIMIT + 1))


def _bound(previous, document):
    """The page's formset bound to the document's Review post."""
    options, delete = admin_shares.parse_changes(json.dumps(document))
    formset = ShareOptions(
        admin_shares.formset_data(previous, options, delete, VERSION),
        prefix="options",
        previous=previous,
    )
    return formset, len(options)


def _kept(previous):
    """The document that keeps every saved option as it is."""
    return [
        {
            "id": row["id"],
            "name": row["label"],
            "short_name": row.get("short_label", ""),
            "free_text": row["free_text"],
        }
        for row in previous
    ]


def test_the_post_keeps_reorders_adds_and_deletes_as_the_page():
    """The formset saves the document's order, new options and deletions."""
    previous = default_share_options()[:3]
    first, second, third = _kept(previous)
    formset, _count = _bound(
        previous,
        {
            "options": [
                third,
                {"name": "A new gift", "free_text": True},
                first | {"name": "Renamed"},
            ],
            "delete": [second["id"]],
        },
    )
    assert formset.is_valid(), formset.errors
    values = formset.values()
    assert [row["label"] for row in values] == [
        third["name"],
        "A new gift",
        "Renamed",
    ]
    assert values[0]["id"] == third["id"] and values[2]["id"] == first["id"]
    assert values[1]["id"] not in {row["id"] for row in previous}
    assert values[1]["free_text"] is True


def test_an_unchanged_document_binds_to_the_saved_list():
    """Keeping every option as it is gives the saved list back exactly."""
    previous = default_share_options()
    formset, _count = _bound(previous, {"options": _kept(previous)})
    assert formset.is_valid(), formset.errors
    assert formset.values() == previous


@pytest.mark.parametrize(
    "change",
    [
        # A saved option in neither list: the page's missing-rows rule.
        lambda kept: {"options": kept[1:]},
        # An id given twice.
        lambda kept: {"options": [*kept, kept[0]]},
        # An id kept and deleted.
        lambda kept: {"options": kept, "delete": [kept[0]["id"]]},
        # An id the campaign never had.
        lambda kept: {"options": [*kept, {"id": str(uuid4()), "name": "Gift"}]},
    ],
)
def test_the_pages_identity_rules_apply_at_options(change):
    """The formset's own refusals are problems at ``options``."""
    previous = default_share_options()[:3]
    formset, count = _bound(previous, change(_kept(previous)))
    assert not formset.is_valid()
    problems = admin_shares.field_errors(formset, count)
    assert problems and {problem["field"] for problem in problems} == {"options"}


def test_a_refused_label_is_a_problem_at_its_options_name():
    """The page's label check names the document's option by its position."""
    previous = default_share_options()[:2]
    kept = _kept(previous)
    formset, count = _bound(
        previous, {"options": [kept[0], kept[1] | {"name": "{{ family_name }}"}]}
    )
    assert not formset.is_valid()
    problems = admin_shares.field_errors(formset, count)
    assert [problem["field"] for problem in problems] == ["options.1.name"]
    assert "placeholders" in problems[0]["message"]


@pytest.mark.parametrize(
    ("short", "words", "rows"),
    [
        # Another option's short label in another case (#1569): both
        # options that have it are named, as the page marks both.
        ("bank CHECK", "Another option already has this short label.", [0, 1]),
        ("Much too long a name", "at most 12 characters", [1]),
        ("{{ year }}", "plain text", [1]),
    ],
)
def test_a_refused_short_label_is_a_problem_at_its_short_name(short, words, rows):
    """The page's short label checks name the option's ``short_name``."""
    previous = default_share_options()[:2]
    kept = _kept(previous)
    formset, count = _bound(
        previous, {"options": [kept[0], kept[1] | {"short_name": short}]}
    )
    assert not formset.is_valid()
    problems = admin_shares.field_errors(formset, count)
    assert [problem["field"] for problem in problems] == [
        f"options.{row}.short_name" for row in rows
    ]
    assert all(words in problem["message"] for problem in problems)


def test_the_preview_options_carry_the_short_label():
    """A kept option's short label survives the round trip; leaving it out
    clears it, as an empty page field does."""
    previous = default_share_options()[:2]
    kept = _kept(previous)
    del kept[1]["short_name"]
    formset, _count = _bound(previous, {"options": kept})
    assert formset.is_valid(), formset.errors
    values = formset.values()
    assert values[0]["short_label"] == "Bank check"
    assert "short_label" not in values[1]


@pytest.mark.parametrize(
    "argv",
    [
        ["campaign", "shares-preview", "--expected-version", VERSION],
        ["campaign", "shares-preview", "--changes", "-"],
        [
            "campaign",
            "shares-preview",
            "--changes",
            "-",
            "--expected-version",
            "nope",
        ],
        [
            "campaign",
            "shares-preview",
            "--changes",
            "-",
            "--expected-version",
            VERSION,
            "--campaign",
            "not-a-uuid",
        ],
        ["campaign", "shares-confirm"],
        ["campaign", "shares-confirm", "--token", "-", "extra"],
        ["campaign", "shares-confirm", "--token", "-", "--campaign", "nope"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The preview needs its document and a version; the confirm needs its
    token; ``--campaign`` is a UUID."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def test_a_campaign_given_is_accepted_by_the_parser():
    """``--campaign`` takes the campaign's id, as ``campaign shares`` does."""
    parser = admin_cli.build_parser()
    args = parser.parse_args(
        [
            "campaign",
            "shares-confirm",
            "--token",
            "-",
            "--campaign",
            CAMPAIGN,
            "--config",
            "web.yaml",
            "--session-stdin",
        ]
    )
    assert str(args.campaign) == CAMPAIGN
