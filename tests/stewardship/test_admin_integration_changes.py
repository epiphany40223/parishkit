"""``integration preview`` and ``integration confirm``: pure tests.

The change document's shape, the form post it becomes, the prompt, the
catalog entries, the ledger and usage errors. The commands against a real
database are in database/test_admin_integration_changes_cli_postgresql.py.
"""

import io
import json

import pytest
from django.core import signing

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts.integration_views import SALT
from parishkit.stewardship.admin_changes import InvalidChange
from parishkit.stewardship.admin_integration_changes import (
    CHANGES_LIMIT,
    IntegrationConfirm,
    IntegrationPreview,
    form_data,
    parse_schedule,
    parse_settings,
    prompt_lines,
    schedule_errors,
    schedule_review,
    schedule_rows,
    setting_names,
)
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS


def document(settings):
    """A change document with ``settings``."""
    return json.dumps({"settings": settings})


@pytest.mark.parametrize(
    ("target", "names"),
    [
        ("parishsoft", ["organization_id"]),
        ("google_workspace", ["delegated_email"]),
        ("email", ["sender", "sender_name", "reply_to"]),
        ("slack", ["channel_id"]),
        ("backup", ["target"]),
    ],
)
def test_each_target_takes_the_pages_fields(target, names):
    """The page's fields, never its version, schedule rows or a key."""
    assert setting_names(target) == names


def test_a_change_document_names_settings():
    """Any non-empty subset; the From name may be null; numbers are allowed."""
    assert parse_settings(
        document({"sender": "a@example.org", "sender_name": None}), "email"
    ) == {"sender": "a@example.org", "sender_name": None}
    assert parse_settings(document({"organization_id": 7}), "parishsoft") == {
        "organization_id": 7
    }


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        document({}),
        json.dumps({"settings": []}),
        json.dumps({"settings": {"sender": "a@example.org"}, "extra": 1}),
        document({"sender": True}),
        document({"sender": ["a@example.org"]}),
        "x" * (CHANGES_LIMIT + 1),
    ],
)
def test_a_malformed_change_document_is_invalid(text):
    """Anything but ``{"settings": {name: string, number or null}}``."""
    with pytest.raises(ValueError):
        parse_settings(text, "email")


def test_unknown_settings_and_nulls_are_named():
    """Another target's field, a schedule key or a key; a required null."""
    with pytest.raises(InvalidChange) as raised:
        parse_settings(
            document(
                {
                    "sender": None,
                    "channel_id": "C1",
                    "refresh_rules": {},
                    "candidate": "secret",
                }
            ),
            "email",
        )
    assert [(p["field"], p["code"]) for p in raised.value.fields] == [
        ("settings.candidate", "invalid"),
        ("settings.channel_id", "invalid"),
        ("settings.refresh_rules", "invalid"),
        ("settings.sender", "required"),
    ]
    assert "secret" not in json.dumps(raised.value.fields)


def test_the_form_post_keeps_what_the_document_omits():
    """Applied values first, the document's over them; null is blank."""
    stored = {"sender": "a@example.org", "sender_name": "Office", "reply_to": "r@x.org"}
    data = form_data(
        stored,
        setting_names("email"),
        {"sender_name": None, "reply_to": "s@x.org"},
        "f" * 64,
    )
    assert data == {
        "sender": "a@example.org",
        "sender_name": "",
        "reply_to": "s@x.org",
        "base_digest": "f" * 64,
    }
    # No schedule rows are posted, so the page keeps the stored schedule.
    assert form_data({"organization_id": "12"}, ["organization_id"], {}, "0") == {
        "organization_id": "12",
        "base_digest": "0",
    }


def test_the_prompt_names_changed_settings_never_values():
    """Only the settings the reviewed patch changes against the applied ones
    (never an unchanged stored schedule key), or the removal; another
    target's token is refused before the prompt."""
    applied = {"sender": "a@example.org", "reply_to": "r@x", "sender_name": "O"}
    update = [
        {
            "operation": "update",
            "section": "integrations",
            "id": "x",
            "values": {"settings": {**applied, "reply_to": "s@x", "sender_name": None}},
        }
    ]
    update[0]["values"]["settings"].pop("sender_name")
    token = signing.dumps({"patch": update}, salt=SALT + "email")
    [line] = prompt_lines("email", token, applied)
    assert line == (
        "This changes the Outgoing email addresses settings: reply_to, sender_name."
    )
    assert "a@example.org" not in line and "s@x" not in line
    schedule = {"organization_id": "1", "refresh_rules": {"rules": []}}
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": "p",
            "values": {"settings": {**schedule, "organization_id": "2"}},
        }
    ]
    token = signing.dumps({"patch": patch}, salt=SALT + "parishsoft")
    assert prompt_lines("parishsoft", token, schedule) == [
        "This changes the ParishSoft settings: organization_id."
    ]
    # Nothing differs any more (applied since): the plain wording.
    assert prompt_lines("parishsoft", token, patch[0]["values"]["settings"]) == [
        "This applies the reviewed ParishSoft settings."
    ]
    removal = [{"operation": "remove", "section": "integrations", "id": "x"}]
    token = signing.dumps({"patch": removal}, salt=SALT + "slack")
    assert prompt_lines("slack", token, {}) == ["This removes Slack notifications."]
    with pytest.raises(ValueError):
        prompt_lines("email", token, {})


def test_the_catalog_entries():
    """Full scope, as the page's posts; Apply is fresh-gated and prompts.

    The preview records nothing, as the page's review; the confirmation
    records its own described event.
    """
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    preview, confirm = entries["integration preview"], entries["integration confirm"]
    for entry in (preview, confirm):
        assert entry["scope"] == "full" and entry["pr"] == 10
    assert not preview["changes_state"] and preview["expected_version"]
    assert not preview["prompts"] and not preview["fresh_gated"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == list(IntegrationPreview.field_names())
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert {"--changes", "--remove"} <= set(options)
    assert confirm["changes_state"] and confirm["prompts"] and confirm["fresh_gated"]
    assert confirm["audit_event"] == "admin_cmd_integration_confirm"
    assert "admin_cmd_integration_confirm" in DESCRIPTIONS
    assert confirm["result_fields"] == list(IntegrationConfirm.field_names())
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--token"]["required"]


def test_the_ledger_covers_the_settings_pages_save_and_remove():
    """The settings page's read, review and Apply; key steps still owed."""
    entry = LEDGER["integration_settings"]
    assert entry.kind == "command" and entry.owed == "PR 10"
    assert entry.detail == (
        "integration show",
        "integration preview",
        "integration confirm",
    )


VERSION = ["--expected-version", "a" * 64]


@pytest.mark.parametrize(
    "argv",
    [
        ["integration", "preview", "email", *VERSION],
        ["integration", "preview", "email", *VERSION, "--changes", "{}", "--remove"],
        ["integration", "preview", "email", "--changes", "{}"],
        ["integration", "preview", "backup_key", *VERSION, "--remove"],
        ["integration", "preview", *VERSION, "--remove"],
        ["integration", "confirm", "email"],
        ["integration", "confirm", "--token", "x"],
        ["integration", "confirm", "email", "--token", "-"],
        [
            "integration",
            "preview",
            "parishsoft",
            *VERSION,
            "--refresh-schedule",
            "{}",
            "--remove",
        ],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """Neither or both of a change and a removal, no version, an unknown
    target, no token, a token read from standard input without --yes, or a
    refresh schedule with a removal."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


SCHEDULE = {
    "rules": [
        {"kind": "full", "at": "02:00"},
        {"kind": "full", "every": 120, "from": "08:00", "to": "18:00"},
    ],
    "skips": [{"from": "12:00", "to": "13:00"}],
    "skip_around_family_emails": True,
}


def test_a_refresh_schedule_reads_as_json_or_yaml():
    """The stored ``refresh_rules`` shape; skips default to none and a switch
    left out stays absent (the current one is kept)."""
    assert parse_schedule(json.dumps(SCHEDULE)) == SCHEDULE
    text = 'rules:\n  - {kind: full, at: "02:00"}\n'
    assert parse_schedule(text) == {
        "rules": [{"kind": "full", "at": "02:00"}],
        "skips": [],
    }


@pytest.mark.parametrize(
    "text",
    ["[]", "rules: [", "a: 1\na: 2", "x" * (CHANGES_LIMIT + 1), "&a [*a]"],
)
def test_a_malformed_refresh_schedule_is_invalid(text):
    """Not an object, not YAML, a duplicate key, too long or self-referring."""
    with pytest.raises(ValueError):
        parse_schedule(text)


def test_refresh_schedule_shape_problems_are_placed_never_echoed():
    """Another member, a row of another shape, an unquoted YAML time (read as
    a number), a non-integer interval and a non-boolean switch."""
    text = (
        "secret_member: 1\n"
        "rules:\n"
        "  - {kind: full, at: 12:30}\n"
        "  - {kind: full, at: '02:00', every: 60}\n"
        "  - {kind: full, every: '60', from: '00:00', to: '01:00'}\n"
        "skips: {at: '01:00'}\n"
        "skip_around_family_emails: 'yes'\n"
    )
    with pytest.raises(InvalidChange) as raised:
        parse_schedule(text)
    assert [p["field"] for p in raised.value.fields] == [
        "refresh_schedule",
        "refresh_schedule.rules[0]",
        "refresh_schedule.rules[1]",
        "refresh_schedule.rules[2]",
        "refresh_schedule.skips",
        "refresh_schedule.skip_around_family_emails",
    ]
    assert "secret_member" not in json.dumps(raised.value.fields)
    with pytest.raises(InvalidChange) as raised:
        parse_schedule("{}")
    assert raised.value.fields[0]["field"] == "refresh_schedule.rules"


def editor(document, stored=None):
    """The page's editor reading ``document`` posted as the command posts it."""
    from parishkit.stewardship.accounts.refresh_schedule_forms import ScheduleEditor

    stored = {} if stored is None else stored
    rows = schedule_rows(
        parse_schedule(json.dumps(document)), ScheduleEditor(stored=stored)
    )
    return ScheduleEditor(rows, stored=stored)


def test_the_page_editor_reads_the_posted_rows_back():
    """The command posts the page's rows: the editor reads the same rules,
    derives the stored settings and reports a change."""
    from parishkit.stewardship.source.refresh_rules import stored_settings

    read = editor(SCHEDULE)
    assert read.posted and read.changed and not read.blocking
    assert read.document() == SCHEDULE
    assert read.settings() == stored_settings(SCHEDULE) | {
        "refresh_schedule_mode": "advanced"
    }


def test_a_switch_left_out_keeps_the_shown_one_and_the_mode_is_the_pages():
    """No switch keeps the page's (off for an existing schedule). The mode is
    the one the page opens in: Advanced for an older schedule Basic cannot
    show (here with quick updates), else the last saved, Basic when none."""
    rules = {"rules": [{"kind": "full", "at": "02:00"}]}
    read = editor(rules)
    assert read.document()["skip_around_family_emails"] is False
    assert read.settings()["refresh_schedule_mode"] == "advanced"
    # Business hours, a Basic choice, saved with no mode.
    business = [
        {"kind": "full", "at": "02:00"},
        {"kind": "full", "every": 120, "from": "08:00", "to": "18:00"},
    ]
    shown = {"rules": business, "skips": []}
    stored = {"refresh_rules": shown | {"skip_around_family_emails": True}}
    read = editor(rules, stored)
    assert read.document()["skip_around_family_emails"] is True
    assert read.settings()["refresh_schedule_mode"] == "basic"
    stored["refresh_schedule_mode"] = "advanced"
    assert editor(rules, stored).settings()["refresh_schedule_mode"] == "advanced"


def test_a_null_switch_is_refused():
    """Present means true or false; a null would silently turn it off."""
    with pytest.raises(InvalidChange) as raised:
        parse_schedule('{"rules": [], "skip_around_family_emails": null}')
    assert [p["field"] for p in raised.value.fields] == [
        "refresh_schedule.skip_around_family_emails"
    ]


SENTINEL = "SECRETXYZ"


@pytest.mark.parametrize(
    "row",
    [
        {"kind": SENTINEL, "at": "02:00"},
        {"kind": "full", "at": SENTINEL},
        {"kind": "full", "at": "24:00"},
        {"kind": "full", "at": "2pm"},
        {"kind": "full", "every": 7777, "from": "00:00", "to": "01:00"},
        {"kind": "full", "every": 60.0, "from": "00:00", "to": "01:00"},
        {"kind": "full", "every": True, "from": "00:00", "to": "01:00"},
        {"kind": "full", "every": 60, "from": "00:00", "to": SENTINEL},
    ],
)
def test_values_the_page_form_would_quote_are_refused_in_fixed_words(row):
    """A kind, a time or an interval the page's form would quote back in its
    own message is refused first, in words that never echo the input."""
    with pytest.raises(InvalidChange) as raised:
        parse_schedule(json.dumps({"rules": [row]}))
    [problem] = raised.value.fields
    assert problem["field"] == "refresh_schedule.rules[0]"
    for value in (SENTINEL, "7777", "24:00", "2pm", "60.0", "True"):
        assert value not in problem["message"]


def test_new_quick_rules_are_refused():
    """Only a quick rule the shown schedule already has may be posted back
    (#1122); the page never offers adding one."""
    from parishkit.stewardship.accounts.refresh_schedule_forms import ScheduleEditor
    from parishkit.stewardship.admin_integration_changes import quick_problems

    shown = ScheduleEditor(stored={})
    kept = {"rules": shown.shown["rules"]}
    assert quick_problems(parse_schedule(json.dumps(kept)), shown) == []
    added = {"rules": [*kept["rules"], {"kind": "quick", "at": "12:00"}]}
    problems = quick_problems(parse_schedule(json.dumps(added)), shown)
    assert [p["field"] for p in problems] == [
        f"refresh_schedule.rules[{len(kept['rules'])}]"
    ]


def test_the_page_editors_problems_come_back_at_each_row():
    """A time off the quarter hour and the cap, each
    with the page's words; an unchanged schedule never blocks."""
    close = {
        "rules": [{"kind": "full", "at": "02:00"}, {"kind": "full", "at": "02:10"}]
    }
    problems = schedule_errors(editor(close))
    assert {p["field"] for p in problems} == {"refresh_schedule.rules[1]"}
    assert any(":00, :15, :30 or :45" in p["message"] for p in problems)
    many = {"rules": [{"kind": "full", "at": "02:00"}] * 65}
    messages = [p["message"] for p in schedule_errors(editor(many))]
    assert "A schedule has at most 64 rules." in messages
    shown = editor({"rules": [{"kind": "full", "at": "02:00"}]}).shown
    assert schedule_errors(editor(shown)) == []


def test_the_schedule_review_shows_before_after_and_differences():
    """The schedule as the page showed it, as posted, and the review's words."""
    read = editor(SCHEDULE)
    review = schedule_review(read, {}, "America/New_York")
    assert review["timezone"] == "America/New_York"
    assert review["before"] == read.shown and review["after"] == SCHEDULE
    assert any("Full refreshes added" in item for item in review["differences"])
    assert schedule_review(editor(read.shown), {}, "UTC") is None
    assert schedule_review(None, {}, "UTC") is None
