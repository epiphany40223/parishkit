"""``parish preview`` and ``parish confirm`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
review and apply parish profile changes through the Parish settings page's
own form, review and confirmation, so a token from either channel confirms
on the other.
"""

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import parish_views, user_views
from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent

from .automation_builders import as_staff, paired
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_parish_confirm"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def profile(admin):
    """The applied parish profile's values."""
    return admin.service.store.active().document()["sections"]["parish"][0]["values"]


def preview(admin, secret, changes, version=None):
    """Run ``parish preview`` with ``changes`` (the ``parish`` member)."""
    version = version or admin.service.store.active().digest
    return admin(
        "parish",
        "preview",
        "--expected-version",
        version,
        "--changes",
        json.dumps({"parish": changes}),
        secret=secret,
    )


def confirm(admin, secret, token):
    """Run ``parish confirm`` with ``token``."""
    return admin("parish", "confirm", "--token", token, secret=secret)


def apply(admin, secret, changes):
    """Preview, confirm and install a change; return the preview's result."""
    code, document, _ = preview(admin, secret, changes)
    assert code == 0, document
    code, confirmed, _ = confirm(admin, secret, document["result"]["preview"]["token"])
    assert code == 0 and confirmed["result"]["created"], confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"
    return document["result"]


def events():
    """How many ``parish confirm`` events are recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def refused(document, code):
    """The error document's code is ``code``; return its field names."""
    assert document["error"]["code"] == code, document
    assert "result" not in document
    return [problem["field"] for problem in document["error"].get("fields", [])]


def test_preview_and_confirm_change_the_profile_as_the_page(admin):
    """The review lists stored values and the page's timezone note; nothing
    changes until confirmed, which records the request and the command's
    event; a repeated confirm returns the same request, recording nothing."""
    _, secret, _ = paired(admin.service)
    before = profile(admin)
    zone = "America/Denver" if before["timezone"] != "America/Denver" else "UTC"
    version = admin.service.store.active().digest
    code, document, _ = preview(
        admin, secret, {"phone": "(212) 555-0123", "timezone": zone}
    )
    assert code == 0, document
    result = document["result"]
    assert result["version"] == version
    assert result["changes"] == [
        {"field": "timezone", "before": before["timezone"], "after": zone},
        {"field": "phone", "before": before["phone"], "after": "+12125550123"},
    ]
    assert result["notes"] == [str(parish_views.TIMEZONE_NOTE)]
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0

    token = result["preview"]["token"]
    code, confirmed, _ = confirm(admin, secret, token)
    assert code == 0 and confirmed["result"]["created"], confirmed
    request_id = confirmed["result"]["request"]["request_id"]
    assert events() == 1
    install_request(
        admin.service.store, request_id=UUID(request_id), correlation_id=uuid4()
    )
    after = profile(admin)
    assert (after["phone"], after["timezone"]) == ("+12125550123", zone)
    assert after["name"] == before["name"]
    code, again, _ = confirm(admin, secret, token)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events() == 1 and ConfigurationChangeRequest.objects.count() == 1


def test_the_giving_link_is_set_then_removed(admin):
    """A null giving link removes it, as clearing the page's field does."""
    _, secret, _ = paired(admin.service)
    link = "https://giving.example.org/parish"
    result = apply(admin, secret, {"online_giving_url": link})
    assert result["changes"] == [
        {"field": "online_giving_url", "before": None, "after": link}
    ]
    assert result["notes"] == []
    result = apply(admin, secret, {"online_giving_url": None})
    assert result["changes"] == [
        {"field": "online_giving_url", "before": link, "after": None}
    ]
    assert "online_giving_url" not in profile(admin)


def test_a_command_preview_confirms_on_the_page(admin):
    """Tokens are bound to the Administrator, not the channel."""
    browser, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, {"name": "Saint Somewhere"})
    assert code == 0, document
    response = browser.post(
        "/admin/parish/settings/",
        {
            "action": "confirm",
            "preview": document["result"]["preview"]["token"],
            "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        },
    )
    assert response.status_code == 302, response.content
    assert ConfigurationChangeRequest.objects.count() == 1 and events() == 0


def test_preview_refusals(admin):
    """The page's refusals, each with nothing requested."""
    _, secret, _ = paired(admin.service)
    current = profile(admin)
    code, document, _ = preview(admin, secret, {"name": "New"}, version="0" * 64)
    assert code == 1 and refused(document, "stale_version") == []
    code, document, _ = preview(admin, secret, {"name": current["name"]})
    assert code == 1 and refused(document, "invalid") == ["parish"]
    assert "No settings have changed." in json.dumps(document["error"])
    code, document, _ = preview(admin, secret, {"phone": "123", "name": ""})
    assert code == 1
    assert sorted(refused(document, "invalid")) == ["parish.name", "parish.phone"]
    code, document, _ = preview(
        admin, secret, {"website": "https://example.org/parish?tab=1"}
    )
    # The page's form refuses the website at its field (#1330), so the
    # refusal names the field, as the other field errors do.
    assert code == 1 and refused(document, "invalid") == ["parish.website"]
    code, document, _ = preview(admin, secret, {"logo": "x"})
    assert code == 1 and refused(document, "invalid") == ["parish.logo"]
    # Only the giving link may be null, and the date format never blank: the
    # page's form reads a blank date format as the default style.
    for changes in ({"date_format": None}, {"date_format": ""}):
        code, document, _ = preview(admin, secret, changes)
        assert code == 1 and refused(document, "invalid") == ["parish.date_format"]
    code, document, _ = preview(admin, secret, {"name": None, "timezone": None})
    assert code == 1
    assert refused(document, "invalid") == ["parish.name", "parish.timezone"]
    assert not ConfigurationChangeRequest.objects.exists()


def test_confirm_refuses_foreign_tokens(admin):
    """An altered token, another page's or another Administrator's."""
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, {"name": "Saint Elsewhere"})
    token = document["result"]["preview"]["token"]
    code, document, _ = confirm(admin, secret, token[:-2] + "xx")
    assert code == 1 and refused(document, "invalid") == []
    payload = signing.loads(token, salt=parish_views.SALT)
    code, document, _ = confirm(
        admin, secret, signing.dumps(payload, salt=user_views.SALT)
    )
    assert code == 1 and refused(document, "invalid") == []
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=parish_views.SALT,
    )
    code, document, _ = confirm(admin, secret, other)
    assert code == 1 and refused(document, "denied") == []
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0


def test_refused_sessions(admin, monkeypatch):
    """A read-only session and Staff are denied; an ended session is exit 5."""
    _, reader, _ = paired(admin.service, scope="read-only")
    code, document, _ = preview(admin, reader, {"name": "X"})
    assert code == 1 and refused(document, "denied") == []
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, {"name": "Saint Anywhere"})
    token = document["result"]["preview"]["token"]
    revoke(admin.service, secret)
    for code, document, _ in (
        preview(admin, secret, {"name": "X"}),
        confirm(admin, secret, token),
    ):
        assert code == 5 and refused(document, "session_ended") == []
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        preview(admin, staff, {"name": "X"}),
        confirm(admin, staff, token),
    ):
        assert code == 1 and refused(document, "denied") == []
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0
