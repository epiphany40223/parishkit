"""Absence guarantees of the LOCAL test sign-in and its LOCAL-only selection (#476).

The specification requires proof that only LOCAL's ``configure_web`` selects
``local_urls``, that no production URL reaches the view, that the view called
directly outside LOCAL answers 404, that the command refuses a production
configuration before touching anything, and that the Production ingress
documents carry no local route. The real sign-in against disposable
PostgreSQL and Valkey lives in the database suite.
"""

import inspect
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import Resolver404, URLPattern, URLResolver, resolve

from parishkit.config import ConfigError
from parishkit.stewardship import local_urls, runtime_web, urls
from parishkit.stewardship.accounts import access_gate, local_sign_in, sessions
from parishkit.stewardship.accounts.authentication import AuthRuntime
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import DeploymentProfile, ServiceRole
from parishkit.stewardship.runtime_valkey import web_acl
from parishkit.stewardship.web.security import CSP

from .test_local_profile import FIXTURES, PROFILES, configuration_for

PATH = local_sign_in.PATH
OTHER_PROFILES = [
    profile for profile in PROFILES if profile is not DeploymentProfile.LOCAL
]


def untouched_runtime(settings):
    """Install an auth runtime whose limiter (and so Valkey) must not be reached.

    Setup is reported incomplete, so the branding context processor reads
    nothing from the store when a page renders; the limiter records calls.
    """
    runtime = AuthRuntime(store=Mock(), limiter=Mock(), setup_complete=None)
    settings.STEWARDSHIP_AUTH_RUNTIME = runtime
    return runtime.limiter


def request_for(method, **extra):
    """Build a request as the security middleware would have left it."""
    factory = RequestFactory(HTTP_HOST="localhost:8443", **extra)
    request = (
        factory.post(PATH, {"token": "a" * 43})
        if method == "POST"
        else (factory.get(PATH))
    )
    request.client_address = "127.0.0.1"
    return request


# Selection.
@pytest.mark.parametrize("profile", PROFILES)
def test_url_configuration_selects_local_urls_only_for_local(profile):
    """LOCAL alone gets the local module; every other profile keeps urls.py."""
    selected = runtime_web.url_configuration(profile)
    if profile is DeploymentProfile.LOCAL:
        assert selected == "parishkit.stewardship.local_urls"
    else:
        assert selected == "parishkit.stewardship.urls"


def test_the_compose_probe_checks_the_real_configure_web_selection():
    """The in-container probe asserts ROOT_URLCONF against url_configuration.

    configure_web needs a fresh process with real mounts and credentials, so
    the real check runs in the opt-in Compose test through this probe.
    """
    probe = Path(__file__).with_name("runtime_auth_probe.py").read_text()
    assert "url_configuration(configuration.profile) == settings.ROOT_URLCONF" in (
        probe
    )
    assert 'resolve("/admin/local/sign-in")' in probe


def test_the_access_gate_lets_the_route_authenticate_itself():
    """The gate must not send the route to /admin/login before setup completes."""
    assert PATH in access_gate.AUTH_ROUTES
    assert "/admin/login" in access_gate.AUTH_ROUTES


def _views(patterns):
    """Every view callable reachable from a URL pattern list."""
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _views(pattern.url_patterns)
        elif isinstance(pattern, URLPattern):
            yield pattern.callback


def test_production_urls_never_import_or_route_the_local_view():
    """urls.py has no local import, and no production URL resolves to the view."""
    source = inspect.getsource(urls)
    assert "local_urls" not in source and "local_sign_in" not in source
    assert local_sign_in.sign_in not in set(_views(urls.urlpatterns))
    with pytest.raises(Resolver404):
        resolve(PATH, urlconf="parishkit.stewardship.urls")


def test_local_urls_add_exactly_the_sign_in_route():
    """local_urls is the production list plus the one route, in front of it."""
    assert local_urls.urlpatterns[1:] == urls.urlpatterns
    match = resolve(PATH, urlconf="parishkit.stewardship.local_urls")
    assert match.func is local_sign_in.sign_in
    assert match.url_name == "local_sign_in"
    # Production routes resolve to the same views under the local module.
    for path in ("/admin/login", "/admin/oauth/callback", "/", "/health/live"):
        assert resolve(path, urlconf="parishkit.stewardship.local_urls").func is (
            resolve(path, urlconf="parishkit.stewardship.urls").func
        )


# The view outside LOCAL.
@pytest.mark.parametrize("profile", [None, *OTHER_PROFILES])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_view_called_directly_outside_local_is_404_without_valkey(
    settings, profile, method
):
    """Even bypassing routing, a non-LOCAL process answers 404 and touches nothing."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = (
        None if profile is None else (profile.value)
    )
    runtime = untouched_runtime(settings)
    response = local_sign_in.sign_in(request_for(method))
    assert response.status_code == 404
    assert response.content == b"Not Found\n"
    assert runtime.mock_calls == []


def test_local_get_serves_the_form_without_consuming_anything(settings):
    """A GET renders the CSRF form and the static script; Valkey is never read."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = DeploymentProfile.LOCAL.value
    runtime = untouched_runtime(settings)
    response = local_sign_in.sign_in(request_for("GET"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'action="/admin/local/sign-in"' in body
    assert 'id="local-sign-in-token" name="token" type="hidden"' in body
    assert 'id="local-sign-in-submit" type="submit" disabled' in body
    assert 'id="local-sign-in-missing"' in body
    assert "tools/stewardship-local.sh sign-in --email E" in body
    assert "stewardship/local-sign-in-v1.js" in body
    assert "<script>" not in body
    assert runtime.mock_calls == []


def test_local_post_from_another_host_is_denied_before_valkey(settings):
    """The Host re-check refuses anything but the one LOCAL origin, untouched."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = DeploymentProfile.LOCAL.value
    runtime = untouched_runtime(settings)
    for host in ("127.0.0.1:8443", "localhost", "localhost:8444"):
        request = RequestFactory(HTTP_HOST=host).post(PATH, {"token": "a" * 43})
        request.client_address = "127.0.0.1"
        response = local_sign_in.sign_in(request)
        assert response.status_code == 403
    assert runtime.mock_calls == []


# Token helpers.
def test_token_key_hashes_the_token_under_the_web_acl_namespace():
    """The key holds the SHA-256 of the token inside stewardship:auth:v1."""
    key = local_sign_in.token_key("abc")
    assert key == (
        "stewardship:auth:v1:local-sign-in:"
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    # The web ACL covers that namespace and grants EVAL, never GETDEL.
    acl = web_acl(b"synthetic").decode()
    assert "~stewardship:auth:v1:*" in acl and "+eval" in acl
    assert "getdel" not in acl.lower()


@pytest.mark.parametrize(
    "raw",
    [
        None,
        b"not json",
        b"[]",
        b'{"email":"a@example.test"}',
        b'{"email":"a@example.test","recovery_epoch":1}',
        b'{"email":"a@example.test","recovery_epoch":"1","extra":"x"}',
    ],
)
def test_consume_returns_none_for_anything_but_an_issued_record(raw):
    """A missing or malformed record is indistinguishable from an unknown token."""
    client = Mock()
    client.eval.return_value = raw
    assert local_sign_in.consume_token(client, "x" * 43, namespace="ns") is None
    client.eval.assert_called_once_with(
        local_sign_in.CONSUME, 1, local_sign_in.token_key("x" * 43, "ns")
    )


def test_issue_and_consume_round_trip_through_the_lua_script():
    """issue_token writes one expiring NX key; consume_token reads it back."""
    client = Mock()
    client.set.return_value = True
    token = local_sign_in.issue_token(client, "a@example.test", "initial")
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    client.set.assert_called_once_with(
        local_sign_in.token_key(token),
        '{"email":"a@example.test","recovery_epoch":"initial"}',
        ex=120,
        nx=True,
    )
    client.eval.return_value = client.set.call_args.args[1].encode()
    assert local_sign_in.consume_token(client, token) == {
        "email": "a@example.test",
        "recovery_epoch": "initial",
    }
    # A hash collision never silently overwrites an outstanding token.
    client.set.return_value = None
    with pytest.raises(ConfigError):
        local_sign_in.issue_token(client, "a@example.test", "initial")


# The operator command.
def forbid_side_effects(monkeypatch):
    """Make any database or Valkey contact fail the test."""
    from parishkit.stewardship import operator_commands

    def forbidden(*args, **kwargs):
        raise AssertionError("the command touched a backend")

    monkeypatch.setattr(operator_commands, "configure_operator_database", forbidden)
    monkeypatch.setattr(runtime_web, "valkey_client", forbidden)


@pytest.mark.parametrize("profile", OTHER_PROFILES)
def test_command_refuses_every_other_profile_before_any_contact(monkeypatch, profile):
    """A production, development or test configuration is refused untouched."""
    forbid_side_effects(monkeypatch)
    with pytest.raises(ConfigError):
        local_sign_in.local_sign_in_command(
            configuration_for(profile), "admin@example.test"
        )


@pytest.mark.parametrize(
    "change",
    [
        {"public_origin": "https://127.0.0.1:8443"},
        {"public_origin": "https://parish.example"},
        {"service_role": ServiceRole.WORKER},
    ],
)
def test_command_refuses_local_without_localhost_origin_or_outside_web(
    monkeypatch, change
):
    """LOCAL alone is not enough: the origin must be localhost, inside web."""
    forbid_side_effects(monkeypatch)
    configuration = replace(configuration_for(DeploymentProfile.LOCAL), **change)
    with pytest.raises(ConfigError):
        local_sign_in.local_sign_in_command(configuration, "admin@example.test")


def test_command_refuses_an_invalid_email_before_any_contact(monkeypatch):
    """The address is normalized first; garbage never reaches the database."""
    forbid_side_effects(monkeypatch)
    with pytest.raises(ConfigError):
        local_sign_in.local_sign_in_command(
            configuration_for(DeploymentProfile.LOCAL), "not an address"
        )


def test_command_mints_the_link_for_local(monkeypatch):
    """In LOCAL the command stores the record and prints the fragment link."""
    from parishkit.stewardship import operator_commands

    configuration = configuration_for(DeploymentProfile.LOCAL)
    client = Mock()
    client.set.return_value = True
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda config: None
    )
    monkeypatch.setattr(runtime_web, "valkey_client", lambda config: client)
    monkeypatch.setattr(sessions, "revocation_epoch", lambda: "epoch-7")
    link = local_sign_in.local_sign_in_command(configuration, "Admin@Example.test")
    origin, _, token = link.partition("#")
    assert origin == "https://localhost:8443/admin/local/sign-in"
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    client.set.assert_called_once_with(
        local_sign_in.token_key(token),
        json.dumps(
            {"email": "admin@example.test", "recovery_epoch": "epoch-7"},
            separators=(",", ":"),
        ),
        ex=120,
        nx=True,
    )
    client.connection_pool.disconnect.assert_called_once_with()


def test_cli_refuses_a_production_configuration_generically(
    monkeypatch, capsys, tmp_path
):
    """pk-stewardship local-sign-in prints one fixed refusal, no detail, exit 2."""
    forbid_side_effects(monkeypatch)
    monkeypatch.setattr(
        local_sign_in,
        "load_deployment",
        lambda path: configuration_for(DeploymentProfile.PRODUCTION),
    )
    config = tmp_path / "deployment.yaml"
    config.write_text("deployment: {}\n", encoding="utf-8")
    code = main(["local-sign-in", "--config", str(config), "--email", "a@b.test"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err.startswith("ERROR: test sign-in refused")
    assert "production" not in captured.err and str(config) not in captured.err


def test_cli_prints_the_link_and_requires_its_two_options(monkeypatch, capsys):
    """The command takes exactly --config and --email and prints the link alone."""
    monkeypatch.setattr(local_sign_in, "load_deployment", lambda path: "configuration")
    monkeypatch.setattr(
        local_sign_in,
        "local_sign_in_command",
        lambda configuration, email: f"link-for-{email}",
    )
    assert main(["local-sign-in", "--config", "x.yaml", "--email", "a@b.test"]) == 0
    assert capsys.readouterr().out == "link-for-a@b.test\n"
    # A missing option is a usage error naming it, as for config-check.
    for arguments, missing in (
        (["local-sign-in", "--config", "x.yaml"], "--email"),
        (["local-sign-in", "--email", "a@b.test"], "--config"),
    ):
        with pytest.raises(SystemExit) as exit:
            main(arguments)
        assert exit.value.code == 2
        assert f"local-sign-in requires {missing}" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["local-sign-in", "--config", "x.yaml", "--email", "a@b.test", "--send"])
    assert "options not supported by local-sign-in" in capsys.readouterr().err


# The login page and the static script.
def test_login_page_hides_google_sign_in_only_in_local():
    """LOCAL's login page offers no Google button; other profiles are unchanged."""
    local = render_to_string("stewardship/login.html", {"local_environment": True})
    assert "Sign in with Google" not in local
    assert 'action="/admin/login"' not in local
    assert "not available in the local laptop environment" in local
    assert "<code>tools/stewardship-local.sh sign-in --email E</code>" in local
    assert "works once, within two minutes" in local
    assert "Family campaign portal" in local
    other = render_to_string("stewardship/login.html", {})
    assert "Sign in with Google" in other
    assert 'action="/admin/login"' in other
    assert "local laptop environment" not in other


def test_step_up_prompt_describes_the_command_only_in_local():
    """LOCAL's fresh-authentication prompt cannot offer Google; others still do."""
    local = render_to_string(
        "stewardship/components/reauthenticate.html",
        {"local_environment": True, "next": "/admin/users"},
    )
    assert "Confirm with Google" not in local and "<form" not in local
    assert (
        "<code>tools/stewardship-local.sh sign-in --email you@example.org</code>"
        in local
    )
    assert "refreshes this sign-in in place" in local
    other = render_to_string(
        "stewardship/components/reauthenticate.html", {"next": "/admin/users"}
    )
    assert "Confirm with Google" in other
    assert 'action="/admin/login"' in other and 'value="/admin/users"' in other
    assert "stewardship-local.sh" not in other


def test_static_script_is_shipped_and_the_policy_forbids_inline_script():
    """The fragment mover is a static file, the only kind the CSP allows."""
    static = Path(local_sign_in.__file__).parent / "static" / "stewardship"
    script = (static / "local-sign-in-v1.js").read_text(encoding="utf-8")
    assert '"local-sign-in-token"' in script and "location.hash" in script
    assert "button.disabled = false" in script
    assert "submit()" not in script
    assert "script-src 'self'" in CSP


def test_production_ingress_documents_carry_no_local_route():
    """The pinned Production Compose and Caddy files mention no local sign-in."""
    for name in ("production-Caddyfile.golden", "production-compose.golden.json"):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        assert "/admin/local" not in text
        assert "sign-in" not in text
        assert "local-sign-in" not in text


def test_the_command_imports_before_django_is_configured(tmp_path):
    """The console entry point loads the command without Django settings.

    The command configures Django itself after its LOCAL checks, so a
    module-level import of anything that loads Django models would crash it
    before the refusal (#508). A fresh interpreter with no settings module
    must be able to import it.
    """
    source = Path(__file__).resolve().parents[2] / "src"
    environment = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(source)}
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import parishkit.stewardship.accounts.local_sign_in",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
