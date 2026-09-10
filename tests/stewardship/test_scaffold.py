"""ARC-01: credential-free package, settings, routing, and CLI smoke tests."""

import json
import os
import subprocess
import sys
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.urls import resolve, reverse

from parishkit.stewardship.cli import main


@pytest.mark.parametrize(
    "name",
    [
        "accounts",
        "source",
        "campaigns",
        "responses",
        "workflows",
        "reports",
        "jobs",
        "audit",
    ],
)
def test_app_boundaries(name):
    """App registration has stable, collision-free labels and importable modules."""
    config = apps.get_app_config(f"stewardship_{name}")
    assert config.name == f"parishkit.stewardship.{name}"
    assert import_module(config.name) is config.module


def test_safe_test_settings():
    """Scaffold tests cannot accidentally send mail or create local data files."""
    assert settings.USE_TZ and settings.TIME_ZONE == "UTC"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.dummy"
    assert settings.SESSION_ENGINE == "django.contrib.sessions.backends.db"
    profile = import_module("parishkit.stewardship.settings.test")
    assert profile.EMAIL_BACKEND == "django.core.mail.backends.dummy.EmailBackend"
    # pytest-django substitutes its in-memory backend for each test.
    assert settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"
    assert "django.contrib.admin" not in settings.INSTALLED_APPS
    # Allauth uses Django's auth internals, but no password backend is enabled.
    assert settings.AUTHENTICATION_BACKENDS == []


@pytest.mark.parametrize(
    ("name", "url", "status"),
    [
        ("public:entry", "/", 503),
        ("family:entry", "/family/", 503),
        ("admin:index", "/admin/", 503),
        ("admin:login", "/admin/login", 503),
        ("internal:live", "/health/live", 200),
        ("internal:ready", "/health/ready", 503),
        ("internal:metrics", "/metrics", 404),
    ],
)
def test_namespaced_scaffold_routes(client, name, url, status):
    """Routes expose only intentional status responses, without database access."""
    assert reverse(name) == url
    assert resolve(url).view_name == name
    response = client.get(url)
    assert response.status_code == status
    assert response["Cache-Control"] == "no-store"
    assert not response.cookies
    assert client.head(url).status_code == status
    # Setup admission precedes HTTP-method dispatch for all application routes.
    # Internal probes retain their safe-method contract without a runtime.
    assert client.post(url).status_code == (
        405 if name.startswith("internal:") else 503
    )


def test_token_placeholder_does_not_reflect_or_exchange_token(client):
    """Email links cannot yet create sessions or disclose their token in a page."""
    token = "synthetic-private-link"
    url = reverse("public:access", kwargs={"token": token})
    assert resolve(url).view_name == "public:access"
    response = client.get(url)
    assert response.status_code == 503
    assert token.encode() not in response.content
    assert not response.cookies


@pytest.mark.parametrize("url", ["/admin/password_reset/", "/api/", "/health/other"])
def test_unimplemented_routes_are_not_found(client, url):
    """No generic Django admin, public API, or wildcard health interface exists."""
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("profile", ["development", "test"])
def test_profile_starts_without_credentials(profile):
    """Each safe profile imports the actual WSGI app in a fresh interpreter."""
    result = subprocess.run(
        [sys.executable, "-c", "from parishkit.stewardship.wsgi import application"],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": f"parishkit.stewardship.settings.{profile}",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_production_profile_fails_closed():
    """Production cannot fall back to a developer key or incomplete configuration."""
    result = subprocess.run(
        [sys.executable, "-m", "django", "check"],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "parishkit.stewardship.settings.production",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Production startup is unavailable" in result.stderr
    assert "development-scaffold-only" not in result.stderr


def test_cli_version_and_help(capsys):
    """Version and help are usable before any deployment configuration exists."""
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"pk-stewardship {version('parishkit')}\n"
    assert main([]) == 2
    help_text = capsys.readouterr().out
    assert "config-check" in help_text
    for flag in ("--dry-run", "--debug", "--slack", "--ps-", "--log-"):
        assert flag not in help_text


@pytest.mark.parametrize(
    "arguments",
    [
        ["--version", "healthcheck"],
        ["--version", "--config", "unused"],
        ["--runtime-root", "unused"],
    ],
)
def test_options_without_consuming_command_are_rejected(arguments, capsys):
    """Neither version nor missing-command handling silently discards options."""
    with pytest.raises(SystemExit) as exc:
        main(arguments)
    assert exc.value.code == 2
    assert not capsys.readouterr().out


def test_config_diagnostics_are_redacted(tmp_path, capsys):
    """Syntactically valid mappings are checked without displaying any values."""
    config = tmp_path / "deployment.yaml"
    config.write_text("synthetic_secret: do-not-print-me\n", encoding="utf-8")
    assert main(["--config", str(config), "config-check"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "yaml_mapping_valid": True,
        "deployment_validated": False,
    }
    assert not output.err


@pytest.mark.parametrize(
    "content", [b"token: [synthetic-secret", b"- synthetic-secret", b"\xff"]
)
def test_invalid_config_redacts_parser_errors(tmp_path, capsys, content):
    """Malformed input never leaks YAML source lines, contents, or path names."""
    config = tmp_path / "sensitive-filename.yaml"
    config.write_bytes(content)
    assert main(["--config", str(config), "config-check"]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert output.err == "ERROR: configuration must be a readable YAML mapping\n"


def test_missing_config_is_required(tmp_path, capsys):
    """Both absent argument and nonexistent files fail diagnostics safely."""
    with pytest.raises(SystemExit) as exc:
        main(["config-check"])
    assert exc.value.code == 2
    capsys.readouterr()
    assert main(["--config", str(tmp_path / "absent.yaml"), "config-check"]) == 2
    assert "absent.yaml" not in capsys.readouterr().err


def test_wrapper_can_be_executed():
    """The installed package is reachable through the thin executable wrapper."""
    wrapper = Path(__file__).parents[2] / "scripts/pk-stewardship/pk-stewardship.py"
    result = subprocess.run(
        [sys.executable, str(wrapper), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"pk-stewardship {version('parishkit')}\n"
