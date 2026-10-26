"""Typed public wizard steps neither accept credentials nor activate configuration."""

from copy import deepcopy

import pytest

from parishkit.stewardship.accounts.setup_forms import (
    FORMS,
    form_values,
    initial_values,
    validate_values,
)

VALUES = {
    "parish": {
        "name": "Sample Parish",
        "website": "https://parish.example.org/",
        "timezone": "America/New_York",
        "phone": "+12125551234",
        "online_giving_url": "",
    },
    "access": {
        "staff_addresses": ["staff@example.net"],
        "ministry_addresses": ["chair@example.net"],
        "admin_addresses": ["another@example.net"],
    },
    "mail": {
        "delegated_email": "mail@example.org",
        "sender": "mail@example.org",
        "sender_name": "St. Example Stewardship",
        "reply_to": "office@example.org",
    },
    "slack": {"enabled": False, "channel_id": ""},
    "testing": {"testing_recipient": "testing@example.org"},
}


@pytest.mark.parametrize("step", VALUES)
def test_public_form_round_trip_is_detached_and_canonical(step):
    """A validated draft contains exactly the same closed data on later visits."""
    values = deepcopy(VALUES[step])
    form = FORMS[step](initial_values(step, values))
    assert form.is_valid(), form.errors
    assert form_values(form) == values
    restored = validate_values(step, values)
    assert restored == values and restored is not values


@pytest.mark.parametrize(
    "step,fields",
    [
        ("parish", {"website": "https://user:password@parish.example.org"}),
        ("parish", {"website": "https://parish.example.org?token=secret"}),
        ("parish", {"timezone": "Mars/Nowhere"}),
        ("parish", {"phone": "123"}),
        # A step saved before #993 that granted a domain must be saved again.
        ("access", {"staff_domains": ["example.org"], "ministry_domains": []}),
        ("access", {"staff_domains": [], "ministry_domains": ["example.org"]}),
        ("access", {"staff_domains": "", "ministry_domains": []}),
        ("access", {"admin_addresses": ["not-an-address"]}),
        ("access", {"staff_addresses": "staff@example.org"}),
        ("mail", {"sender": "not-an-address"}),
        ("slack", {"enabled": True}),
        ("slack", {"channel_id": "C123"}),
        ("testing", {"testing_recipient": "no-at"}),
    ],
)
def test_invalid_or_inconsistent_public_settings_are_not_staged(step, fields):
    """Validation happens before SQL, with no submitted value in the exception."""
    with pytest.raises(ValueError):
        validate_values(step, VALUES[step] | fields)


@pytest.mark.parametrize("step", VALUES)
def test_extra_private_or_authority_fields_are_never_ignored(step):
    """A public JSON payload cannot carry a hidden credential to the database."""
    for field in ("api_key", "candidate", "credential_fingerprint", "base_digest"):
        with pytest.raises(ValueError):
            validate_values(step, VALUES[step] | {field: "synthetic-private"})


def test_identity_input_normalizes_case_duplicates_and_empty_lines():
    """Access lists are additive; repeated spelling does not duplicate users."""
    form = FORMS["access"]({"staff_addresses": "Pat@EXAMPLE.ORG\n\npat@example.org"})
    assert form.is_valid()
    assert form_values(form)["staff_addresses"] == ["pat@example.org"]
    # Sign-in is by address only (#993): no list grants a whole domain.
    assert not {name for name in form.fields if name.endswith("_domains")}


def test_an_access_step_saved_with_empty_domain_lists_is_still_read():
    """A step saved before #993 with no domain listed needs no new save."""
    values = VALUES["access"] | {"staff_domains": [], "ministry_domains": []}
    assert validate_values("access", values) == VALUES["access"]


def test_identity_lists_and_unknown_steps_are_bounded():
    """Long lists do not generate an unbounded policy or draft response."""
    form = FORMS["access"](
        {"staff_addresses": "\n".join(f"staff{i}@example.org" for i in range(51))}
    )
    assert not form.is_valid()
    with pytest.raises(ValueError):
        form_values(form)
    for value in (None, "passwords"):
        with pytest.raises(ValueError):
            validate_values(value, {})
        with pytest.raises(ValueError):
            initial_values(value, {})


def test_slack_channel_is_shown_and_required_only_while_enabled():
    """The page shows the channel only for an enabled Slack (#563), so the
    server's "enable and supply, or disable and clear" rule is never hit."""
    attrs = FORMS["slack"]().fields["channel_id"].widget.attrs
    assert attrs["data-show-when"] == "enabled=on"
    assert attrs["data-required-when-shown"] == ""
    assert str(attrs["data-empty-hint"]).startswith("Enter the Slack channel ID")
    # Hidden, the field is disabled and not sent: Slack off clears it.
    assert FORMS["slack"]({"enabled": ""}).is_valid()
    assert not FORMS["slack"]({"enabled": "on"}).is_valid()


BACKUP_LINK = "https://drive.google.com/drive/folders/A1b2C3d4E5f6G7h8I9j0"


def backup_form(data, **options):
    """The Off-site backups step's bound form (#2200)."""
    from parishkit.stewardship.accounts.setup_forms import SetupBackupForm

    return SetupBackupForm(data, **options)


def test_backup_off_carries_no_folder_and_on_needs_both_fields():
    """Off saves no folder or acknowledgement; on needs the folder link and
    the private-key checkbox, and stores the link in canonical form."""
    off = backup_form({})
    assert off.is_valid(), off.errors
    assert form_values(off) == {
        "enabled": False,
        "folder_link": "",
        "acknowledged": False,
    }
    missing = backup_form({"enabled": "on", "folder_link": BACKUP_LINK})
    assert not missing.is_valid()
    assert missing.errors["acknowledged"][0].startswith("Tick this box")
    unlinked = backup_form({"enabled": "on", "acknowledged": "on"})
    assert not unlinked.is_valid() and "folder_link" in unlinked.errors
    pasted = backup_form(
        {
            "enabled": "on",
            "folder_link": "https://drive.google.com/open?id=A1b2C3d4E5f6G7h8I9j0",
            "acknowledged": "on",
        }
    )
    assert pasted.is_valid(), pasted.errors
    assert form_values(pasted)["folder_link"] == BACKUP_LINK
    document = backup_form(
        {"enabled": "on", "folder_link": "https://example.org/x", "acknowledged": "on"}
    )
    assert not document.is_valid()
    assert document.errors.as_data()["folder_link"][0].code == "invalid_folder_link"


def test_backup_hidden_leftovers_and_a_closed_drive_are_refused():
    """A folder or tick sent while copies are off is refused, as Slack's
    channel is; where the local environment does not reach Drive, a tick is
    refused and the box is drawn unavailable."""
    leftover = backup_form({"folder_link": BACKUP_LINK})
    assert not leftover.is_valid()
    assert leftover.errors.as_data()["__all__"][0].code == "backup_mismatch"
    closed = backup_form(
        {"enabled": "on", "folder_link": BACKUP_LINK, "acknowledged": "on"},
        drive_closed=True,
    )
    assert not closed.is_valid()
    assert closed.errors.as_data()["__all__"][0].code == "drive_not_in_local"
    closed_form = backup_form(None, drive_closed=True, initial={"enabled": True})
    html = closed_form["enabled"].as_field_group()
    assert "disabled" in html and "checked" not in html
    assert "not available in this local environment" in html


def test_backup_fields_follow_the_switch_and_name_the_mailbox():
    """The folder and the checkbox are shown and required only while copies
    are ticked; the folder help names the saved delegated mailbox user."""
    form = backup_form(None, mailbox="mail@example.org")
    for name in ("folder_link", "acknowledged"):
        attrs = form.fields[name].widget.attrs
        assert attrs["data-show-when"] == "enabled=on", name
        assert attrs["data-required-when-shown"] == "", name
        assert attrs["data-missing-hint"].startswith("Add the folder link"), name
    assert "pattern" in form.fields["folder_link"].widget.attrs
    rendered = form["folder_link"].as_field_group()
    assert "mail@example.org" in rendered


def test_backup_values_round_trip_and_refuse_other_shapes():
    """Stored values are re-read exactly; wrong types or fields are refused."""
    on = {"enabled": True, "folder_link": BACKUP_LINK, "acknowledged": True}
    assert validate_values("backup", on) == on
    for bad in (
        on | {"enabled": "true"},
        on | {"extra": ""},
        {"enabled": True, "folder_link": BACKUP_LINK},
        on | {"folder_link": BACKUP_LINK + "?usp=sharing"},
    ):
        with pytest.raises(ValueError):
            validate_values("backup", bad)
