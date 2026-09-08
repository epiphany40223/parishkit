"""Local service launch, redacted health probes, and safe synthetic provisioning."""

import os
import stat
import sys
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from parishkit.stewardship import cli, services
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import (
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--dry-run"],
        ["--no-dry-run"],
        ["--verbose"],
        ["--no-verbose"],
        ["--debug"],
        ["--no-debug"],
        ["--log-file", "unused"],
        ["--log-dir", "unused"],
        ["--slack-token-file", "unused"],
        ["--slack-channel", "unused"],
        ["--slack-log-level", "ERROR"],
        ["--ps-api-key-file", "unused"],
        ["--ps-cache-dir", "unused"],
        ["--ps-cache-limit", "1d"],
        ["--runtime-ro", "unused"],
    ],
)
def test_unsupported_options_cannot_provision_storage(tmp_path, arguments):
    """Unsupported shared flags and abbreviations fail before real disk writes."""
    target = tmp_path / "new-runtime"
    with pytest.raises(SystemExit) as exc:
        main([*arguments, "prepare-development", "--runtime-root", str(target)])
    assert exc.value.code == 2
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("command", "option", "value"),
    [
        ("config-check", "--profile", "development"),
        ("config-check", "--service-role", "web"),
        ("config-check", "--public-origin", "synthetic-sensitive-value"),
        ("config-check", "--runtime-root", "synthetic-sensitive-value"),
        ("service", "--config", "synthetic-sensitive-value"),
        ("service", "--public-origin", "synthetic-sensitive-value"),
        ("service", "--runtime-root", "synthetic-sensitive-value"),
        ("healthcheck", "--config", "synthetic-sensitive-value"),
        ("healthcheck", "--profile", "development"),
        ("healthcheck", "--service-role", "web"),
        ("healthcheck", "--public-origin", "synthetic-sensitive-value"),
        ("healthcheck", "--runtime-root", "synthetic-sensitive-value"),
        ("prepare-development", "--config", "synthetic-sensitive-value"),
        ("prepare-development", "--profile", "development"),
        ("prepare-development", "--service-role", "web"),
        ("prepare-development", "--public-origin", "synthetic-sensitive-value"),
    ],
)
@pytest.mark.parametrize("before_command", [True, False])
def test_command_inapplicable_options_fail_before_dispatch(
    command, option, value, before_command, monkeypatch, capsys
):
    """Reject ignored flags on either side of the command without echoing values."""
    operations = []
    for name in (
        "prepare_development",
        "run_service",
        "healthcheck",
        "load_deployment",
        "load_yaml_config",
    ):
        operation = Mock()
        monkeypatch.setattr(cli, name, operation)
        operations.append(operation)
    arguments = [option, value, command] if before_command else [command, option, value]
    with pytest.raises(SystemExit) as exc:
        main(arguments)
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "options not supported by" in output.err
    assert "synthetic-sensitive-value" not in output.err
    assert not output.out
    for operation in operations:
        operation.assert_not_called()


@pytest.mark.parametrize("profile", [None, *DeploymentProfile])
@pytest.mark.parametrize("role", [None, *ServiceRole])
@pytest.mark.parametrize("bind_all_interfaces", [False, True])
def test_service_startup_is_explicit_and_fail_closed(
    profile, role, bind_all_interfaces, monkeypatch, capsys
):
    """Only the implemented development web identity can replace its process."""
    execute = Mock()
    monkeypatch.setattr(services.os, "execve", execute)
    assert (
        services.run_service(profile, role, bind_all_interfaces=bind_all_interfaces)
        == 2
    )
    if profile == "development" and role == "web":
        executable, arguments, environment = execute.call_args.args
        assert executable == sys.executable
        assert arguments == [
            sys.executable,
            "-m",
            "django",
            "runserver",
            "0.0.0.0:8000" if bind_all_interfaces else "127.0.0.1:8000",
        ]
        assert environment["DJANGO_SETTINGS_MODULE"].endswith(".development")
        assert environment["PATH"] == os.environ["PATH"]
        assert not capsys.readouterr().err
    else:
        execute.assert_not_called()
        assert "startup refused" in capsys.readouterr().err


@pytest.mark.parametrize("bind_all_interfaces", [False, True])
@pytest.mark.parametrize("before_command", [False, True])
def test_service_cli(bind_all_interfaces, before_command, monkeypatch):
    """Host use defaults to loopback; only explicit opt-in exposes all interfaces."""
    execute = Mock()
    monkeypatch.setattr(services.os, "execve", execute)
    arguments = ["service", "--profile", "development", "--service-role", "web"]
    opt_in = ["--bind-all-interfaces"] if bind_all_interfaces else []
    assert main(opt_in + arguments if before_command else arguments + opt_in) == 2
    execute.assert_called_once()
    assert execute.call_args.args[1][-1] == (
        "0.0.0.0:8000" if bind_all_interfaces else "127.0.0.1:8000"
    )
    assert main(["service"]) == 2


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--version"],
        ["config-check"],
        ["validate-deployment"],
        ["healthcheck"],
        ["prepare-development"],
        ["service"],
        ["service", "--profile", "production", "--service-role", "web"],
        ["service", "--profile", "development", "--service-role", "worker"],
    ],
)
@pytest.mark.parametrize("before_command", [False, True])
def test_bind_opt_in_is_rejected_before_unrelated_dispatch(
    arguments, before_command, monkeypatch, capsys
):
    """The opt-in cannot silently affect other commands or unimplemented services."""
    operations = []
    for name in (
        "prepare_development",
        "run_service",
        "healthcheck",
        "load_deployment",
        "load_yaml_config",
    ):
        operation = Mock()
        monkeypatch.setattr(cli, name, operation)
        operations.append(operation)
    opt_in = ["--bind-all-interfaces"]
    with pytest.raises(SystemExit) as exc:
        main(opt_in + arguments if before_command else arguments + opt_in)
    assert exc.value.code == 2
    assert not capsys.readouterr().out
    for operation in operations:
        operation.assert_not_called()


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [(200, b"ok\n", 0), (200, b"no\n", 1), (200, b"ok\nx", 1), (503, b"ok\n", 1)],
)
def test_health_response(status, body, expected, monkeypatch, capsys):
    """Health uses bounded reads, bypasses ambient proxies, and prints no body."""
    response = Mock(status=status)
    response.read.return_value = body
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    factory = Mock(return_value=opener)
    monkeypatch.setattr(services, "build_opener", factory)
    assert main(["healthcheck"]) == expected
    proxy, redirect = factory.call_args.args
    assert proxy.proxies == {}
    assert (
        redirect.redirect_request(None, None, 302, None, None, "http://remote") is None
    )
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8000/health/live"
    assert opener.open.call_args.kwargs == {"timeout": 3}
    if status == 200:
        response.read.assert_called_once_with(4)
    else:
        response.read.assert_not_called()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("error", [URLError("private reason"), TimeoutError("secret")])
def test_health_errors_are_redacted(error, monkeypatch, capsys):
    """Network failure is an unhealthy exit code, never raw exception output."""
    opener = Mock()
    opener.open.side_effect = error
    monkeypatch.setattr(services, "build_opener", Mock(return_value=opener))
    assert services.healthcheck() == 1
    assert capsys.readouterr() == ("", "")


def test_new_development_tree_is_private_and_not_application_bootstrap(
    tmp_path, capsys
):
    """Provision only disposable storage and a random, never-printed DB password."""
    root = tmp_path / "local"
    assert main(["prepare-development", "--runtime-root", str(root)]) == 0
    password_file = root / "credentials/development-postgres-password"
    password = password_file.read_text()
    assert len(password.strip()) >= 32
    assert password.strip() not in capsys.readouterr().out
    assert list((root / "credentials").iterdir()) == [password_file]
    assert not list((root / "config").iterdir())
    for path in [root, *root.rglob("*")]:
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == (
                0o711
                if path == root / "run/persistent/postgresql"
                else 0o700
                if path.is_dir()
                else 0o600
            )
    assert main(["prepare-development", "--runtime-root", str(root)]) == 2
    assert password_file.read_text() == password
    assert "no existing data was replaced" in capsys.readouterr().err


def test_development_provisioning_requires_explicit_new_target(tmp_path):
    """No default root writes, implicit parents, or symlink-target modifications."""
    with pytest.raises(SystemExit):
        main(["prepare-development"])
    assert (
        main(["prepare-development", "--runtime-root", str(tmp_path / "absent/child")])
        == 2
    )
    if os.name == "posix":
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        assert main(["prepare-development", "--runtime-root", str(link)]) == 2
        assert link.is_symlink()


def test_development_tree_matches_resolved_standard_paths(tmp_path):
    """Detect layout drift without treating local provisioning as bootstrap."""
    root = tmp_path / "local"
    paths = load_deployment(environ={"PARISHKIT_ROOT": str(root)}).paths
    services.prepare_development(root)
    # Parish authority is deliberately absent until the offline bootstrap work.
    expected = {path for name, path in paths.values.items() if name != "authority"} | {
        paths["caddy"] / "data",
        paths["caddy"] / "config",
    }
    assert {path for path in root.rglob("*") if path.is_dir()} == expected
    assert not paths["authority"].exists()


def test_development_provisioning_expands_user_path(tmp_path, monkeypatch):
    """Quoted tilde paths must resolve before provisioning, like deployment paths."""
    target = tmp_path / "expanded"
    expand = Mock(return_value=target)
    provision = Mock()
    monkeypatch.setattr(Path, "expanduser", expand)
    monkeypatch.setattr(cli, "prepare_development", provision)
    assert main(["prepare-development", "--runtime-root", "~/expanded"]) == 0
    expand.assert_called_once()
    provision.assert_called_once_with(target.absolute())


@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError])
def test_development_path_expansion_failure_is_safe(error, monkeypatch, capsys):
    """Resolution failures cannot provision a literal tilde path or leak details."""
    monkeypatch.setattr(
        Path, "expanduser", Mock(side_effect=error("synthetic-private-home"))
    )
    provision = Mock()
    monkeypatch.setattr(cli, "prepare_development", provision)
    assert main(["prepare-development", "--runtime-root", "~unknown/private"]) == 2
    provision.assert_not_called()
    output = capsys.readouterr()
    assert not output.out
    assert "no existing data was replaced" in output.err
    assert "private" not in output.err
