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
    SyncAction,
    SyncConfig,
    apply_actions,
    compute_actions,
    desired_members,
    load_google_credentials,
    normalize_email,
    sync_config_from_yaml,
    sync_group,
    validate_large_removal_guard,
)
from parishkit.pk_sync_ps_to_ggroup import (
    main as sync_google_group_main,
)


@pytest.fixture(autouse=True)
def _restore_parishkit_logging():
    """Undo the package logging setup that ``sync_google_group_main`` performs.

    ``setup_logging`` replaces the handlers of the ``parishkit`` logger and its
    children, binds a console handler to this test's captured stderr, and stops
    propagation. Left in place, later tests in the same process log through a
    handler whose stream pytest has closed (#2364), so restore the snapshot and
    close any handler this test added.
    """
    manager = logging.Logger.manager
    loggers = [logging.getLogger("parishkit")] + [
        logger
        for name, logger in manager.loggerDict.items()
        if name.startswith("parishkit.") and isinstance(logger, logging.Logger)
    ]
    saved = {
        logger: (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
        for logger in loggers
    }
    yield
    for name, logger in list(manager.loggerDict.items()):
        if (name == "parishkit" or name.startswith("parishkit.")) and isinstance(
            logger, logging.Logger
        ):
            handlers, level, propagate, disabled = saved.get(
                logger, ([], logging.NOTSET, True, False)
            )
            for handler in logger.handlers:
                if handler not in handlers:
                    handler.close()
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


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
        self.list_errors_by_group: dict[str, Exception] = {}
        self.roster = [
            {"email": "old@example.org", "role": "MEMBER", "id": "old-id"},
            {"email": "leader@example.org", "role": "MEMBER", "id": "lead-id"},
        ]
        # Extra addresses Google resolves to a roster member (same account).
        self.aliases: dict[str, str] = {}
        # Addresses whose insert Google rejects as an existing member.
        self.insert_conflicts: set[str] = set()

    def list(self, **kwargs):
        """Record the list call and return the current group roster fixture."""
        self.calls.append(("list", kwargs))
        group_key = str(kwargs.get("groupKey", ""))
        if group_key in self.list_errors_by_group:
            return Request(exc=self.list_errors_by_group[group_key])
        if self.list_error is not None:
            return Request(exc=self.list_error)
        return Request({"members": list(self.roster)})

    def insert(self, **kwargs):
        """Record an add-member call; answer 409 for configured conflicts."""
        self.calls.append(("insert", kwargs))
        if kwargs["body"]["email"] in self.insert_conflicts:
            return Request(exc=GoogleAPIError(409, "Member already exists."))
        return Request()

    def update(self, **kwargs):
        """Record a member-update call and return an empty request."""
        self.calls.append(("update", kwargs))
        return Request()

    def delete(self, **kwargs):
        """Record a remove-member call and return an empty request."""
        self.calls.append(("delete", kwargs))
        return Request()

    def get(self, **kwargs):
        """Record a member lookup and answer like Google for the roster.

        A roster email, id, or registered alias returns that roster member;
        anything else is Google's 404 for a non-member.
        """
        self.calls.append(("get", kwargs))
        key = str(kwargs["memberKey"]).lower()
        key = self.aliases.get(key, key)
        for member in self.roster:
            if key in (member["email"], member["id"]):
                return Request(dict(member))
        return Request(exc=GoogleAPIError(404, "Resource Not Found: memberKey"))


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


def _member_call_names(admin: AdminService, *, reads: bool = True) -> list[str]:
    """Return the fake members-resource call names, optionally without lookups.

    Plan-time ``get`` lookups resolve unmatched addresses (#2389); tests that
    only care about roster listing and writes pass ``reads=False``.
    """
    return [call[0] for call in admin._members.calls if reads or call[0] != "get"]


def test_duplicate_add_is_satisfied_without_error_or_warning(caplog):
    """Google's "already a member" answer to an add is not a failure (#2389).

    The address is on the same Google account as a current member, so the add
    is already satisfied: no exception, no warning, and it is not reported as
    an applied change.
    """

    class DuplicateMembers:
        """Reject an alias insert and resolve it to the existing primary email."""

        def insert(self, **_kwargs):
            """Return Google's duplicate-member failure."""
            return Request(exc=GoogleAPIError(409, "Member already exists."))

        def get(self, **kwargs):
            """Resolve the attempted member alias to its canonical address."""
            assert kwargs == {
                "groupKey": "ministry@example.org",
                "memberKey": "person@me.com",
            }
            return Request({"email": "person@gmail.com"})

    service = SimpleNamespace(members=lambda: DuplicateMembers())
    log = logging.getLogger("test.duplicate-add")

    with caplog.at_level(logging.DEBUG):
        applied = apply_actions(
            service,
            "ministry@example.org",
            [SyncAction(action="add", email="person@me.com", role="MEMBER")],
            log=log,
        )

    assert applied == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert (
        "Google Group ministry@example.org already has person@me.com as a member "
        "(as person@gmail.com); nothing to add"
    ) in caplog.messages
    assert not any("Applied Google Group add" in item for item in caplog.messages)


def test_duplicate_add_survives_failed_canonical_member_lookup(caplog):
    """A duplicate add stays satisfied even when the diagnostic lookup fails."""

    class DuplicateMembers:
        """Reject both the insert and the subsequent diagnostic lookup."""

        def insert(self, **_kwargs):
            """Return Google's duplicate-member failure."""
            return Request(exc=GoogleAPIError(409, "Member already exists."))

        def get(self, **_kwargs):
            """Simulate Google being unable to resolve the attempted alias."""
            return Request(exc=GoogleAPIError(404, "Member not found."))

    service = SimpleNamespace(members=lambda: DuplicateMembers())

    with caplog.at_level(logging.DEBUG):
        applied = apply_actions(
            service,
            "ministry@example.org",
            [
                SyncAction(action="add", email="person@me.com", role="MEMBER"),
            ],
            log=logging.getLogger("test.duplicate-add"),
        )

    assert applied == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert (
        "Google Group ministry@example.org already has person@me.com as a member; "
        "nothing to add"
    ) in caplog.messages


class OneAccountMembers:
    """Stateful fake: two addresses on one Google account, not yet a member.

    The first insert of either address adds the account; any later insert is
    Google's 409. Lookups by either address find the account once it is a
    member, and role updates change its role.
    """

    addresses = {"pat@gmail.com", "pat@example.org"}

    def __init__(self):
        self.role: str | None = None
        self.calls = []

    def insert(self, **kwargs):
        """Add the account, or answer 409 when it is already a member."""
        self.calls.append(("insert", kwargs["body"]))
        assert kwargs["body"]["email"] in self.addresses
        if self.role is not None:
            return Request(exc=GoogleAPIError(409, "Member already exists."))
        self.role = kwargs["body"]["role"]
        return Request()

    def get(self, **kwargs):
        """Return the account's member record, or 404 before it is added."""
        self.calls.append(("get", kwargs["memberKey"]))
        if self.role is None or kwargs["memberKey"] not in self.addresses:
            return Request(exc=GoogleAPIError(404, "Resource Not Found"))
        return Request({"id": "pat-id", "email": "pat@gmail.com", "role": self.role})

    def update(self, **kwargs):
        """Record and apply a role change."""
        self.calls.append(("update", kwargs["memberKey"], kwargs["body"]))
        self.role = kwargs["body"]["role"]
        return Request()


@pytest.mark.parametrize("owner_first", [False, True])
def test_duplicate_add_still_applies_the_owner_role(owner_first, caplog):
    """Membership and role are independent (#2389): when a leader address
    lands on an account another address already added, the account is
    promoted to owner; a member address never demotes it."""
    members = OneAccountMembers()
    service = SimpleNamespace(members=lambda: members)
    adds = [
        SyncAction(action="add", email="pat@gmail.com", role="MEMBER"),
        SyncAction(action="add", email="pat@example.org", role="OWNER"),
    ]
    if owner_first:
        adds.reverse()

    with caplog.at_level(logging.DEBUG):
        applied = apply_actions(
            service, "chairs@example.org", adds, log=logging.getLogger("test.dup")
        )

    assert members.role == "OWNER"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    if owner_first:
        assert [(a.action, a.email) for a in applied] == [("add", "pat@example.org")]
        assert not [call for call in members.calls if call[0] == "update"]
    else:
        assert [(a.action, a.email, a.role, a.group_member_id) for a in applied] == [
            ("add", "pat@gmail.com", "MEMBER", None),
            ("change_role", "pat@example.org", "OWNER", "pat-id"),
        ]
        assert ("update", "pat-id", {"role": "OWNER"}) in members.calls


def test_duplicate_owner_add_warns_when_member_cannot_be_found(caplog):
    """If Google says "already a member" but cannot look the member up, the
    owner role cannot be applied, so that one case is a warning."""

    class UnresolvableMembers:
        """Reject the insert as a duplicate and fail every lookup."""

        def insert(self, **_kwargs):
            """Return Google's duplicate-member failure."""
            return Request(exc=GoogleAPIError(409, "Member already exists."))

        def get(self, **_kwargs):
            """Simulate a lookup that fails after the duplicate add."""
            return Request(exc=GoogleAPIError(404, "Resource Not Found"))

    service = SimpleNamespace(members=lambda: UnresolvableMembers())

    with caplog.at_level(logging.DEBUG):
        applied = apply_actions(
            service,
            "chairs@example.org",
            [SyncAction(action="add", email="pat@example.org", role="OWNER")],
            log=logging.getLogger("test.dup-owner"),
        )

    assert applied == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [
        "Google Group chairs@example.org already has pat@example.org as a member, "
        "but Google could not look that member up (HTTP 404), so it could not be "
        "made an owner; set its role in Google Groups"
    ]


def test_duplicate_owner_add_for_existing_owner_changes_nothing(caplog):
    """An owner add for an account that is already an owner is satisfied."""

    class OwnerMembers:
        """Reject the insert as a duplicate; the account is already an owner."""

        def insert(self, **_kwargs):
            """Return Google's duplicate-member failure."""
            return Request(exc=GoogleAPIError(409, "Member already exists."))

        def get(self, **_kwargs):
            """Return the existing owner record."""
            return Request({"id": "pat-id", "email": "pat@gmail.com", "role": "OWNER"})

    service = SimpleNamespace(members=lambda: OwnerMembers())

    with caplog.at_level(logging.DEBUG):
        applied = apply_actions(
            service,
            "chairs@example.org",
            [SyncAction(action="add", email="pat@example.org", role="OWNER")],
            log=logging.getLogger("test.dup-owner"),
        )

    assert applied == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_nonduplicate_add_error_identifies_attempted_address_without_lookup():
    """Every add failure has context, while only duplicate errors trigger lookup."""

    class ForbiddenMembers:
        """Reject an insert and deliberately provide no member lookup method."""

        def insert(self, **_kwargs):
            """Return a non-duplicate authorization failure."""
            return Request(exc=GoogleAPIError(403, "Not authorized."))

    service = SimpleNamespace(members=lambda: ForbiddenMembers())

    with pytest.raises(GoogleAPIError) as exc_info:
        apply_actions(
            service,
            "ministry@example.org",
            [SyncAction(action="add", email="person@example.org", role="MEMBER")],
        )

    message = str(exc_info.value)
    assert "HTTP 403" in message
    assert "failed to add 'person@example.org'" in message
    assert "Google Group 'ministry@example.org'" in message
    assert "Not authorized." in message


def test_apply_actions_runs_all_deletes_before_other_actions():
    """Deletes are stable-partitioned ahead of adds and role changes."""
    admin = AdminService()
    actions = [
        SyncAction(action="add", email="first-new@example.org", role="MEMBER"),
        SyncAction(
            action="change_role",
            email="leader@example.org",
            role="OWNER",
            group_member_id="leader-id",
        ),
        SyncAction(
            action="delete",
            email="first-old@example.org",
            group_member_id="first-old-id",
        ),
        SyncAction(action="add", email="second-new@example.org", role="MEMBER"),
        SyncAction(
            action="delete",
            email="second-old@example.org",
            group_member_id="second-old-id",
        ),
    ]

    apply_actions(admin, "ministry@example.org", actions)

    assert [call[0] for call in admin._members.calls] == [
        "delete",
        "delete",
        "insert",
        "update",
        "insert",
    ]
    assert [
        call[1].get("memberKey") for call in admin._members.calls if call[0] == "delete"
    ] == ["first-old-id", "second-old-id"]


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


def test_sync_config_rejects_unknown_group_key():
    """Misspelled group guardrail keys fail instead of using defaults."""
    with pytest.raises(ConfigError, match="unsupported key"):
        sync_config_from_yaml(
            {
                "sync": {
                    "groups": [
                        {
                            "group": "group@example.org",
                            "static_members": [{"email": "ann@example.org"}],
                            "max_removal_fractions": 0.1,
                        }
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


def test_google_group_credentials_can_skip_settings_scope(tmp_path, monkeypatch):
    """Notification-free syncs do not require the Groups Settings scope."""
    calls = []

    def fake_load(path, *, scopes, subject):
        """Capture scopes for assertion instead of loading credentials."""
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
        include_settings_scope=False,
    )

    assert calls[0][1] == [
        "https://www.googleapis.com/auth/admin.directory.group.member"
    ]


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


def test_configured_leader_roles_control_ministry_owner_mapping():
    """Parishes can choose which ParishSoft ministry roles become owners."""
    data = parishsoft_data()
    data.members[1]["py ministries"]["Readers"]["role"] = "Coordinator"
    config = sync_config_from_yaml(
        {
            "sync": {
                "leader_roles": ["Coordinator"],
                "groups": [
                    {
                        "group": "group@example.org",
                        "ministries": ["Readers"],
                    }
                ],
            }
        }
    )

    desired = desired_members(
        data,
        config.groups[0],
        leader_roles=config.leader_roles,
    )

    assert [(item.email, item.leader) for item in desired] == [
        ("leader@example.org", True)
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


def test_sync_group_plans_without_mutating_google_group():
    """The compatibility helper returns actions but leaves writes to apply phase."""
    group = GroupSync(group="group@example.org", notify=(), workgroups=("Movers",))
    admin = AdminService()

    actions = sync_group(
        admin,
        SettingsService(),
        None,
        parishsoft_data(),
        SyncConfig(groups=(group,), sender=None, google_mail_domains=frozenset()),
        group,
        dry_run=False,
        log=logging.getLogger("test"),
    )

    assert actions
    assert _member_call_names(admin) == ["list", "get"]


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


def test_all_ministry_chairs_staff_owner_is_decided_per_address():
    """Owner promotion checks each added address's own domain (#2389)."""
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

    assert [
        (item.email, item.leader) for item in desired if item.names == ["Pat Chair"]
    ] == [("pat@gmail.com", False), ("staff@example.org", True)]


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
    assert (
        normalize_email("First.Last+tag@example.org", frozenset({"example.org"}))
        == "first.last@example.org"
    )


def test_google_group_refuses_large_delete_batch_by_default():
    """A mostly destructive non-empty group sync requires guardrail changes."""
    group = GroupSync(
        group="big@example.org",
        notify=(),
    )
    actions = [
        SimpleNamespace(action="delete", email=f"old{index}@example.org")
        for index in range(40)
    ]
    current = [{"email": f"old{index}@example.org"} for index in range(40)]

    with pytest.raises(ConfigError, match="would remove 40 of 40"):
        validate_large_removal_guard(group, actions, current)


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


def test_desired_members_include_every_valid_address(caplog):
    """Every plain address of a member is desired (#2389).

    A fragment that is not a plain address is skipped with a warning naming
    the member, instead of being sent to Google.
    """
    data = parishsoft_data()
    data.members[2]["py emailAddresses"] = [
        "bob.mover+tag@gmail.com",
        "bob@work.example.net",
        "bad, address@example.net",
    ]
    config = sync_config_from_yaml(
        {"sync": {"groups": [{"group": "group@example.org", "workgroups": ["Movers"]}]}}
    )
    log = logging.getLogger("test.desired-every-address")

    with caplog.at_level(logging.WARNING, logger=log.name):
        desired = desired_members(data, config.groups[0], log=log)

    assert [(item.email, item.leader) for item in desired] == [
        ("bob.mover+tag@gmail.com", False),
        ("bob@work.example.net", False),
    ]
    assert caplog.messages == [
        "Skipping invalid ParishSoft email address(es) for member DUID 2: "
        "bad, address@example.net"
    ]


def test_desired_members_shared_address_lists_every_member():
    """An address several members share is one desired entry naming them all."""
    data = parishsoft_data()
    data.members[2]["py emailAddresses"] = [
        "bob.mover+tag@gmail.com",
        "family@example.net",
    ]
    data.members[3] = {
        "memberDUID": 3,
        "firstName": "Carol",
        "lastName": "Mover",
        "py friendly name FL": "Carol Mover",
        "py emailAddresses": ["family@example.net"],
        "py ministries": {},
        "py workgroups": {"Movers": {"name": "Movers"}},
    }
    config = sync_config_from_yaml(
        {"sync": {"groups": [{"group": "group@example.org", "workgroups": ["Movers"]}]}}
    )

    desired = desired_members(data, config.groups[0])

    shared = next(item for item in desired if item.email == "family@example.net")
    assert shared.names == ["Bob Mover", "Carol Mover"]


def test_compute_actions_matches_same_account_address_by_lookup():
    """An address Google resolves to a current member is neither added nor
    leaves that member to be deleted; any leader address makes it an owner."""
    lookups = []

    def find_member(email):
        """Resolve the second address to the current member's id."""
        lookups.append(email)
        if email == "ann@home.example.net":
            return {"email": "leader@example.org", "id": "lead-id"}
        return None

    actions = compute_actions(
        [
            DesiredMember("leader@example.org", False, ["Ann Leader"]),
            DesiredMember("ann@home.example.net", True, ["Ann Leader"]),
            DesiredMember("new@example.org", False, ["New Person"]),
        ],
        [{"email": "leader@example.org", "role": "MEMBER", "id": "lead-id"}],
        frozenset(),
        find_member=find_member,
    )

    assert lookups == ["ann@home.example.net", "new@example.org"]
    assert [
        (item.action, item.email, item.role, item.group_member_id) for item in actions
    ] == [
        ("change_role", "leader@example.org", "OWNER", "lead-id"),
        ("add", "new@example.org", "MEMBER", None),
    ]
    assert actions[0].desired is not None
    assert actions[0].desired.names == ["Ann Leader"]


def test_compute_actions_keeps_member_listed_under_another_canonical_address():
    """When Google lists a member under an address ParishSoft does not have,
    the lookup keeps the plan from deleting and re-adding that person."""

    def find_member(email):
        """Resolve both ParishSoft addresses to the canonical Google address."""
        if email in {"a@example.net", "b@example.net"}:
            return {"email": "c@gmail.com", "id": "c-id"}
        return None

    actions = compute_actions(
        [
            DesiredMember("a@example.net", False, ["Pat"]),
            DesiredMember("b@example.net", False, ["Pat"]),
        ],
        [{"email": "c@gmail.com", "role": "MEMBER", "id": "c-id"}],
        frozenset({"gmail.com"}),
        find_member=find_member,
    )

    assert actions == []


def test_sync_google_group_main_handles_members_with_several_addresses(
    tmp_path, monkeypatch
):
    """End to end: same-account addresses cause no add, a new additional
    address is added like any other, and an add Google answers "already a
    member" is silent and left out of the notification (#2389)."""
    admin = AdminService()
    admin._members.aliases["ann@home.example.net"] = "lead-id"
    admin._members.insert_conflicts.add("bob@alt.example.net")
    settings = SettingsService()
    email = EmailProvider()
    log_file = tmp_path / "sync-google-group.log"
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )
    data = parishsoft_data()
    data.members[1]["py emailAddresses"] = [
        "leader@example.org",
        "ann@home.example.net",
    ]
    data.members[2]["py emailAddresses"] = [
        "bob.mover+tag@gmail.com",
        "bob@work.example.net",
        "bob@alt.example.net",
    ]

    assert (
        sync_google_group_main(
            ["--config", str(write_config(tmp_path)), "--log-file", str(log_file)],
            loader=lambda _client, **_kwargs: data,
            service_factory=lambda _config: (admin, settings),
            email_provider=email,
        )
        == 0
    )

    inserts = [call[1]["body"] for call in admin._members.calls if call[0] == "insert"]
    assert {"email": "ann@home.example.net", "role": "OWNER"} not in inserts
    assert {"email": "bob@work.example.net", "role": "MEMBER"} in inserts
    assert {"email": "bob@alt.example.net", "role": "MEMBER"} in inserts
    text = email.sent[0][0].text
    assert "bob@work.example.net" in text
    assert "bob@alt.example.net" not in text
    assert "ann@home.example.net" not in text
    log_text = log_file.read_text(encoding="utf-8")
    assert "already has bob@alt.example.net as a member" in log_text
    assert "WARNING" not in log_text


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

    assert _member_call_names(admin, reads=False) == ["list"]


def test_sync_google_group_live_noop_does_not_build_email_provider(
    tmp_path,
    monkeypatch,
):
    """Notification credentials are not required when a live run has no email."""
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            """      ministries:
        - Readers
      workgroups:
        - Movers
      static_members:
        - email: static@example.org
          leader: false
""",
            """      static_members:
        - email: leader@example.org
          leader: false
        - email: old@example.org
          leader: false
""",
        ),
        encoding="utf-8",
    )
    admin = AdminService()
    settings = SettingsService()
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.provider_from_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConfigError("email loaded")),
    )

    assert (
        sync_google_group_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
        )
        == 0
    )

    assert _member_call_names(admin, reads=False) == ["list"]


def test_sync_google_group_live_noop_does_not_build_settings_scope(
    tmp_path,
    monkeypatch,
):
    """A no-op live run with notify configured does not need settings scope."""
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            """      ministries:
        - Readers
      workgroups:
        - Movers
      static_members:
        - email: static@example.org
          leader: false
""",
            """      static_members:
        - email: leader@example.org
          leader: false
        - email: old@example.org
          leader: false
""",
        ),
        encoding="utf-8",
    )
    admin = AdminService()
    scope_calls = []
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.load_google_credentials",
        lambda _config, *, base_dir=None, include_settings_scope=True: (
            scope_calls.append(include_settings_scope) or object()
        ),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.build_admin_directory_service",
        lambda _credentials: admin,
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.build_groups_settings_service",
        lambda _credentials: (_ for _ in ()).throw(AssertionError("settings built")),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.provider_from_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConfigError("email loaded")),
    )

    assert (
        sync_google_group_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
        )
        == 0
    )

    assert scope_calls == [False]
    assert _member_call_names(admin, reads=False) == ["list"]


def test_sync_google_group_notification_failure_does_not_skip_later_writes(
    tmp_path,
    monkeypatch,
):
    """Notification failures are reported after every group plan is applied."""
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8")
        + """
    - group: second@example.org
      static_members:
        - email: second@example.org
          leader: false
""",
        encoding="utf-8",
    )
    admin = AdminService()
    settings = SettingsService()

    class FailingEmailProvider:
        """Email provider that fails when the first notification is sent."""

        def send(self, *_args, **_kwargs):
            """Raise a user-facing delivery error."""
            raise ConfigError("smtp failed")

    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_google_group_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
            email_provider=FailingEmailProvider(),
        )
        == 2
    )

    assert any(
        call[0] in {"insert", "delete", "update"}
        and call[1]["groupKey"] == "second@example.org"
        for call in admin._members.calls
    )


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
    assert "ERROR pk.pk_sync_ps_to_ggroup" in error
    assert "Configured Google Group was not found" in error
    assert "sync.groups[].group" in error
    assert _member_call_names(admin, reads=False) == ["list"]


def test_sync_google_group_preflights_all_groups_before_writes(
    tmp_path,
    monkeypatch,
):
    """A later group failure prevents earlier group mutations."""
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8")
        + """
    - group: missing@example.org
      static_members:
        - email: somebody@example.org
""",
        encoding="utf-8",
    )
    admin = AdminService()
    admin._members.list_errors_by_group["missing@example.org"] = GoogleAPIError(
        404,
        "group not found",
    )
    settings = SettingsService()
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_ggroup.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_google_group_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            service_factory=lambda _config: (admin, settings),
            email_provider=EmailProvider(),
        )
        == 2
    )

    assert _member_call_names(admin, reads=False) == ["list", "list"]


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
    assert "ERROR pk.pk_sync_ps_to_ggroup" in error
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

    assert _member_call_names(admin, reads=False) == ["list"]
    assert settings._groups.calls == [
        (
            "get",
            {
                "groupUniqueId": "group@example.org",
                "fields": "whoCanPostMessage",
            },
        )
    ]


def test_sync_google_group_without_notifications_skips_settings_api(
    tmp_path,
    monkeypatch,
):
    """Membership-only syncs do not require Groups Settings API permission."""
    admin = AdminService()
    settings = SettingsService()
    settings._groups.get_error = GoogleAPIError(403, "settings denied")
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "      notify:\n        - admin@example.org\n",
            "",
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
        == 0
    )

    assert any(call[0] == "insert" for call in admin._members.calls)
    assert settings._groups.calls == []


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
    assert "ERROR pk.pk_sync_ps_to_ggroup" in error
    assert "Configured ParishSoft selector matched no ministries" in error
    assert "sync.groups[].selectors[].ministry_pattern" in error
    assert admin._members.calls == []
