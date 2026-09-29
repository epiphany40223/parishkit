"""The upgrade no-op query covers every login and refuses unclear input (#162).

The query's behavior against a real database is proven in
test_database_provisioning_container; these tests pin its rendering and the
command's refusals without a database.
"""

import json
from uuid import uuid4

from parishkit.stewardship import upgrade_check
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment_documents import deployment_document
from parishkit.stewardship.runtime_identities import database_identities

from .test_runtime_topology import configuration_at


def test_literals_are_quoted_and_empty_arrays_stay_typed():
    """Code-owned names still pass through SQL quoting."""
    assert upgrade_check.literal("a'b") == "'a''b'"
    assert upgrade_check.text_array([]) == "ARRAY[]::text[]"
    assert upgrade_check.text_array(["x", "y"]) == "ARRAY['x','y']::text[]"


def test_the_query_names_every_login_and_migration(tmp_path):
    """No foundation login or shipped migration is left out of the proof."""
    deployment = uuid4()
    query = upgrade_check.upgrade_noop_query(configuration_at(tmp_path), deployment)
    assert query.startswith("SELECT (")
    assert f"'parishkit-stewardship:{deployment}'" in query
    for _, login, _, _ in database_identities():
        assert f"r.rolname='{login}'" in query, login
    for app, name in upgrade_check.disk_migrations():
        assert f"'{name}'" in query and f"'{app}'" in query
    # The writer guard is compared by this release's function body digest.
    assert upgrade_check._writer_guard_digest() in query


def test_the_command_prints_the_query(tmp_path, capsys):
    """The CLI renders the same query from a deployment document."""
    configuration = configuration_at(tmp_path)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(deployment_document(configuration)))
    deployment = str(uuid4())
    argv = ["upgrade-check", "--config", str(path), "--confirm-deployment"]
    assert main([*argv, deployment]) == 0
    assert capsys.readouterr().out.startswith("SELECT (")


def test_the_command_refuses_unclear_input(tmp_path, capsys):
    """A missing configuration or a non-canonical UUID prints no query."""
    configuration = configuration_at(tmp_path)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(deployment_document(configuration)))
    deployment = str(uuid4())
    for argv in (
        ["upgrade-check", "--confirm-deployment", deployment],
        ["upgrade-check", "--config", str(path)],
        ["upgrade-check", "--config", str(path), "--confirm-deployment", "x"],
        [
            "upgrade-check",
            "--config",
            str(path),
            "--confirm-deployment",
            deployment.upper(),
        ],
    ):
        assert main(argv) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "run migration and grants" in captured.err
