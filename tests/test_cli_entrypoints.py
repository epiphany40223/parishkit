from importlib.metadata import entry_points, version

EXPECTED_ENTRYPOINTS = {
    "parishkit-run": "parishkit.cli:run_main",
    "parishkit-print-member": "parishkit.cli:print_member_main",
    "parishkit-print-ministries": "parishkit.cli:print_ministries_main",
    "parishkit-calendar-reservations": "parishkit.cli:calendar_reservations_main",
    "parishkit-create-ministry-rosters": "parishkit.cli:create_ministry_rosters_main",
    "parishkit-sync-google-group": "parishkit.cli:sync_google_group_main",
    "parishkit-sync-ps-to-cc": "parishkit.cli:sync_ps_to_cc_main",
}


def test_console_entrypoints_are_registered():
    scripts = {
        entrypoint.name: entrypoint.value
        for entrypoint in entry_points(group="console_scripts")
        if entrypoint.name.startswith("parishkit-")
    }

    assert scripts == EXPECTED_ENTRYPOINTS


def test_placeholder_entrypoints_report_versions(capsys):
    for tool_name in EXPECTED_ENTRYPOINTS:
        entrypoint = entry_points(group="console_scripts")[tool_name].load()
        assert entrypoint(["--version"]) == 0

    output = capsys.readouterr().out
    for tool_name in EXPECTED_ENTRYPOINTS:
        assert f"{tool_name} {version('parishkit')}" in output
