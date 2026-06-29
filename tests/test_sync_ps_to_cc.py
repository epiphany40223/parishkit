from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from parishkit.config import ConfigError
from parishkit.parishsoft import ParishSoftData
from parishkit.pk_sync_ps_to_cc import (
    DEFAULT_UNSUBSCRIBED_REPORT_STATE,
    CCAction,
    CCSyncConfig,
    CCSyncMapping,
    CCUnsubscribedReportConfig,
    cc_sync_config_from_yaml,
    compute_all_actions,
    constant_contact_client,
    detect_name_mismatches,
    ensure_unsubscribed_report_state_writable,
    filter_unsubscribed,
    load_cc_data,
    name_update_candidate_emails,
    parishsoft_members_by_email,
    resolve_desired_state,
    send_notifications,
    validate_large_removal_guard,
    validate_non_empty_desired_state,
)
from parishkit.pk_sync_ps_to_cc import (
    main as sync_ps_to_cc_main,
)


class CCClient:
    """Fake Constant Contact client that records calls and returns fixtures.

    Reads are served from the cc_lists/cc_contacts fixtures; writes are
    captured in self.calls so tests can assert on what would be sent.
    """

    def __init__(
        self,
        *,
        lists: list[dict] | None = None,
        contacts: list[dict] | None = None,
    ):
        """Initialize call recording and optional list/contact fixtures."""
        self.calls = []
        self.lists = lists
        self.contacts = contacts

    def get_all(self, endpoint, field, **kwargs):
        """Record the read and return the matching list or contact fixture."""
        self.calls.append(("get_all", endpoint, field, kwargs))
        if endpoint == "contact_lists":
            return cc_lists() if self.lists is None else self.lists
        return cc_contacts() if self.contacts is None else self.contacts

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


class FailingEmailProvider(EmailProvider):
    """Fake email provider that fails for selected message subjects."""

    def __init__(self, fail_subject_contains: str):
        """Store the subject fragment that should raise an OSError."""
        super().__init__()
        self.fail_subject_contains = fail_subject_contains

    def send(self, message, *, dry_run=False):
        """Record successful messages and fail matching subjects."""
        if self.fail_subject_contains in message.subject:
            raise OSError(f"forced email failure for {message.subject}")
        return super().send(message, dry_run=dry_run)


def write_config(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    unsubscribed_report: bool = False,
    unsubscribed_report_day: str | None = None,
    state_file: str | Path | None = None,
) -> Path:
    """Write a complete contacts YAML config under tmp_path.

    Produces one workgroup-to-list mapping with notifications enabled; the
    dry_run flag lets tests toggle write-skipping behavior.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    api_key = tmp_path / "parishsoft-api-key.txt"
    api_key.write_text("key", encoding="utf-8")
    config = tmp_path / "config.yaml"
    state_file_value = state_file or (tmp_path / "unsubscribed-report-state.json")
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
  unsubscribed_report:
    enabled: {str(unsubscribed_report).lower()}
    day_of_week: {unsubscribed_report_day or "null"}
    time: "02:00"
    window_minutes: 60
    state_file: {state_file_value}
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


def cc_contacts_without_sync_actions(*, ann_lists: list[str] | None = None):
    """Return contacts where only Bob's unsubscribed status needs reporting."""
    ann_memberships = ann_lists or ["list-1"]
    return [
        {
            "contact_id": "contact-ann",
            "email_address": {
                "address": "ann@example.org",
                "permission_to_send": "implicit",
            },
            "first_name": "Ann",
            "last_name": "Smith",
            "list_memberships": ann_memberships,
        },
        {
            "contact_id": "contact-bob",
            "email_address": {
                "address": "bob@example.org",
                "permission_to_send": "unsubscribed",
            },
            "first_name": "Bob",
            "last_name": "Jones",
            "list_memberships": [],
        },
    ]


def cc_lists_without_sync_actions():
    """Return a fixture where Ann is already on both report target lists."""
    return [
        {
            "list_id": "list-1",
            "name": "Newsletter",
            "CONTACTS": {"ann@example.org": {}},
        },
        {
            "list_id": "list-2",
            "name": "Second Newsletter",
            "CONTACTS": {"ann@example.org": {}},
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
    assert not config.unsubscribed_report.enabled
    assert config.unsubscribed_report.state_file == DEFAULT_UNSUBSCRIBED_REPORT_STATE


def test_cc_sync_config_rejects_duplicate_target_lists():
    """One Constant Contact list must not be reconciled by multiple mappings."""
    with pytest.raises(ConfigError, match="target_list values must be unique"):
        cc_sync_config_from_yaml(
            {
                "sync": {
                    "lists": [
                        {
                            "source_workgroup": "Newsletter WG",
                            "target_list": "Newsletter",
                        },
                        {
                            "source_workgroup": "Second WG",
                            "target_list": "newsletter",
                        },
                    ]
                }
            }
        )


def test_cc_sync_config_rejects_unknown_mapping_key():
    """Misspelled list mapping keys fail instead of using default guardrails."""
    with pytest.raises(ConfigError, match="unsupported key"):
        cc_sync_config_from_yaml(
            {
                "sync": {
                    "lists": [
                        {
                            "source_workgroup": "Newsletter WG",
                            "target_list": "Newsletter",
                            "max_removal_fractions": 0.1,
                        }
                    ]
                }
            }
        )


def test_cc_sync_config_requires_sender_for_notifications():
    """Notification recipients require a sender before any sync writes happen."""
    with pytest.raises(ConfigError, match="sync.notifications.sender is required"):
        cc_sync_config_from_yaml(
            {
                "sync": {
                    "notifications": {},
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


def test_cc_sync_config_accepts_unsubscribed_report_schedule(tmp_path):
    """Verify YAML can schedule the standalone unsubscribed report."""
    config = cc_sync_config_from_yaml(
        {
            "sync": {
                "notifications": {"sender": "no-reply@example.org"},
                "unsubscribed_report": {
                    "enabled": True,
                    "day_of_week": "sunday",
                    "time": "03:15",
                    "window_minutes": 30,
                    "state_file": str(tmp_path / "state.json"),
                },
                "lists": [
                    {
                        "source_workgroup": "Newsletter WG",
                        "target_list": "Newsletter",
                    }
                ],
            }
        }
    )

    assert config.unsubscribed_report.enabled
    assert config.unsubscribed_report.day_of_week == 6
    assert config.unsubscribed_report.time.hour == 3
    assert config.unsubscribed_report.time.minute == 15
    assert config.unsubscribed_report.window_minutes == 30
    assert config.unsubscribed_report.state_file == tmp_path / "state.json"


def test_cc_sync_config_resolves_relative_report_state_path(tmp_path):
    """Relative report state paths resolve against the config file directory."""
    config = cc_sync_config_from_yaml(
        {
            "sync": {
                "unsubscribed_report": {
                    "enabled": True,
                    "state_file": "run/unsubscribed-report-state.json",
                },
                "lists": [
                    {
                        "source_workgroup": "Newsletter WG",
                        "target_list": "Newsletter",
                    }
                ],
            }
        },
        base_dir=tmp_path,
    )

    assert config.unsubscribed_report.state_file == (
        tmp_path / "run" / "unsubscribed-report-state.json"
    )


def test_cc_sync_config_rejects_missing_lists():
    """Verify config parsing fails when the required `lists` key is absent."""
    with pytest.raises(ConfigError, match="lists"):
        cc_sync_config_from_yaml({"sync": {}})


def test_constant_contact_client_resolves_relative_credential_paths(
    tmp_path,
    monkeypatch,
):
    """Relative Constant Contact credential paths resolve against the config dir."""
    calls = []

    def fake_load_client_id(path):
        """Capture the resolved client-id path."""
        calls.append(("client_id", path))
        return {"endpoints": {"api": "https://api.example"}}

    def fake_get_access_token(path, client_id, **kwargs):
        """Capture the resolved token path."""
        calls.append(("access_token", path, client_id, kwargs))
        return {"access_token": "token"}

    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.load_client_id",
        fake_load_client_id,
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.get_access_token",
        fake_get_access_token,
    )

    constant_contact_client(
        {
            "constant_contact": {
                "client_id_file": "credentials/cc-client.json",
                "access_token_file": "credentials/cc-token.json",
            }
        },
        base_dir=tmp_path,
    )

    assert calls[0] == ("client_id", tmp_path / "credentials" / "cc-client.json")
    assert calls[1][0] == "access_token"
    assert calls[1][1] == tmp_path / "credentials" / "cc-token.json"
    assert calls[1][3]["allow_refresh"] is True


def test_load_cc_data_filters_soft_deleted_contacts():
    """Soft-deleted Constant Contact records are not reconciled as live contacts."""
    deleted = {
        "contact_id": "deleted-ann",
        "deleted_at": "2026-01-01T00:00:00Z",
        "email_address": {
            "address": "ann@example.org",
            "permission_to_send": "implicit",
        },
        "list_memberships": ["list-1"],
    }
    lists, contacts = load_cc_data(CCClient(contacts=[deleted, *cc_contacts()]))

    assert set(lists[0]["CONTACTS"]) == {"bob@example.org", "old@example.org"}
    assert all(contact["contact_id"] != "deleted-ann" for contact in contacts)


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


def test_cc_sync_refuses_empty_desired_state_with_current_contacts():
    """A zero-member source cannot unsubscribe a populated list without opt-in."""
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

    with pytest.raises(ConfigError, match="allow_empty"):
        validate_non_empty_desired_state(config, [set()], cc_lists())


def test_cc_sync_refuses_large_unsubscribe_batch_by_default():
    """A mostly destructive non-empty sync requires explicit guardrail changes."""
    config = CCSyncConfig(
        mappings=(
            CCSyncMapping(source_workgroup="Newsletter WG", target_list="Newsletter"),
        )
    )
    lists = [
        {
            "name": "Newsletter",
            "CONTACTS": {f"old{index}@example.org": {} for index in range(40)},
        }
    ]
    actions = [
        CCAction("unsubscribe", email, 0, f"Unsubscribe {email}")
        for email in lists[0]["CONTACTS"]
    ]

    with pytest.raises(ConfigError, match="would remove 40 of 40"):
        validate_large_removal_guard(config, actions, lists)


def test_sync_ps_to_cc_all_desired_unsubscribed_does_not_trip_empty_guard(
    tmp_path,
    monkeypatch,
):
    """A non-empty source that filters to opt-outs still completes the sync."""
    data = parishsoft_data()
    data.member_workgroup_memberships[10]["membership"] = [{"py member duid": 2}]
    cc = CCClient()
    email = EmailProvider()
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client, **_kwargs: data,
            cc_factory=lambda _config: cc,
            email_provider=email,
        )
        == 0
    )

    assert any(
        call[0] == "put" and call[1] == "contacts/contact-old" for call in cc.calls
    )


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


def test_name_updates_are_scoped_to_configured_lists_and_desired_emails():
    """Unrelated Constant Contact lists are outside update_names scope."""
    config = cc_sync_config_from_yaml(
        {
            "sync": {
                "lists": [
                    {
                        "source_workgroup": "Newsletter WG",
                        "target_list": "Newsletter",
                    }
                ],
            }
        }
    )
    desired = [{"ann@example.org"}]
    contacts = {item["email_address"]["address"]: item for item in cc_contacts()}
    contacts["ann@example.org"]["PS MEMBERS"] = [parishsoft_data().members[1]]
    contacts["other@example.org"] = {
        "email_address": {"address": "other@example.org"},
        "first_name": "Wrong",
        "last_name": "Name",
        "PS MEMBERS": [
            {
                "memberDUID": 99,
                "firstName": "Other",
                "lastName": "Member",
                "py friendly name FL": "Other Member",
            }
        ],
    }

    candidates = name_update_candidate_emails(config, desired, cc_lists())
    actions = detect_name_mismatches(
        contacts,
        update_names=True,
        candidate_emails=candidates,
    )

    assert [action.email for action in actions] == ["ann@example.org"]


def test_sync_ps_to_cc_main_writes_constant_contact_and_email(tmp_path, monkeypatch):
    """Verify a live run posts/puts to Constant Contact and emails admins.

    With dry_run off, main must create and update contacts (post + put) and
    send the regular sync summary to the configured notification address.
    """
    cc = CCClient()
    email = EmailProvider()
    loader_calls = []
    log_file = tmp_path / "sync-cc.log"
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
            ["--config", str(write_config(tmp_path)), "--log-file", str(log_file)],
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
    assert email.sent[0][0].subject == "Constant Contact sync update: Newsletter"
    assert "Constant Contact Sync Update: Newsletter" in (email.sent[0][0].html or "")
    assert "ParishSoft Member Workgroup:" in (email.sent[0][0].html or "")
    assert "Actions Performed" in (email.sent[0][0].html or "")
    assert "update name" in (email.sent[0][0].html or "")
    assert "Filtered Unsubscribed Contacts" in (email.sent[0][0].html or "")
    assert "<th" in (email.sent[0][0].html or "")
    assert "Constant Contact sync operation completed successfully" in (
        log_file.read_text(encoding="utf-8")
    )


def test_sync_ps_to_cc_sends_due_unsubscribed_report_once(tmp_path, monkeypatch):
    """Verify the standalone unsubscribed report is daily and state-backed."""
    cc = CCClient()
    email = EmailProvider()
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="sunday",
    )
    now = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=now,
        )
        == 0
    )
    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=now,
        )
        == 0
    )

    report_messages = [
        message
        for message, _dry_run in email.sent
        if message.subject.startswith("Constant Contact unsubscribed contacts report")
    ]
    assert len(report_messages) == 1
    assert report_messages[0].to == ("admin@example.org",)
    assert "Bob Jones" in (report_messages[0].text or "")
    assert "bob@example.org" in (report_messages[0].html or "")
    assert "2026-06-28" in (tmp_path / "unsubscribed-report-state.json").read_text(
        encoding="utf-8"
    )


def test_sync_ps_to_cc_relative_report_state_uses_config_dir(
    tmp_path,
    monkeypatch,
):
    """Cron cwd changes must not move the report state file."""
    cc = CCClient(contacts=cc_contacts_without_sync_actions())
    email = EmailProvider()
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="sunday",
        state_file="run/unsubscribed-report-state.json",
    )
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    now = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=now,
        )
        == 0
    )

    assert (tmp_path / "run" / "unsubscribed-report-state.json").exists()
    assert not (cwd / "run" / "unsubscribed-report-state.json").exists()


def test_sync_ps_to_cc_scheduled_report_suppresses_regular_unsubscribed_notice(
    tmp_path,
    monkeypatch,
):
    """Scheduled reports prevent repeated regular emails for only unsubscribed data."""
    cc = CCClient(contacts=cc_contacts_without_sync_actions())
    email = EmailProvider()
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="sunday",
    )
    now = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=now,
        )
        == 0
    )
    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=now,
        )
        == 0
    )

    assert [message.subject for message, _dry_run in email.sent] == [
        "Constant Contact unsubscribed contacts report: Newsletter"
    ]


def test_sync_ps_to_cc_scheduled_report_notice_explains_filtered_count(
    tmp_path,
    monkeypatch,
):
    """Regular emails do not claim zero filtered contacts when details move."""
    cc = CCClient()
    email = EmailProvider()
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="monday",
    )
    sunday = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=sunday,
        )
        == 0
    )

    sync_messages = [
        message
        for message, _dry_run in email.sent
        if message.subject == "Constant Contact sync update: Newsletter"
    ]
    assert len(sync_messages) == 1
    assert (
        "Unsubscribed contacts filtered: 1 (handled by scheduled report)"
        in sync_messages[0].text
    )
    assert "Unsubscribed contacts filtered: 0" not in sync_messages[0].text


def test_sync_ps_to_cc_regular_notification_skips_empty_recipients():
    """Mappings without notification recipients do not attempt to send email."""
    provider = EmailProvider()
    config = CCSyncConfig(
        mappings=(
            CCSyncMapping(
                source_workgroup="Newsletter WG",
                target_list="Newsletter",
                notifications=(),
            ),
        ),
        sender="no-reply@example.org",
    )

    send_notifications(
        provider,
        config,
        [CCAction("subscribe", "ann@example.org", 0, "subscribe")],
        [[]],
        {},
        {},
    )

    assert provider.sent == []


def test_sync_ps_to_cc_unsubscribed_report_preflight_preserves_lock_file(tmp_path):
    """The state preflight probes the lock path without replacing it."""
    state = tmp_path / "state.json"
    lock = tmp_path / ".state.json.lock"
    lock.write_text("held by another process", encoding="utf-8")

    ensure_unsubscribed_report_state_writable(
        CCUnsubscribedReportConfig(enabled=True, state_file=state)
    )

    assert lock.read_text(encoding="utf-8") == "held by another process"


def test_sync_ps_to_cc_due_unsubscribed_report_requires_sender(
    tmp_path,
    monkeypatch,
    capsys,
):
    """A due report with recipients but no sender is a config error."""
    cc = CCClient()
    email = EmailProvider()
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="sunday",
    )
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "  notifications:\n    sender: no-reply@example.org\n",
            "  notifications: {}\n",
        ),
        encoding="utf-8",
    )
    now = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=now,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_sync_ps_to_cc" in error
    assert "sync.notifications.sender is required" in error
    assert email.sent == []
    assert cc.calls == []


def test_sync_ps_to_cc_due_unsubscribed_report_requires_recipients(
    tmp_path,
    monkeypatch,
    capsys,
):
    """A due report with filtered contacts but no recipients is a config error."""
    cc = CCClient()
    email = EmailProvider()
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="sunday",
    )
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "      notifications:\n        - admin@example.org\n",
            "      notifications: []\n",
        ),
        encoding="utf-8",
    )
    now = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=now,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_sync_ps_to_cc" in error
    assert "sync.lists[].notifications is required" in error
    assert email.sent == []
    assert [call[0] for call in cc.calls] == ["get_all", "get_all"]


def test_sync_ps_to_cc_due_unsubscribed_report_preflights_state_path(
    tmp_path,
    monkeypatch,
):
    """The report must fail before email if the state path cannot be written."""
    cc = CCClient()
    email = EmailProvider()
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("blocked", encoding="utf-8")
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="sunday",
        state_file=blocked_parent / "state.json",
    )
    now = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=now,
        )
        == 2
    )

    assert email.sent == []
    assert [call[0] for call in cc.calls] == ["get_all", "get_all"]


def test_sync_ps_to_cc_unsubscribed_report_retries_only_unsent_mapping(
    tmp_path,
    monkeypatch,
):
    """Per-list report state prevents duplicate reports after partial failure."""
    cc = CCClient(
        lists=cc_lists_without_sync_actions(),
        contacts=cc_contacts_without_sync_actions(ann_lists=["list-1", "list-2"]),
    )
    first_email = FailingEmailProvider("Second Newsletter")
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="sunday",
    )
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "    - source_workgroup: Newsletter WG\n"
            "      target_list: Newsletter\n"
            "      notifications:\n"
            "        - admin@example.org\n",
            "    - source_workgroup: Newsletter WG\n"
            "      target_list: Newsletter\n"
            "      notifications:\n"
            "        - admin@example.org\n"
            "    - source_workgroup: Newsletter WG\n"
            "      target_list: Second Newsletter\n"
            "      notifications:\n"
            "        - admin@example.org\n",
        ),
        encoding="utf-8",
    )
    now = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=first_email,
            now=now,
        )
        == 2
    )

    first_subjects = [message.subject for message, _dry_run in first_email.sent]
    assert first_subjects == [
        "Constant Contact unsubscribed contacts report: Newsletter"
    ]

    second_email = EmailProvider()
    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=second_email,
            now=now,
        )
        == 0
    )

    second_subjects = [message.subject for message, _dry_run in second_email.sent]
    assert second_subjects == [
        "Constant Contact unsubscribed contacts report: Second Newsletter"
    ]
    state_text = (tmp_path / "unsubscribed-report-state.json").read_text(
        encoding="utf-8"
    )
    assert "Newsletter" in state_text
    assert "Second Newsletter" in state_text
    assert "last_sent_date" in state_text


def test_sync_ps_to_cc_unsubscribed_report_waits_for_configured_weekday(
    tmp_path,
    monkeypatch,
):
    """Verify the standalone report does not send on the wrong weekday."""
    cc = CCClient()
    email = EmailProvider()
    config = write_config(
        tmp_path,
        unsubscribed_report=True,
        unsubscribed_report_day="monday",
    )
    sunday = dt.datetime(
        2026,
        6,
        28,
        2,
        5,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
            now=sunday,
        )
        == 0
    )

    assert not any(
        message.subject.startswith("Constant Contact unsubscribed contacts report")
        for message, _dry_run in email.sent
    )
    assert not (tmp_path / "unsubscribed-report-state.json").exists()


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


def test_sync_ps_to_cc_dry_run_marks_injected_email_provider_dry_run(
    tmp_path,
    monkeypatch,
):
    """Injected email providers still receive dry_run=True in dry-run mode."""
    cc = CCClient()
    email = EmailProvider()
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(write_config(tmp_path, dry_run=True))],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
            email_provider=email,
        )
        == 0
    )

    assert [call[0] for call in cc.calls] == ["get_all", "get_all"]
    assert email.sent
    assert all(dry_run for _message, dry_run in email.sent)


def test_sync_ps_to_cc_live_noop_does_not_build_email_provider(tmp_path, monkeypatch):
    """Notification credentials are not required when a live run has no email."""
    data = parishsoft_data()
    data.member_workgroup_memberships[10]["membership"] = [{"py member duid": 1}]
    cc = CCClient(
        lists=cc_lists_without_sync_actions(),
        contacts=cc_contacts_without_sync_actions(),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.provider_from_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConfigError("email loaded")),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client, **_kwargs: data,
            cc_factory=lambda _config: cc,
        )
        == 0
    )


def test_sync_ps_to_cc_reports_missing_parishsoft_workgroup(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Unknown configured ParishSoft workgroup logs a helpful config error."""
    cc = CCClient()
    config = write_config(tmp_path)
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace("Newsletter WG", "Missing Newsletter WG"),
        encoding="utf-8",
    )
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
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_sync_ps_to_cc" in error
    assert "Configured ParishSoft member workgroup was not found" in error
    assert "sync.lists[].source_workgroup" in error
    assert cc.calls == []


def test_sync_ps_to_cc_reports_missing_constant_contact_list(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Unknown configured Constant Contact list logs a helpful config error."""
    cc = CCClient(lists=[])
    monkeypatch.setattr(
        "parishkit.pk_sync_ps_to_cc.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        sync_ps_to_cc_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            cc_factory=lambda _config: cc,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_sync_ps_to_cc" in error
    assert "Configured Constant Contact list was not found" in error
    assert "sync.lists[].target_list" in error
    assert [call[0] for call in cc.calls] == ["get_all", "get_all"]
