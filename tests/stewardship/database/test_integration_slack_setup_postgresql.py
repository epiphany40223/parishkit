"""Slack can be set up, replaced and removed after setup from its own page."""

from dataclasses import replace
from types import MappingProxyType
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.credential_installation import CredentialInstaller
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.accounts.handoff_discovery import publish_handoff
from parishkit.stewardship.accounts.integration_credentials import selection_key
from parishkit.stewardship.accounts.key_files import file_fingerprint
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.credential_runtime import acknowledge_rotations
from parishkit.stewardship.deployment import ServiceRole, load_deployment
from parishkit.stewardship.runtime_grants import runtime_grants

from .auth_builders import signed_in
from .test_configuration_service_postgresql import (  # noqa: F401
    as_config_installer,
    config_role,
)
from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401
from .test_integration_views_postgresql import INDEX, hidden, post

pytestmark = pytest.mark.django_db(transaction=True)
URL = INDEX + "/slack"
TOKEN = b"xoxb-synthetic-slack-token"


@pytest.fixture
def slack(request, monkeypatch, tmp_path, auth_service, google):
    """A signed-in Admin, Slack's installer and a worker reading its folder."""
    request.getfixturevalue("isolated_roles")
    request.getfixturevalue("config_role")
    private = PrivateHandoff("slack", Key("handoff", "active", b"s" * 32))
    with connection.cursor() as cursor:
        cursor.execute(
            "GRANT SELECT, INSERT ON stewardship_public_credential_handoff "
            "TO pk_stewardship_credential_slack"
        )
        tables, columns = runtime_grants(ServiceRole.WEB)
        for table, permissions in tables.items():
            cursor.execute(
                f'GRANT {", ".join(sorted(permissions))} ON "{table}" '
                "TO pk_stewardship_web"
            )
        for table, permissions in columns.items():
            for permission, names in permissions.items():
                selected = ", ".join(f'"{name}"' for name in sorted(names))
                cursor.execute(
                    f'GRANT {permission} ({selected}) ON "{table}" '
                    "TO pk_stewardship_web"
                )
    with identity("pk_stewardship_credential_slack"):
        publish_handoff(private)
    deployment = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    path = deployment.paths["credentials"] / "slack" / "credential"
    path.parent.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.publish_single_process_receipts",
        lambda configuration, receipts: None,
    )
    browser, _ = signed_in()
    return {
        "service": auth_service,
        "browser": browser,
        "installer": CredentialInstaller(
            CredentialFiles(path, private), validate=lambda value: value == TOKEN
        ),
        "worker": replace(
            deployment,
            service_role=ServiceRole.WORKER,
            secrets=MappingProxyType({"slack": path}),
        ),
    }


def records(value):
    """The applied integration records, by kind."""
    return {
        row["values"]["kind"]: row
        for row in value["service"]
        .store.active()
        .document()["sections"]["integrations"]
    }


def test_slack_is_set_up_after_setup_and_can_be_removed(slack):
    """One save adds Slack with its first token; removal takes it out again."""
    browser, store = slack["browser"], slack["service"].store
    assert b"Set up Slack" in browser.get(INDEX).content
    page = browser.get(URL)
    assert page.status_code == 200 and b"Slack is not set up" in page.content
    with identity("pk_stewardship_web"):
        response = post(
            browser,
            URL,
            {
                "action": "preview",
                "base_digest": store.active().digest,
                "channel_id": "C0123ABC",
                "intent": hidden(page, "intent"),
                "candidate": TOKEN.decode(),
            },
        )
    assert response.status_code == 302, response.content
    row = SecretReplacementRequest.objects.get(target="slack")
    assert row.expected_fingerprint is None
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    assert selection.patch[0]["operation"] == "add"
    with identity("pk_stewardship_credential_slack"):
        assert slack["installer"].run_once().state == "awaiting_ack"
    with identity("pk_stewardship_worker"):
        assert acknowledge_rotations(slack["worker"], {}) == [row.pk]
    with identity("pk_stewardship_credential_slack"):
        assert slack["installer"].run_once().state == "applied"
    with as_config_installer():
        assert (
            install_request(
                store, request_id=selection.pk, correlation_id=uuid4()
            ).state
            == "applied"
        )
    added = records(slack)["slack"]["values"]
    assert added["credential_fingerprint"] == file_fingerprint(TOKEN)
    assert added["settings"] == {"channel_id": "C0123ABC"}
    page = browser.get(URL).content
    assert b"Key updated." in page and b"Remove Slack" in page and TOKEN not in page
    preview = hidden(post(browser, URL, {"action": "remove"}), "preview")
    response = post(browser, URL, {"action": "confirm", "preview": preview})
    assert response.status_code == 302
    removal = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    with as_config_installer():
        assert (
            install_request(store, request_id=removal.pk, correlation_id=uuid4()).state
            == "applied"
        )
    assert "slack" not in records(slack)


def test_settings_alone_cannot_set_up_slack(slack):
    """Without a token, Slack is not added and nothing is queued."""
    browser, store = slack["browser"], slack["service"].store
    page = browser.get(URL)
    response = post(
        browser,
        URL,
        {
            "action": "preview",
            "base_digest": store.active().digest,
            "channel_id": "C0123ABC",
            "intent": hidden(page, "intent"),
            "candidate": "",
        },
    )
    assert response.status_code == 400
    assert not ConfigurationChangeRequest.objects.exists()
    assert not SecretReplacementRequest.objects.exists()
