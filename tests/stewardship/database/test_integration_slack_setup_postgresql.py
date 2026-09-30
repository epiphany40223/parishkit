"""Slack can be set up, replaced and removed after setup from its own page."""

from dataclasses import replace
from types import MappingProxyType
from uuid import UUID, uuid4

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
from .campaign_builders import change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_configuration_service_postgresql import (  # noqa: F401
    as_config_installer,
    config_role,
)
from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401
from .test_integration_views_postgresql import INDEX, hidden, post

pytestmark = pytest.mark.django_db(transaction=True)
URL = INDEX + "/slack"
TOKEN = b"xoxb-synthetic-slack-token"
AGAIN = b"xoxb-synthetic-second-slack-token"


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
            CredentialFiles(path, private),
            validate=lambda value: value in {TOKEN, AGAIN},
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


def add_slack(slack, token, channel="C0123ABC"):
    """Save a channel with a token on the Slack page; return the key's request."""
    browser, store = slack["browser"], slack["service"].store
    page = browser.get(URL)
    assert page.status_code == 200 and b"Slack is not set up" in page.content
    with identity("pk_stewardship_web"):
        response = post(
            browser,
            URL,
            {
                "action": "preview",
                "base_digest": store.active().digest,
                "channel_id": channel,
                "intent": hidden(page, "intent"),
                "candidate": token.decode(),
            },
        )
    assert response.status_code == 302, response.content
    return SecretReplacementRequest.objects.order_by("-created_at").first()


def install_key(slack, row):
    """Check, install and acknowledge a staged Slack key."""
    with identity("pk_stewardship_credential_slack"):
        assert slack["installer"].run_once().state == "awaiting_ack"
    with identity("pk_stewardship_worker"):
        assert acknowledge_rotations(slack["worker"], {}) == [row.pk]
    with identity("pk_stewardship_credential_slack"):
        assert slack["installer"].run_once().state == "applied"


def apply(slack, request_id):
    """Run the configuration installer once for one request."""
    with as_config_installer():
        return install_request(
            slack["service"].store,
            request_id=UUID(str(request_id)),
            correlation_id=uuid4(),
        )


def remove_slack(slack):
    """Preview, confirm and apply Remove Slack."""
    browser = slack["browser"]
    preview_page = post(browser, URL, {"action": "remove"})
    assert b"To set Slack up again later" in preview_page.content
    # Removal is reviewed like any other change (#196).
    assert flow_steps(preview_page.content) == (STEPS, "Review")
    preview = hidden(preview_page, "preview")
    response = post(browser, URL, {"action": "confirm", "preview": preview})
    assert response.status_code == 302
    assert apply(slack, response["Location"].rsplit("/", 1)[-1]).state == "applied"
    # The status names Slack and leads back to its page, which still opens.
    status = browser.get(response["Location"]).content
    assert f'<a href="{URL}">Return to Slack notifications</a>'.encode() in status
    assert "slack" not in records(slack)


def test_slack_is_set_up_removed_and_set_up_again(slack):
    """One save adds Slack with its first token; removal takes it out again.

    Setting it up again afterwards must work (#307 M2): the removed
    integration's key file is still installed, so the new key names it as
    its predecessor, and the page shows no stale status line in between.
    """
    browser = slack["browser"]
    assert b"Set up Slack" in browser.get(INDEX).content
    row = add_slack(slack, TOKEN)
    assert row.expected_fingerprint is None
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    assert selection.patch[0]["operation"] == "add"
    install_key(slack, row)
    assert apply(slack, selection.pk).state == "applied"
    added = records(slack)["slack"]["values"]
    assert added["credential_fingerprint"] == file_fingerprint(TOKEN)
    assert added["settings"] == {"channel_id": "C0123ABC"}
    page = browser.get(URL).content
    assert b"Key updated." in page and b"Remove Slack" in page and TOKEN not in page
    remove_slack(slack)
    # The old key cannot bring Slack back without a newly pasted, checked
    # token (#338 review): its selection is refused and not offered.
    select = f"/admin/configuration/credentials/{row.pk}/select"
    count = ConfigurationChangeRequest.objects.count()
    assert browser.get(select).status_code == 409
    assert ConfigurationChangeRequest.objects.count() == count
    status = browser.get(f"/admin/configuration/credentials/{row.pk}").content
    assert select.encode() not in status
    # Removed: no leftover "installed" line or dead Finish switching link.
    page = browser.get(URL).content
    assert b"Slack is not set up" in page
    assert b"/select" not in page and b"switching to it did not" not in page
    assert b"Key updated." not in page
    # Set it up again, with a new token and channel.
    again = add_slack(slack, AGAIN, channel="C0456DEF")
    assert again.pk != row.pk
    assert again.expected_fingerprint == file_fingerprint(TOKEN)
    install_key(slack, again)
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(again.pk)
    )
    assert apply(slack, selection.pk).state == "applied"
    readded = records(slack)["slack"]["values"]
    assert readded["credential_fingerprint"] == file_fingerprint(AGAIN)
    assert readded["settings"] == {"channel_id": "C0456DEF"}
    page = browser.get(URL).content
    assert b"Key updated." in page and b"Remove Slack" in page


def test_a_failed_slack_add_can_finish_switching(slack):
    """A Slack add whose own selection failed says so and re-adds on Finish.

    The token is installed but Slack is not configured, so the page shows
    the error line, and Finish switching repeats the whole add on the
    current settings.
    """
    browser, store = slack["browser"], slack["service"].store
    row = add_slack(slack, TOKEN)
    install_key(slack, row)
    base = store.active()
    parish = base.document()["sections"]["parish"][0]
    change(
        store,
        base,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Renamed first"},
            }
        ],
    )
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    result = apply(slack, selection.pk)
    assert result.state == "failed" and result.failure_code == "stale_base"
    page = browser.get(URL).content
    assert b"Slack alerts are not sent" in page
    select = f"/admin/configuration/credentials/{row.pk}/select"
    assert select.encode() in page
    status = browser.get(f"/admin/configuration/credentials/{row.pk}").content
    assert select.encode() in status
    preview = hidden(browser.get(select), "preview")
    with identity("pk_stewardship_web"):
        response = post(browser, select, {"action": "confirm", "preview": preview})
    assert response.status_code == 302, response.content
    finish = response["Location"].rsplit("/", 1)[-1]
    assert apply(slack, finish).state == "applied"
    added = records(slack)["slack"]["values"]
    assert added["credential_fingerprint"] == file_fingerprint(TOKEN)
    assert added["settings"] == {"channel_id": "C0123ABC"}
    assert b"Key updated." in browser.get(URL).content


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
