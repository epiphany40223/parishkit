import tomllib
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

EXPECTED_ENTRYPOINTS = {
    "pk-cron-runner": "parishkit.cli:run_main",
    "pk-query-ps-memfam": "parishkit.cli:print_member_main",
    "pk-print-ps-ministries": "parishkit.cli:print_ministries_main",
    "pk-validate-gcalendar-reservations": "parishkit.cli:calendar_reservations_main",
    "pk-create-ps-ministry-rosters": "parishkit.cli:create_ministry_rosters_main",
    "pk-sync-ps-to-ggroup": "parishkit.cli:sync_google_group_main",
    "pk-sync-ps-to-cc": "parishkit.cli:sync_ps_to_cc_main",
}


def test_console_entrypoints_are_registered():
    with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as stream:
        pyproject = tomllib.load(stream)

    assert pyproject["project"]["scripts"] == EXPECTED_ENTRYPOINTS


def test_placeholder_entrypoints_report_versions(capsys):
    for entrypoint in EXPECTED_ENTRYPOINTS.values():
        module_name, function_name = entrypoint.split(":", maxsplit=1)
        cli_function = getattr(import_module(module_name), function_name)
        assert cli_function(["--version"]) == 0

    output = capsys.readouterr().out
    for tool_name in EXPECTED_ENTRYPOINTS:
        assert f"{tool_name} {version('parishkit')}" in output
