"""Same-origin Admin return paths for sign-in and error-page links."""

import pytest

from parishkit.stewardship.web.namespaces import admin_return_path


@pytest.mark.parametrize(
    "value",
    [
        "/admin/",
        "/admin/setup",
        "/admin/setup/credentials/parishsoft",
        "/admin/campaigns/0b6f7e1c-7a44-4b43-9d52-2c6a1e0f3a10/delivery-control",
        "/admin/logs?source=audit&event=admin_login",
        "/admin/users/",
    ],
)
def test_same_origin_admin_paths_are_kept(value):
    """Ordinary Admin pages, with a plain query, are valid destinations."""
    assert admin_return_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        42,
        "/",
        "/admin",
        "/family/",
        "https://attacker.example/admin/",
        "//attacker.example/admin/",
        "/admin//attacker.example",
        "/\\attacker.example",
        "/admin/\\attacker.example",
        "/admin/../family",
        "/admin/./setup",
        "/admin/%2e%2e/family",
        "/admin/%2F%2Fattacker.example",
        "/admin/setup\r\nLocation: https://attacker.example",
        "/admin/setup\x00",
        "/admin/set up",
        "/admin/ünicode",
        "/admin/setup#fragment",
        "/admin/setup?next=//attacker.example",
        "/admin/login",
        "/admin/logout",
        "/admin/oauth/callback",
        "/admin/local/sign-in",
        "javascript:alert(1)",
        "/admin/" + "a" * 1100,
    ],
)
def test_everything_else_falls_back_to_admin_home(value):
    """Off-origin, ambiguous, control-character and auth routes return home."""
    assert admin_return_path(value) == "/admin/"
