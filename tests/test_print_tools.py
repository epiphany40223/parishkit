from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from parishkit.parishsoft import ParishSoftAPIError, ParishSoftData
from parishkit.pk_print_ps_ministries import main as print_ministries_main
from parishkit.pk_print_ps_ministries import sorted_ministry_names
from parishkit.pk_query_ps_memfam import OMITTED_MEMBERSHIP, find_members_by_name
from parishkit.pk_query_ps_memfam import main as print_member_main


class FakeClient:
    """Stand-in ParishSoft client that records whether validation ran.

    The ``validated`` flag lets tests assert whether the command reached the
    organization-validation step before failing.
    """

    def __init__(self):
        self.validated = False

    def validate_organization(self):
        """Record the validation call and return a fixed organization id."""
        self.validated = True
        return 7


def data() -> ParishSoftData:
    """Build a small in-memory ParishSoft dataset shared by these tests.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    dataset = ParishSoftData(
        organization_id=7,
        families={
            10: {
                "familyDUID": 10,
                "familyName": "Smith",
            }
        },
        members={
            1: {
                "memberDUID": 1,
                "familyDUID": 10,
                "firstName": "Jane",
                "lastName": "Smith",
                "py friendly name FL": "Janie Smith",
                "py friendly name LF": "Smith, Janie",
            },
            2: {
                "memberDUID": 2,
                "familyDUID": 10,
                "firstName": "Bob",
                "lastName": "Jones",
            },
        },
        family_groups={},
        family_workgroups={},
        family_workgroup_memberships={
            20: {
                "id": 20,
                "name": "Family Workgroup",
                "membership": [{"familyId": 10, "note": "family row"}],
            }
        },
        member_contactinfos={},
        member_workgroups={},
        member_workgroup_memberships={
            30: {
                "id": 30,
                "name": "Member Workgroup",
                "membership": [{"memberId": 1, "note": "member row"}],
            }
        },
        ministry_types={
            2: {"id": 2, "name": "002-Ushers"},
            1: {"id": 1, "name": "001-Readers"},
            3: {"id": 3, "name": "Historical Ministry"},
            4: {"id": 4, "name": "Example Special Ministry"},
        },
        ministry_type_memberships={
            1: {
                "id": 1,
                "name": "001-Readers",
                "membership": [{"memberId": 1, "role": "Reader"}],
            }
        },
        funds={},
        pledges={},
        contributions={},
    )
    family = dataset.families[10]
    member = dataset.members[1]
    family["py workgroups"] = {
        "Family Workgroup": dataset.family_workgroup_memberships[20]
    }
    member["py family"] = family
    member["py workgroups"] = {
        "Member Workgroup": dataset.member_workgroup_memberships[30]
    }
    member["py ministries"] = {"001-Readers": dataset.ministry_type_memberships[1]}
    return dataset


def ministry_types():
    """Return just the ministry-type mapping from the shared dataset."""
    return data().ministry_types


def write_config(tmp_path: Path, *, load_contributions: str = "false") -> Path:
    """Write an API key file and config YAML under ``tmp_path``.

    Returns the path to the config file. ``load_contributions`` is injected
    verbatim so tests can exercise YAML-driven contribution settings.
    """
    api_key = tmp_path / "parishsoft-api-key.txt"
    api_key.write_text("key", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
parishsoft:
  api_key_file: {api_key}
  cache_dir: {tmp_path / "cache"}
  cache_limit: 1d
  expected_organization: Example Parish
print_member:
  load_contributions: {load_contributions}
print_ministries:
  include_patterns:
    - '^\\d\\d\\d-'
  include_names:
    - Example Special Ministry
  exclude_patterns: []
""",
        encoding="utf-8",
    )
    return config


def test_find_members_by_name_matches_friendly_names():
    """Name search matches the friendly-name fields, not just first/last name."""
    # "janie" appears only in the friendly-name fields of member 1, so a hit
    # confirms those fields are searched.
    assert [
        member["memberDUID"] for member in find_members_by_name(data().members, "janie")
    ] == [1]


def test_print_member_selects_member_and_load_contributions(
    tmp_path, monkeypatch, capsys
):
    """A --member-duid run passes the parsed selector and contribution date to
    the loader and prints the full raw member object.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    calls = []
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    def loader(_client, **kwargs):
        """Record the loader keyword arguments and return the shared dataset."""
        calls.append(kwargs)
        return data()

    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path)),
                "--member-duid",
                "1",
                "--load-contributions",
                "2026-01-01",
            ],
            loader=loader,
        )
        == 0
    )

    assert calls == [
        {
            "active_only": False,
            "parishioners_only": False,
            "load_contributions": "2026-01-01",
            "selector": ("member", 1),
        }
    ]
    output = capsys.readouterr().out
    assert "'memberDUID': 1" in output
    # pk-query-ps-memfam is a debugging/reference tool, so it intentionally
    # exposes the derived "py" fields from the cross-linked data structure.
    assert "'py friendly name FL': 'Janie Smith'" in output
    assert f"'membership': '{OMITTED_MEMBERSHIP}'" in output
    assert "member row" not in output


def test_print_member_full_includes_membership_lists(tmp_path, monkeypatch, capsys):
    """--full prints membership rows that are omitted by default."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path)),
                "--member-duid",
                "1",
                "--full",
            ],
            loader=lambda _client, **_kwargs: data(),
        )
        == 0
    )

    output = capsys.readouterr().out
    assert f"'membership': '{OMITTED_MEMBERSHIP}'" not in output
    assert "member row" in output
    assert "Reader" in output


def test_print_member_name_selector_runs_through_main(tmp_path, monkeypatch, capsys):
    """A --name search resolves to the matching member and prints it."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        print_member_main(
            ["--config", str(write_config(tmp_path)), "--name", "janie"],
            loader=lambda _client, **_kwargs: data(),
        )
        == 0
    )

    assert "'memberDUID': 1" in capsys.readouterr().out


def test_print_member_verbose_shows_parishsoft_loader_logs(
    tmp_path, monkeypatch, capsys
):
    """--verbose displays INFO logs emitted by shared ParishSoft helpers."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    def loader(_client, **_kwargs):
        """Emit a representative shared-loader log message."""
        logging.getLogger("parishkit.parishsoft").info("Loading mocked PS data")
        return data()

    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path)),
                "--member-duid",
                "1",
                "--verbose",
            ],
            loader=loader,
        )
        == 0
    )

    assert "Loading mocked PS data" in capsys.readouterr().err


def test_print_member_selects_family(tmp_path, monkeypatch, capsys):
    """A --family-duid run prints the selected family."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        print_member_main(
            ["--config", str(write_config(tmp_path)), "--family-duid", "10"],
            loader=lambda _client, **_kwargs: data(),
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "'familyDUID': 10" in output
    assert f"'membership': '{OMITTED_MEMBERSHIP}'" in output
    assert "family row" not in output


def test_print_member_reports_missing_member_without_traceback(
    tmp_path, monkeypatch, capsys
):
    """An unknown member DUID exits with code 2 and a plain error message
    rather than an uncaught traceback."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        print_member_main(
            ["--config", str(write_config(tmp_path)), "--member-duid", "999"],
            loader=lambda _client, **_kwargs: data(),
        )
        == 2
    )

    assert "ERROR: member DUID not found: 999" in capsys.readouterr().err


def test_print_member_missing_api_key_is_user_facing(tmp_path, capsys):
    """A missing API key file produces a user-facing error and exit code 2."""
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
parishsoft:
  api_key_file: {tmp_path / "missing.txt"}
  cache_dir: {tmp_path / "cache"}
  cache_limit: 1d
""",
        encoding="utf-8",
    )

    assert (
        print_member_main(
            ["--config", str(config), "--member-duid", "1"],
            loader=lambda _client, **_kwargs: data(),
        )
        == 2
    )

    assert "ERROR:" in capsys.readouterr().err


def test_print_member_load_contribution_overrides_and_yaml_dates(tmp_path, monkeypatch):
    """The load_contributions value resolves correctly across sources: a YAML
    date passes through, --no-load-contributions wins over a YAML true, and a
    bare --load-contributions flag enables it.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    calls = []
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    def loader(_client, **kwargs):
        """Record each run's resolved load_contributions value."""
        calls.append(kwargs["load_contributions"])
        return data()

    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path, load_contributions="2026-01-01")),
                "--member-duid",
                "1",
            ],
            loader=loader,
        )
        == 0
    )
    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path, load_contributions="true")),
                "--member-duid",
                "1",
                "--no-load-contributions",
            ],
            loader=loader,
        )
        == 0
    )
    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path)),
                "--member-duid",
                "1",
                "--load-contributions",
            ],
            loader=loader,
        )
        == 0
    )

    assert calls == ["2026-01-01", False, True]


def test_print_member_lookup_loader_uses_full_parishsoft_loader(monkeypatch):
    """The default query loader delegates to the full ParishSoft aggregation
    helper so selected records include workgroups, ministries, contact info,
    and other cross-linked fields."""
    from parishkit.pk_query_ps_memfam import load_lookup_data

    calls = []
    expected = data()

    def full_loader(client, **kwargs):
        """Record the full-loader call and return the shared dataset."""
        calls.append((client, kwargs))
        return expected

    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.load_families_and_members", full_loader
    )
    client = SimpleNamespace()

    result = load_lookup_data(
        client,
        load_contributions=True,
        selector=("member", 1),
    )

    assert result is expected
    assert calls == [
        (
            client,
            {
                "active_only": False,
                "parishioners_only": False,
                "load_contributions": True,
            },
        )
    ]


def test_print_member_allows_contributions_for_name_search(
    tmp_path, monkeypatch, capsys
):
    """A --name run can request contributions because the full loader is used."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path)),
                "--name",
                "Jane",
                "--load-contributions",
            ],
            loader=lambda _client, **_kwargs: data(),
        )
        == 0
    )

    assert "'memberDUID': 1" in capsys.readouterr().out


def test_print_member_rejects_bad_contribution_date(tmp_path, monkeypatch, capsys):
    """A slash-delimited contribution date is rejected with a YYYY-MM-DD hint."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path)),
                "--member-duid",
                "1",
                "--load-contributions",
                "2026/01/01",
            ],
            loader=lambda _client, **_kwargs: data(),
        )
        == 2
    )

    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_print_member_rejects_compact_iso_date(tmp_path, monkeypatch, capsys):
    """A compact (no-dash) ISO date is rejected with a YYYY-MM-DD hint."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        print_member_main(
            [
                "--config",
                str(write_config(tmp_path)),
                "--member-duid",
                "1",
                "--load-contributions",
                "20260101",
            ],
            loader=lambda _client, **_kwargs: data(),
        )
        == 2
    )

    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_print_member_reports_parishsoft_api_errors(tmp_path, monkeypatch, capsys):
    """A ParishSoftAPIError from the loader becomes a user-facing message and
    exit code 2."""
    monkeypatch.setattr(
        "parishkit.pk_query_ps_memfam.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    def fail_loader(_client, **_kwargs):
        raise ParishSoftAPIError(503, "endpoint", "unavailable")

    assert (
        print_member_main(
            ["--config", str(write_config(tmp_path)), "--member-duid", "1"],
            loader=fail_loader,
        )
        == 2
    )

    assert "ParishSoft API error" in capsys.readouterr().err


def test_print_member_rejects_blank_name_selector():
    """A whitespace-only --name value is rejected by argument parsing."""
    with pytest.raises(SystemExit):
        print_member_main(["--name", " "])


def test_print_member_requires_one_selector():
    """Invoking with no selector at all is rejected by argument parsing."""
    with pytest.raises(SystemExit):
        print_member_main([])


def test_print_ministries_outputs_sorted_unique_names(tmp_path, monkeypatch, capsys):
    """The command validates the organization and prints the configured
    ministry names sorted and de-duplicated."""
    client = FakeClient()
    monkeypatch.setattr(
        "parishkit.pk_print_ps_ministries.parishsoft_client_from_config",
        lambda _common, _config: client,
    )

    assert (
        print_ministries_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client: ministry_types(),
        )
        == 0
    )

    assert client.validated
    assert capsys.readouterr().out.splitlines() == [
        "001-Readers",
        "002-Ushers",
        "Example Special Ministry",
    ]


def test_print_ministries_verbose_shows_parishsoft_loader_logs(
    tmp_path, monkeypatch, capsys
):
    """--verbose displays INFO logs emitted while loading ministries."""
    client = FakeClient()
    monkeypatch.setattr(
        "parishkit.pk_print_ps_ministries.parishsoft_client_from_config",
        lambda _common, _config: client,
    )

    def loader(_client):
        """Emit a representative ministry-loader log message."""
        logging.getLogger("parishkit.parishsoft").info("Loading mocked ministries")
        return ministry_types()

    assert (
        print_ministries_main(
            ["--config", str(write_config(tmp_path)), "--verbose"],
            loader=loader,
        )
        == 0
    )

    assert "Loading mocked ministries" in capsys.readouterr().err


def test_sorted_ministry_names_deduplicates_and_sorts():
    """Include patterns and include names select matching ministries, returned
    sorted with duplicates removed; the historical ministry is excluded."""
    assert sorted_ministry_names(
        ministry_types(),
        include_patterns=[r"^\d\d\d-"],
        include_names=["Example Special Ministry"],
        exclude_patterns=[],
    ) == ["001-Readers", "002-Ushers", "Example Special Ministry"]


def test_sorted_ministry_names_without_filters_returns_all_names():
    """With no filters, every ministry name is returned, sorted."""
    assert sorted_ministry_names(ministry_types()) == [
        "001-Readers",
        "002-Ushers",
        "Example Special Ministry",
        "Historical Ministry",
    ]


def test_print_ministries_reports_bad_regex(tmp_path, monkeypatch, capsys):
    """An invalid include pattern is reported as an error and exit code 2,
    before the organization is validated (so no remote work happens).

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    config = tmp_path / "config.yaml"
    api_key = tmp_path / "parishsoft-api-key.txt"
    api_key.write_text("key", encoding="utf-8")
    config.write_text(
        f"""
parishsoft:
  api_key_file: {api_key}
  cache_dir: {tmp_path / "cache"}
  cache_limit: 1d
print_ministries:
  include_patterns:
    - "["
""",
        encoding="utf-8",
    )
    client = FakeClient()
    monkeypatch.setattr(
        "parishkit.pk_print_ps_ministries.parishsoft_client_from_config",
        lambda _common, _config: client,
    )

    assert (
        print_ministries_main(
            ["--config", str(config)],
            loader=lambda _client: ministry_types(),
        )
        == 2
    )

    assert "invalid regex" in capsys.readouterr().err
    assert not client.validated


def test_print_ministries_validates_bad_regex_before_loading(
    tmp_path, monkeypatch, capsys
):
    """Regex validation happens before loading: a failing loader is never
    reached, so the invalid-regex error surfaces instead of an API error."""
    config = tmp_path / "config.yaml"
    api_key = tmp_path / "parishsoft-api-key.txt"
    api_key.write_text("key", encoding="utf-8")
    config.write_text(
        f"""
parishsoft:
  api_key_file: {api_key}
  cache_dir: {tmp_path / "cache"}
  cache_limit: 1d
print_ministries:
  include_patterns:
    - "["
""",
        encoding="utf-8",
    )

    def fail_loader(_client):
        raise ParishSoftAPIError(503, "ministry/type/list", "unavailable")

    assert print_ministries_main(["--config", str(config)], loader=fail_loader) == 2

    assert "invalid regex" in capsys.readouterr().err
