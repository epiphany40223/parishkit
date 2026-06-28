from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.parishsoft import ParishSoftData
from parishkit.pk_sync_ps_to_cc import (
    cc_sync_config_from_yaml,
    compute_all_actions,
    detect_name_mismatches,
    filter_unsubscribed,
    parishsoft_members_by_email,
    resolve_desired_state,
)
from parishkit.pk_sync_ps_to_cc import (
    main as sync_ps_to_cc_main,
)


class CCClient:
    """Fake Constant Contact client that records calls and returns fixtures.

    Reads are served from the cc_lists/cc_contacts fixtures; writes are
    captured in self.calls so tests can assert on what would be sent.
    """

    def __init__(self):
        self.calls = []

    def get_all(self, endpoint, field, **kwargs):
        """Record the read and return the matching list or contact fixture."""
        self.calls.append(("get_all", endpoint, field, kwargs))
        if endpoint == "contact_lists":
            return cc_lists()
        return cc_contacts()

    def post(self, endpoint, body):
        """Record a create call and return an empty response."""
        self.calls.append(("post", endpoint, body))
        return {}

    def put(self, endpoint, body):
        """Record an update call and return an empty response."""
        self.calls.append(("put", endpoint, body))
        return {}


class EmailProvider:
    """Fake email provider that captures sent messages instead of sending."""

    def __init__(self):
        self.sent = []

    def send(self, message, *, dry_run=False):
        """Record the message and dry-run flag, then echo the message back."""
        self.sent.append((message, dry_run))
        return message


def write_config(tmp_path: Path, *, dry_run: bool = False) -> Path:
    """Write a complete contacts YAML config under tmp_path.

    Produces one workgroup-to-list mapping with notifications enabled; the
    dry_run flag lets tests toggle write-skipping behavior.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    api_key = tmp_path / "parishsoft-api-key.txt"
    api_key.write_text("key", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
common:
  dry_run: {str(dry_run).lower()}
parishsoft:
  api_key_file: {api_key}
  cache_dir: {tmp_path / "cache"}
  cache_limit: 1d
constant_contact:
  client_id_file: {tmp_path / "constant-contact-client.json"}
  access_token_file: {tmp_path / "constant-contact-token.json"}
email:
  provider: google-workspace
  service_account_file: {tmp_path / "google-service-account.json"}
  delegated_user: no-reply@example.org
sync:
  update_names: true
  notifications:
    sender: no-reply@example.org
  lists:
    - source_workgroup: Newsletter WG
      target_list: Newsletter
      notifications:
        - admin@example.org
""",
        encoding="utf-8",
    )
    return config


def parishsoft_data() -> ParishSoftData:
    """Build a minimal ParishSoftData fixture with two newsletter members.

    Both Ann and Bob belong to the "Newsletter WG" workgroup; all other
    ParishSoft collections are left empty since the sync only reads members.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    members = {
        1: {
            "memberDUID": 1,
            "firstName": "Ann",
            "lastName": "Smith",
            "py friendly name FL": "Ann Smith",
            "emailAddress": "ann@example.org",
            "py emailAddresses": ["ann@example.org"],
        },
        2: {
            "memberDUID": 2,
            "firstName": "Bob",
            "lastName": "Jones",
            "py friendly name FL": "Bob Jones",
            "emailAddress": "bob@example.org",
            "py emailAddresses": ["bob@example.org"],
        },
    }
    return ParishSoftData(
        organization_id=7,
        families={},
        members=members,
        family_groups={},
        family_workgroups={},
        family_workgroup_memberships={},
        member_contactinfos={},
        member_workgroups={},
        member_workgroup_memberships={
            10: {
                "name": "Newsletter WG",
                "membership": [
                    {"py member duid": 1},
                    {"py member duid": 2},
                ],
            }
        },
        ministry_types={},
        ministry_type_memberships={},
        funds={},
        pledges={},
        contributions={},
    )


def cc_lists():
    """Return a fixture with one Constant Contact list holding a stale member.

    The "Newsletter" list currently contains old@example.org, who is not in
    the ParishSoft fixture and should therefore be unsubscribed by the sync.
    """
    return [
        {
            "list_id": "list-1",
            "name": "Newsletter",
            "CONTACTS": {"old@example.org": {}},
        }
    ]


def cc_contacts():
    """Return Constant Contact contact fixtures spanning the sync edge cases.

    Ann has a mismatched first name ("Anne" vs ParishSoft "Ann") to exercise
    name updates, Bob is unsubscribed, and Old is a stale list member with no
    ParishSoft counterpart.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    return [
        {
            "contact_id": "contact-ann",
            "email_address": {
                "address": "ann@example.org",
                "permission_to_send": "implicit",
            },
            "first_name": "Anne",
            "last_name": "Smith",
            "list_memberships": [],
        },
        {
            "contact_id": "contact-bob",
            "email_address": {
                "address": "bob@example.org",
                "permission_to_send": "unsubscribed",
            },
            "first_name": "Bob",
            "last_name": "Jones",
            "list_memberships": ["list-1"],
        },
        {
            "contact_id": "contact-old",
            "email_address": {
                "address": "old@example.org",
                "permission_to_send": "implicit",
            },
            "first_name": "Old",
            "last_name": "Member",
            "list_memberships": ["list-1"],
        },
    ]


def test_cc_sync_config_validation():
    """Verify a valid YAML block parses into the expected sync config.

    update_names and the first list mapping's source workgroup must survive
    the round trip from YAML into the typed config object.
    """
    config = cc_sync_config_from_yaml(
        {
            "sync": {
                "update_names": True,
                "notifications": {"sender": "no-reply@example.org"},
                "lists": [
                    {
                        "source_workgroup": "Newsletter WG",
                        "target_list": "Newsletter",
                        "notifications": ["admin@example.org"],
                    }
                ],
            }
        }
    )

    assert config.update_names
    assert config.mappings[0].source_workgroup == "Newsletter WG"


def test_cc_sync_config_rejects_missing_lists():
    """Verify config parsing fails when the required `lists` key is absent."""
    with pytest.raises(ConfigError, match="lists"):
        cc_sync_config_from_yaml({"sync": {}})


def test_desired_state_and_unsubscribed_filtering():
    """Verify desired-state resolution then unsubscribed filtering.

    resolve_desired_state maps the workgroup to both members; filtering then
    drops the unsubscribed Bob from the desired set and reports him (with his
    friendly name) as an unsubscribed contact.
    """
    config = cc_sync_config_from_yaml(
        {
            "sync": {
                "lists": [
                    {
                        "source_workgroup": "Newsletter WG",
                        "target_list": "Newsletter",
                    }
                ]
            }
        }
    )
    data = parishsoft_data()
    desired = resolve_desired_state(config, data, cc_lists())

    assert desired == [{"ann@example.org", "bob@example.org"}]

    unsubscribed = filter_unsubscribed(
        cc_contacts(),
        desired,
        parishsoft_members_by_email(data.members),
    )

    assert desired == [{"ann@example.org"}]
    assert unsubscribed[0][0][0] == "bob@example.org"
    assert "Bob Jones" in unsubscribed[0][0][1]


def test_action_computation_and_name_updates():
    """Verify the full set of sync actions, including name mismatches.

    A new email triggers create+subscribe, an existing member subscribes, the
    stale member unsubscribes, and Ann's differing name yields update_name.
    """
    config = cc_sync_config_from_yaml(
        {
            "sync": {
                "update_names": True,
                "lists": [
                    {
                        "source_workgroup": "Newsletter WG",
                        "target_list": "Newsletter",
                    }
                ],
            }
        }
    )
    desired = [{"ann@example.org", "new@example.org"}]
    contacts = {item["email_address"]["address"]: item for item in cc_contacts()}
    # Attach Ann's ParishSoft record so name-mismatch detection has a source
    # name to compare against the contact's stored "Anne".
    contacts["ann@example.org"]["PS MEMBERS"] = [parishsoft_data().members[1]]

    actions = compute_all_actions(config, desired, cc_lists(), contacts)
    actions.extend(detect_name_mismatches(contacts, update_names=True))

    assert [(item.type, item.email, item.list_uuid) for item in actions] == [
        ("create", "new@example.org", None),
        ("subscribe", "ann@example.org", "list-1"),
        ("subscribe", "new@example.org", "list-1"),
        ("unsubscribe", "old@example.org", "list-1"),
        ("update_name", "ann@example.org", None),
    ]


def test_sync_ps_to_cc_main_writes_constant_contact_and_email(tmp_path, monkeypatch):
    """Verify a live run posts/puts to Constant Contact and emails admins.

    With dry_run off, main must create and update contacts (post + put) and
    send the unsubscribed-report email to the configured notification address.
    """
    cc = CCClient()
    email = EmailProvider()
    loader_calls = []
    # Replace the real ParishSoft client builder with a no-op stand-in; the
    # injected loader below supplies the data, so the client is never used.
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    def loader(_client, **kwargs):
        """Stub data loader: record load options and return the fixture."""
        loader_calls.append(kwargs)
        return parishsoft_data()

    assert (
        sync_ps_to_cc_main(
            ["--config", str(write_config(tmp_path))],
            loader=loader,
            cc_factory=lambda _config: cc,
            email_provider=email,
        )
        == 0
    )

    assert loader_calls == [{"active_only": True, "parishioners_only": False}]
    assert any(call[0] == "post" for call in cc.calls)
    assert any(call[0] == "put" for call in cc.calls)
    assert email.sent
    assert email.sent[0][0].to == ("admin@example.org",)


def test_sync_ps_to_cc_dry_run_skips_writes_and_email(tmp_path, monkeypatch):
    """Verify dry_run reads only, performing no writes or email.

    The run should issue just the two read calls (lists and contacts) and
    never post, put, or send mail.
    """
    cc = CCClient()
    config = write_config(tmp_path, dry_run=True)
    text = config.read_text(encoding="utf-8")
    # Disable the email section so this run cannot send mail even if it tried;
    # renaming the key makes it invisible to the config loader.
    config.write_text(text.replace("email:\n", "unused_email:\n"), encoding="utf-8")
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
        )
        == 0
    )

    assert [call[0] for call in cc.calls] == ["get_all", "get_all"]
