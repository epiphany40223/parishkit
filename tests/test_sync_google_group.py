from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.google.auth import GoogleAPIError
from parishkit.parishsoft import ParishSoftData
from parishkit.pk_sync_ps_to_ggroup import (
    DesiredMember,
    GroupSync,
    SyncConfig,
    compute_actions,
    desired_members,
    load_google_credentials,
    normalize_email,
    sync_config_from_yaml,
    sync_group,
)
from parishkit.pk_sync_ps_to_ggroup import (
    main as sync_google_group_main,
)


class Request:
    """Fake Google API request whose execute() returns a canned response."""

    def __init__(self, response=None, exc: Exception | None = None):
        """Store either the canned response or exception to raise."""
        self.response = response or {}
        self.exc = exc

    def execute(self):
        """Return the stored response, mimicking the real request execution."""
        if self.exc is not None:
            raise self.exc
        return self.response


class Members:
    """Fake Directory API members resource recording every call it receives.

    Write methods (insert/update/delete) just log their kwargs so tests can
    assert the group mutations the sync would perform.
    """

    def __init__(self):
        self.calls = []
        self.list_error: Exception | None = None

    def list(self, **kwargs):
        """Record the list call and return the current group roster fixture."""
        self.calls.append(("list", kwargs))
        if self.list_error is not None:
            return Request(exc=self.list_error)
        return Request(
            {
                "members": [
                    {"email": "old@example.org", "role": "MEMBER", "id": "old-id"},
                    {"email": "leader@example.org", "role": "MEMBER", "id": "lead-id"},
                ]
            }
        )

    def insert(self, **kwargs):
        """Record an add-member call and return an empty request."""
        self.calls.append(("insert", kwargs))
        return Request()

    def update(self, **kwargs):
        """Record a member-update call and return an empty request."""
        self.calls.append(("update", kwargs))
        return Request()

    def delete(self, **kwargs):
        """Record a remove-member call and return an empty request."""
        self.calls.append(("delete", kwargs))
        return Request()


class Groups:
    """Fake Groups Settings resource returning fixed posting permissions."""

    def __init__(self, permission: str = "ALL_MEMBERS_CAN_POST"):
        self.calls = []
        self.permission = permission
        self.get_error: Exception | None = None

    def get(self, **kwargs):
        """Record the get call and return canned group settings."""
        self.calls.append(("get", kwargs))
        if self.get_error is not None:
            return Request(exc=self.get_error)
        return Request({"whoCanPostMessage": self.permission})


class AdminService:
    """Fake Admin SDK Directory service exposing the members resource."""

    def __init__(self):
        self._members = Members()

    def members(self):
        """Return the fake members resource."""
        return self._members


class SettingsService:
    """Fake Groups Settings service exposing the groups resource."""

    def __init__(self, permission: str = "ALL_MEMBERS_CAN_POST"):
        self._groups = Groups(permission=permission)

    def groups(self):
        """Return the fake groups resource."""
        return self._groups


class EmailProvider:
    """Fake email provider that captures sent messages instead of sending."""

    def __init__(self):
        self.sent = []

    def send(self, message, *, dry_run=False):
        """Record the message and dry-run flag, then echo the message back."""
        self.sent.append((message, dry_run))
        return message


def write_config(tmp_path: Path, *, dry_run: bool = False) -> Path:
    """Write a complete groups YAML config under tmp_path.

    Defines one group sourced from a ministry, a workgroup, and a static
    member, with notifications enabled; dry_run lets tests skip writes.

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
google:
  user_token_file: {tmp_path / "google-user-token.json"}
email:
  provider: google_workspace
  service_account_file: {tmp_path / "google-service-account.json"}
  delegated_user: no-reply@example.org
sync:
  google_mail_domains:
    - gmail.com
    - example.org
  notifications:
    sender: no-reply@example.org
  groups:
    - group: group@example.org
      notify:
        - admin@example.org
      ministries:
        - Readers
      workgroups:
        - Movers
      static_members:
        - email: static@example.org
          leader: false
""",
        encoding="utf-8",
    )
    return config


def parishsoft_data() -> ParishSoftData:
    """Build a ParishSoftData fixture with one ministry and one workgroup member.

    Ann chairs the "Readers" ministry (becomes a group leader) and Bob belongs
    to the "Movers" workgroup with a plus-tagged Gmail address to exercise
    email normalization. Other collections are empty.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    members = {
        1: {
            "memberDUID": 1,
            "firstName": "Ann",
            "lastName": "Leader",
            "py friendly name FL": "Ann Leader",
            "emailAddress": "leader@example.org",
            "py emailAddresses": ["leader@example.org"],
            "py ministries": {"Readers": {"name": "Readers", "role": "Chairperson"}},
            "py workgroups": {},
        },
        2: {
            "memberDUID": 2,
            "firstName": "Bob",
            "lastName": "Mover",
            "py friendly name FL": "Bob Mover",
            "emailAddress": "bob.mover+tag@gmail.com",
            "py emailAddresses": ["bob.mover+tag@gmail.com"],
            "py ministries": {},
            "py workgroups": {"Movers": {"name": "Movers"}},
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
        member_workgroup_memberships={},
        ministry_types={},
        ministry_type_memberships={},
        funds={},
        pledges={},
        contributions={},
    )


def test_sync_config_validation():
    """Verify a valid YAML block parses into the expected group config.

    The first group's selector type and the top-level notification sender
    must survive parsing into the typed config object.
    """
    config = sync_config_from_yaml(
        {
            "sync": {
                "google_mail_domains": ["example.org"],
                "notifications": {"sender": "no-reply@example.org"},
                "groups": [
                    {
                        "group": "group@example.org",
                        "notify": ["admin@example.org"],
                        "selectors": [
                            {
                                "type": "ministry_role",
                                "ministry_prefix": "500",
                                "ministry_pattern": r"^\d\d\d.*",
                                "member_roles": ["Team Member"],
                                "leader_roles": ["Leader"],
                            }
                        ],
                    }
                ],
            }
        }
    )

    assert config.groups[0].selectors[0].type == "ministry_role"
    assert config.groups[0].selectors[0].ministry_pattern == r"^\d\d\d.*"
    assert config.sender == "no-reply@example.org"


def test_sync_config_rejects_duplicate_group_targets():
    """One Google Group must not be reconciled by multiple config entries."""
    with pytest.raises(ConfigError, match="group values must be unique"):
        sync_config_from_yaml(
            {
                "sync": {
                    "groups": [
                        {
                            "group": "group@example.org",
                            "static_members": [{"email": "ann@example.org"}],
                        },
                        {
                            "group": "GROUP@example.org",
                            "static_members": [{"email": "bob@example.org"}],
                        },
                    ]
                }
            }
        )


def test_sync_config_requires_sender_for_notifications():
    """Group notification recipients require a configured sender."""
    with pytest.raises(ConfigError, match="sync.notifications.sender is required"):
        sync_config_from_yaml(
            {
                "sync": {
                    "groups": [
                        {
                            "group": "group@example.org",
                            "notify": ["admin@example.org"],
                            "static_members": [{"email": "ann@example.org"}],
                        }
                    ]
                }
            }
        )


def test_sync_config_rejects_group_without_source():
    """Verify parsing fails for a group with no member source defined.

    A group lacking ministries, workgroups, selectors, or static members has
    nothing to sync and must be rejected.
    """
    with pytest.raises(ConfigError, match="source"):
        sync_config_from_yaml({"sync": {"groups": [{"group": "group@example.org"}]}})


def test_sync_config_rejects_invalid_ministry_pattern():
    """Selector ministry_pattern must be a valid regular expression."""
    with pytest.raises(ConfigError, match="ministry_pattern"):
        sync_config_from_yaml(
            {
                "sync": {
                    "groups": [
                        {
                            "group": "group@example.org",
                            "selectors": [
                                {
                                    "type": "all_ministry_chairs",
                                    "ministry_pattern": "[",
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_google_group_credentials_resolve_relative_paths(tmp_path, monkeypatch):
    """Relative Google group credential paths resolve against the config directory."""
    calls = []

    def fake_load(path, *, scopes, subject):
        """Capture the resolved service account path for assertion."""
        calls.append((path, scopes, subject))
        return object()

    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.load_service_account_credentials",
        fake_load,
    )

    load_google_credentials(
        {
            "google": {
                "service_account_file": "credentials/google-service-account.json",
                "delegated_subject": "itadmin@example.org",
            }
        },
        base_dir=tmp_path,
    )

    assert calls[0][0] == tmp_path / "credentials" / "google-service-account.json"


def test_desired_members_from_ministries_workgroups_and_static():
    """Verify desired_members merges ministry, workgroup, and static sources.

    The ministry chair becomes a leader, the workgroup member a non-leader,
    and the static entry keeps its configured leader flag.
    """
    config = sync_config_from_yaml(
        {
            "sync": {
                "groups": [
                    {
                        "group": "group@example.org",
                        "ministries": ["Readers"],
                        "workgroups": ["Movers"],
                        "static_members": [
                            {"email": "static@example.org", "leader": True}
                        ],
                    }
                ]
            }
        }
    )

    desired = desired_members(parishsoft_data(), config.groups[0])

    assert [(item.email, item.leader) for item in desired] == [
        ("leader@example.org", True),
        ("bob.mover+tag@gmail.com", False),
        ("static@example.org", True),
    ]


def test_desired_members_skips_blank_primary_email_fragment():
    """Blank ParishSoft email fragments do not become desired group members."""
    data = parishsoft_data()
    data.members[2]["emailAddress"] = " ; bob@example.org"
    data.members[2]["py emailAddresses"] = ["", "bob@example.org"]
    config = sync_config_from_yaml(
        {"sync": {"groups": [{"group": "group@example.org", "workgroups": ["Movers"]}]}}
    )

    desired = desired_members(data, config.groups[0])

    assert [(item.email, item.leader) for item in desired] == [
        ("bob@example.org", False)
    ]


def test_desired_members_merges_google_aliases_by_configured_domain():
    """Equivalent Gmail-style desired addresses collapse to one group member."""
    data = parishsoft_data()
    data.members[3] = {
        "memberDUID": 3,
        "firstName": "Robert",
        "lastName": "Mover",
        "py friendly name FL": "Robert Mover",
        "emailAddress": "bobmover@gmail.com",
        "py emailAddresses": ["bobmover@gmail.com"],
        "py ministries": {},
        "py workgroups": {"Movers": {"name": "Movers"}},
    }
    config = sync_config_from_yaml(
        {
            "sync": {
                "google_mail_domains": ["gmail.com"],
                "groups": [{"group": "group@example.org", "workgroups": ["Movers"]}],
            }
        }
    )

    desired = desired_members(
        data,
        config.groups[0],
        config.google_mail_domains,
    )

    assert [(item.email, item.leader, item.names) for item in desired] == [
        (
            "bob.mover+tag@gmail.com",
            False,
            ["Bob Mover", "Robert Mover"],
        )
    ]


def test_sync_group_refuses_empty_desired_state_with_current_members():
    """A zero-member source cannot remove all group members without opt-in."""
    group = GroupSync(group="group@example.org", notify=(), workgroups=("Empty",))

    with pytest.raises(ConfigError, match="allow_empty"):
        sync_group(
            AdminService(),
            SettingsService(),
            None,
            parishsoft_data(),
            SyncConfig(groups=(group,), sender=None, google_mail_domains=frozenset()),
            group,
            dry_run=True,
            log=logging.getLogger("test"),
        )


def test_all_ministry_chairs_selector_can_filter_ministry_names_by_pattern():
    """ministry_pattern limits broad all_ministry_chairs selection by ministry name."""
    data = parishsoft_data()
    data.members[3] = {
        "memberDUID": 3,
        "firstName": "Nina",
        "lastName": "Numbered",
        "py friendly name FL": "Nina Numbered",
        "emailAddress": "numbered@example.org",
        "py emailAddresses": ["numbered@example.org"],
        "py ministries": {
            "100-Readers": {"name": "100-Readers", "role": "Chairperson"}
        },
        "py workgroups": {},
    }
    config = sync_config_from_yaml(
        {
            "sync": {
                "groups": [
                    {
                        "group": "chairs@example.org",
                        "selectors": [
                            {
                                "type": "all_ministry_chairs",
                                "ministry_pattern": r"^\d\d\d.*",
                                "staff_owner_domains": ["example.org"],
                            }
                        ],
                    }
                ]
            }
        }
    )

    desired = desired_members(data, config.groups[0])

    assert [(item.email, item.leader) for item in desired] == [
        ("numbered@example.org", True)
    ]


def test_all_ministry_chairs_staff_owner_uses_primary_email_domain():
    """Owner promotion checks the address that will be added to the group."""
    data = parishsoft_data()
    data.members[3] = {
        "memberDUID": 3,
        "firstName": "Pat",
        "lastName": "Chair",
        "py friendly name FL": "Pat Chair",
        "emailAddress": "pat@gmail.com; staff@example.org",
        "py emailAddresses": ["pat@gmail.com", "staff@example.org"],
        "py ministries": {
            "100-Readers": {"name": "100-Readers", "role": "Chairperson"}
        },
        "py workgroups": {},
    }
    config = sync_config_from_yaml(
        {
            "sync": {
                "groups": [
                    {
                        "group": "chairs@example.org",
                        "selectors": [
                            {
                                "type": "all_ministry_chairs",
                                "ministry_pattern": r"^\d\d\d.*",
                                "staff_owner_domains": ["example.org"],
                            }
                        ],
                    }
                ]
            }
        }
    )

    desired = desired_members(data, config.groups[0])

    assert ("pat@gmail.com", False) in [(item.email, item.leader) for item in desired]


def test_ministry_role_selector_preserves_leader_match_across_ministries():
    """A later non-leader role must not demote an earlier leader role."""
    data = parishsoft_data()
    data.members[3] = {
        "memberDUID": 3,
        "firstName": "Riley",
        "lastName": "Roles",
        "py friendly name FL": "Riley Roles",
        "emailAddress": "riley@example.org",
        "py emailAddresses": ["riley@example.org"],
        "py ministries": {
            "500-Alpha": {"name": "500-Alpha", "role": "Leader"},
            "500-Beta": {"name": "500-Beta", "role": "Team Member"},
        },
        "py workgroups": {},
    }
    config = sync_config_from_yaml(
        {
            "sync": {
                "groups": [
                    {
                        "group": "roles@example.org",
                        "selectors": [
                            {
                                "type": "ministry_role",
                                "ministry_prefix": "500",
                                "member_roles": ["Team Member"],
                                "leader_roles": ["Leader"],
                            }
                        ],
                    }
                ]
            }
        }
    )

    desired = desired_members(data, config.groups[0])

    assert ("riley@example.org", True) in [
        (item.email, item.leader) for item in desired
    ]


def test_compute_actions_add_delete_and_change_role():
    """Verify compute_actions diffs desired vs current group membership.

    A leader already present but ranked MEMBER yields change_role to OWNER; a
    current member absent from the desired set yields delete. Bob matches an
    existing member only after Gmail normalization, so he needs no action.
    """
    actions = compute_actions(
        [
            DesiredMember("leader@example.org", True, ["Ann Leader"]),
            DesiredMember("bob.mover+tag@gmail.com", False, ["Bob Mover"]),
        ],
        [
            {"email": "leader@example.org", "role": "MEMBER", "id": "lead-id"},
            {"email": "bobmover@gmail.com", "role": "MEMBER", "id": "bob-id"},
            {"email": "old@example.org", "role": "MEMBER", "id": "old-id"},
        ],
        frozenset({"gmail.com", "example.org"}),
    )

    assert [
        (item.action, item.email, item.role, item.group_member_id) for item in actions
    ] == [
        ("change_role", "leader@example.org", "OWNER", "lead-id"),
        ("delete", "old@example.org", None, "old-id"),
    ]
    assert (
        normalize_email("bob.mover+tag@gmail.com", frozenset({"gmail.com"}))
        == "bobmover@gmail.com"
    )


def test_sync_google_group_main_writes_group_changes_and_notifications(
    tmp_path, monkeypatch
):
    """Verify a live run applies group changes and emails notifications.

    End to end, main must insert the new ministry/workgroup and static members,
    promote the leader to OWNER, delete the stale member, and send the change
    report to the configured notification address.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    admin = AdminService()
    settings = SettingsService()
    email = EmailProvider()
    loader_calls = []
    log_file = tmp_path / "sync-google-group.log"
    # Replace the real ParishSoft client builder with a no-op stand-in; the
    # injected loader below supplies the data, so the client is never used.
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    def loader(_client, **kwargs):
        """Stub data loader: record load options and return the fixture."""
        loader_calls.append(kwargs)
        return parishsoft_data()

    assert (
        sync_google_group_main(
            ["--config", str(write_config(tmp_path)), "--log-file", str(log_file)],
            loader=loader,
            service_factory=lambda _config: (admin, settings),
            email_provider=email,
        )
        == 0
    )

    assert loader_calls == [{"active_only": True, "parishioners_only": False}]
    assert "Google Group sync operation completed successfully" in (
        log_file.read_text(encoding="utf-8")
    )
    assert (
        "insert",
        {
            "groupKey": "group@example.org",
            "body": {"email": "bob.mover+tag@gmail.com", "role": "MEMBER"},
        },
    ) in admin._members.calls
    assert (
        "insert",
        {
            "groupKey": "group@example.org",
            "body": {"email": "static@example.org", "role": "MEMBER"},
        },
    ) in admin._members.calls
    assert (
        "update",
        {
            "groupKey": "group@example.org",
            "memberKey": "lead-id",
            "body": {"role": "OWNER"},
        },
    ) in admin._members.calls
    assert (
        "delete",
        {"groupKey": "group@example.org", "memberKey": "old-id"},
    ) in admin._members.calls
    assert email.sent
    assert email.sent[0][0].to == ("admin@example.org",)
    assert (
        email.sent[0][0].subject
        == "Update to Google Group group@example.org for Readers, Movers"
    )
    assert "The following changes were made to the Discussion Google Group" in (
        email.sent[0][0].html or ""
    )
    assert "<th" in (email.sent[0][0].html or "")
    assert "These email addresses were obtained from PS" in (
        email.sent[0][0].html or ""
    )
    assert "Members in the &quot;Readers&quot; ministry" in (
        email.sent[0][0].html or ""
    )
    assert "Members in the &quot;Movers&quot; Member WorkGroup" in (
        email.sent[0][0].html or ""
    )
    assert "keyword" not in (email.sent[0][0].html or "")
    assert "[Static member]" in (email.sent[0][0].html or "")
    assert "Added to group" in (email.sent[0][0].html or "")


def test_compute_actions_updates_role_by_matched_google_member_id():
    """Alias-matched role changes use the current Google member identifier."""
    actions = compute_actions(
        [DesiredMember("bob.mover+tag@gmail.com", True, ["Bob Mover"])],
        [{"email": "bobmover@gmail.com", "role": "MEMBER", "id": "bob-id"}],
        frozenset({"gmail.com"}),
    )

    assert [
        (item.action, item.email, item.role, item.group_member_id) for item in actions
    ] == [("change_role", "bob.mover+tag@gmail.com", "OWNER", "bob-id")]


def test_sync_google_group_email_treats_domain_posting_as_discussion(
    tmp_path,
    monkeypatch,
):
    """ALL_IN_DOMAIN_CAN_POST should use the old Discussion wording."""
    admin = AdminService()
    settings = SettingsService(permission="ALL_IN_DOMAIN_CAN_POST")
    email = EmailProvider()
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_google_group_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
            email_provider=email,
        )
        == 0
    )

    assert "Discussion Google Group" in (email.sent[0][0].html or "")
    assert "cannot post to this group" not in (email.sent[0][0].text or "")


def test_sync_google_group_dry_run_skips_writes_and_email(tmp_path, monkeypatch):
    """Verify a dry run reads the roster only, with no writes or email.

    The members resource should see just the list call and never insert,
    update, or delete; no notification mail should be sent.
    """
    admin = AdminService()
    settings = SettingsService()
    config = write_config(tmp_path, dry_run=True)
    text = config.read_text(encoding="utf-8")
    # Disable the email section so this run cannot send mail even if it tried;
    # renaming the key makes it invisible to the config loader.
    config.write_text(text.replace("email:\n", "unused_email:\n"), encoding="utf-8")
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_google_group_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
        )
        == 0
    )

    assert [call[0] for call in admin._members.calls] == ["list"]


def test_sync_google_group_reports_missing_google_group(
    tmp_path,
    monkeypatch,
    capsys,
):
    """A missing configured Google Group logs a helpful config error."""
    admin = AdminService()
    admin._members.list_error = GoogleAPIError(404, "group not found")
    settings = SettingsService()
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_google_group_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
            email_provider=EmailProvider(),
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_sync_ps_to_ggroup" in error
    assert "Configured Google Group was not found" in error
    assert "sync.groups[].group" in error
    assert [call[0] for call in admin._members.calls] == ["list"]


def test_sync_google_group_reports_missing_parishsoft_source(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Unknown ParishSoft ministry/workgroup source names abort before writes."""
    admin = AdminService()
    settings = SettingsService()
    config = write_config(tmp_path)
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace("- Readers", "- Missing Ministry").replace(
            "- Movers", "- Missing Workgroup"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_google_group_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_sync_ps_to_ggroup" in error
    assert "Configured ParishSoft ministry was not found" in error
    assert "sync.groups[].ministries" in error
    assert admin._members.calls == []


def test_sync_google_group_checks_posting_permissions_before_writes(
    tmp_path,
    monkeypatch,
):
    """Notification context failures must happen before group membership writes."""
    admin = AdminService()
    settings = SettingsService()
    settings._groups.get_error = GoogleAPIError(403, "settings denied")
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_google_group_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
            email_provider=EmailProvider(),
        )
        == 2
    )

    assert [call[0] for call in admin._members.calls] == ["list"]
    assert settings._groups.calls == [
        (
            "get",
            {
                "groupUniqueId": "group@example.org",
                "fields": "whoCanPostMessage",
            },
        )
    ]


def test_sync_google_group_reports_selector_with_no_matching_ministries(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Selector name filters that match nothing abort before group writes."""
    admin = AdminService()
    settings = SettingsService()
    config = tmp_path / "config.yaml"
    api_key = tmp_path / "parishsoft-api-key.txt"
    api_key.write_text("key", encoding="utf-8")
    config.write_text(
        f"""
common:
  dry_run: false
parishsoft:
  api_key_file: {api_key}
  cache_dir: {tmp_path / "cache"}
  cache_limit: 1d
google:
  user_token_file: {tmp_path / "google-user-token.json"}
sync:
  notifications:
    sender: no-reply@example.org
  groups:
    - group: group@example.org
      selectors:
        - type: all_ministry_chairs
          ministry_pattern: "^Missing.*"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_google_group_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_sync_ps_to_ggroup" in error
    assert "Configured ParishSoft selector matched no ministries" in error
    assert "sync.groups[].selectors[].ministry_pattern" in error
    assert admin._members.calls == []
