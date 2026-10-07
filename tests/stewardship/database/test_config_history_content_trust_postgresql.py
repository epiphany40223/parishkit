"""Configuration changes keep applying after content text rules evolve (#187).

A deployment's applied history can hold content validated under an older
sanitizer. Tightening the sanitizer must not make that history "invalid" and
block every later configuration change; only new or changed content meets the
current rules.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import content_schema
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.campaigns.models import Campaign

from ..content_factory import content
from ..policy_factory import address
from .campaign_builders import add_draft, change

pytestmark = pytest.mark.django_db(transaction=True)

# Canonical under the old rules the fixture simulates; today's sanitizer
# rewrites a bare <div> block, so it is not in today's canonical form.
LEGACY_HTML = "<div>Welcome to {{ parish_name }}.</div>"


def lenient(html, text=None):
    """An older sanitizer that accepted any HTML as already canonical."""
    return SimpleNamespace(html=html, text=text)


@pytest.fixture
def legacy_history(auth_service, monkeypatch):
    """Apply a draft and a welcome page under the older, looser text rules."""
    store = auth_service.store
    actor = uuid4()
    result, row, mail = add_draft(store, store.active(), actor)
    assert result.state == "applied"
    page = content(row["id"], html=LEGACY_HTML, text="Welcome to {{ parish_name }}.")
    with monkeypatch.context() as patched:
        patched.setattr(content_schema, "prepare_content", lenient)
        added = change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "content", **page}],
        )
    assert added.state == "applied"

    # Today's rules reject the stored text when it is authored anew (on an
    # unused slot, so only the text rule can refuse it) and accept the same
    # record in today's canonical form.
    def probe(html):
        return build_candidate(
            store.active(),
            [
                {
                    "operation": "add",
                    "section": "content",
                    **content(
                        row["id"],
                        slot="login_help",
                        html=html,
                        text="Welcome to {{ parish_name }}.",
                    ),
                }
            ],
            candidate_id=uuid4(),
        )

    with pytest.raises(ConfigError):
        probe(LEGACY_HTML)
    probe("<p>Welcome to {{ parish_name }}.</p>")
    return SimpleNamespace(store=store, actor=actor, campaign=row, schedule=mail)


def _parish(store):
    return store.active().document()["sections"]["parish"][0]


def _integration(store):
    return store.active().document()["sections"]["integrations"][0]


PATHS = {
    "parish settings": lambda h: [
        {
            "operation": "update",
            "section": "parish",
            "id": _parish(h.store)["id"],
            "values": {"phone": "+12025550199"},
        }
    ],
    "integration settings": lambda h: [
        {
            "operation": "update",
            "section": "integrations",
            "id": _integration(h.store)["id"],
            "values": {
                "settings": _integration(h.store)["values"]["settings"]
                | {"nightly_time": "03:00"}
            },
        }
    ],
    "campaign settings": lambda h: [
        {
            "operation": "update",
            "section": "campaigns",
            "id": h.campaign["id"],
            "values": {"name": "Renamed campaign"},
        }
    ],
    "mail schedules": lambda h: [
        {
            "operation": "update",
            "section": "schedules",
            "id": h.schedule["id"],
            "values": {"time": "10:00:00"},
        }
    ],
    "new content": lambda h: [
        {
            "operation": "add",
            "section": "content",
            **content(h.campaign["id"], slot="login_help"),
        }
    ],
    "portal users": lambda h: [
        {"operation": "add", "section": "login_rules", **address("new@example.org")}
    ],
}


@pytest.mark.parametrize("path", sorted(PATHS))
def test_every_edit_path_installs_over_legacy_content(legacy_history, path):
    """One real change per settings path verifies the history and applies."""
    history = legacy_history
    before = history.store.active().digest
    result = change(
        history.store, history.store.active(), history.actor, PATHS[path](history)
    )
    assert result.state == "applied", (path, result)
    assert history.store.active().digest != before
    assert Campaign.objects.exists()


def test_changed_content_still_meets_current_rules(legacy_history):
    """Trust covers exact carried-over records only, never an edited one."""
    history = legacy_history
    stored = next(
        record
        for record in history.store.active().document()["sections"]["content"]
        if record["values"]["html"] == LEGACY_HTML
    )

    def replace(html):
        return build_candidate(
            history.store.active(),
            [
                {"operation": "remove", "section": "content", "id": stored["id"]},
                {
                    "operation": "add",
                    "section": "content",
                    "id": str(uuid4()),
                    "values": stored["values"] | {"html": html},
                },
            ],
            candidate_id=uuid4(),
        )

    with pytest.raises(ConfigError):
        replace("<div>Edited {{ parish_name }}.</div>")
    # Control: the identical operation in canonical form is accepted, so the
    # refusal above comes from the text rule, not the operation's structure.
    replace("<p>Edited {{ parish_name }}.</p>")


def test_unverifiable_history_fails_visibly_instead_of_hanging(
    auth_service, google, monkeypatch
):
    """A request that can never verify ends failed, with a plain reason."""
    from parishkit.stewardship.accounts import configuration_snapshots

    from .auth_builders import signed_in

    store = auth_service.store
    monkeypatch.setattr(
        configuration_snapshots, "_verify_history", lambda *args, **kwargs: False
    )
    from parishkit.stewardship.accounts.models import PortalSession

    browser, _ = signed_in()
    actor = PortalSession.objects.latest("authenticated_at").principal_id
    parish = _parish(store)
    result = change(
        store,
        store.active(),
        actor,
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"phone": "+12025550188"},
            }
        ],
    )
    assert (result.state, result.failure_code) == ("failed", "invalid_candidate")
    page = browser.get(f"/admin/configuration/requests/{result.request_id}")
    assert page.status_code == 200
    assert b"did not pass validation against the saved settings" in page.content
    assert b"data-live-pending" not in page.content


def test_from_name_edit_installs_over_legacy_content(request, monkeypatch):
    """The live #187 report: the email From name edit, through the real editor."""
    import re
    from html import unescape

    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )
    from parishkit.stewardship.accounts.key_files import file_fingerprint
    from parishkit.stewardship.accounts.request_models import (
        ConfigurationChangeRequest,
    )

    from . import campaign_builders
    from .auth_builders import signed_in

    original = campaign_builders.configuration_document

    def document():
        """Initial authority with outgoing email configured, as after setup."""
        value = original()
        value["sections"]["integrations"] += [
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "google_workspace",
                    "settings": {"delegated_email": "sender@example.org"},
                    "credential_fingerprint": file_fingerprint(b"synthetic-key"),
                },
            },
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "email",
                    "settings": {
                        "sender": "sender@example.org",
                        "reply_to": "reply@example.org",
                    },
                    "credential_fingerprint": None,
                },
            },
        ]
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    history = request.getfixturevalue("legacy_history")
    request.getfixturevalue("google")
    browser, _ = signed_in()
    url = "/admin/system/integrations/email/"

    def post(values):
        return browser.post(
            url,
            values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
        )

    page = post(
        {
            "action": "preview",
            "base_digest": history.store.active().digest,
            "sender": "sender@example.org",
            "reply_to": "reply@example.org",
            "sender_name": "Example Parish Stewardship",
        }
    )
    assert page.status_code == 200, page.content
    preview = unescape(
        re.search(r'name="preview" value="([^"]+)"', page.content.decode()).group(1)
    )
    confirmed = post({"action": "confirm", "preview": preview})
    assert confirmed.status_code == 302
    row = ConfigurationChangeRequest.objects.get(
        pk=confirmed["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    result = install_request(history.store, request_id=row.pk, correlation_id=uuid4())
    assert result.state == "applied", result
    email = next(
        record
        for record in history.store.active().document()["sections"]["integrations"]
        if record["values"]["kind"] == "email"
    )
    assert email["values"]["settings"]["sender_name"] == "Example Parish Stewardship"
