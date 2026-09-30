"""Replacing the backup encryption key from the Admin portal (#198) on real SQL."""

import base64
import json
import re
from html import unescape
from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship import backup, backup_sealing
from parishkit.stewardship.accounts.backup_key import key_status
from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in, stale_sign_in
from .campaign_builders import change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_backup_offsite_postgresql import apply
from .test_backup_postgresql import (
    backup_login,
    recorded,
)
from .test_integration_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/configuration/integrations/backup_key"


def keypair():
    """A new key pair: (private key, canonical public key text)."""
    private, public = backup_sealing.generate_keypair()
    return backup_sealing.PrivateKey(base64.b64decode(private)), public.strip()


def field(response, name):
    """A hidden field's value, as the browser would submit it."""
    match = re.search(rf'name="{name}" value="([^"]+)"', response.content.decode())
    return unescape(match.group(1))


def challenge_of(response):
    """The sealed challenge line the page shows."""
    return re.search(r"PKBKP1:[A-Za-z0-9+/=]+", response.content.decode()).group(0)


def configured(store):
    """The applied backup_key settings, if any."""
    return [
        item["values"]["settings"]
        for item in store.active().document()["sections"]["integrations"]
        if item["values"]["kind"] == "backup_key"
    ]


def replace_key(browser, private, public, *, code=None):
    """Paste the key, prove it with the private key and return the review."""
    page = post(browser, URL, {"action": "challenge", "public_key": public})
    assert page.status_code == 200, page.content
    proof = backup_sealing.open_proof(challenge_of(page), private)
    return post(
        browser,
        URL,
        {
            "action": "preview",
            "intent": field(page, "intent"),
            "code": code or backup_sealing.display_code(proof).lower(),
        },
    )


def test_admin_replaces_the_key_after_proving_the_private_key(auth_service, google):
    """Paste, prove, review, confirm: the next backup's key, audited and shown."""
    store = auth_service.store
    browser, _ = signed_in()
    index = browser.get("/admin/configuration/integrations").content
    assert b'href="/admin/configuration/integrations/backup_key">Backup' in index
    page = browser.get(URL)
    assert page.status_code == 200
    assert b"No backup has run yet" in page.content
    # Pasting and proving the key is the first step of the change (#196).
    assert flow_steps(page.content) == (STEPS, "Make changes")
    assert b"Keep the old private key" in page.content
    # Once a backup has run, the page names the key it used.
    recorded(recipient_fingerprint="d" * 16)
    assert ("d" * 16).encode() in browser.get(URL).content
    private, public = keypair()
    # Not a key, and the current key itself, are refused before any proof.
    bad = post(browser, URL, {"action": "challenge", "public_key": "{not a key}"})
    assert bad.status_code == 400 and b"not a backup public key" in bad.content
    assert b"{not a key}" not in bad.content
    # A wrong code shows the same challenge again and saves nothing.
    wrong = replace_key(browser, private, public, code="0000-0000-0000")
    assert wrong.status_code == 400 and b"does not match" in wrong.content
    assert challenge_of(wrong)
    # A challenge made for this key cannot be answered with another key.
    other, _ = keypair()
    page = post(browser, URL, {"action": "challenge", "public_key": public})
    with pytest.raises(backup_sealing.SealError):
        backup_sealing.open_proof(challenge_of(page), other)
    review = replace_key(browser, private, public)
    assert review.status_code == 200, review.content
    assert flow_steps(review.content) == (STEPS, "Review")
    fingerprint = backup_sealing.parse_public_key(public).fingerprint
    assert fingerprint.encode() in review.content
    assert b"keep it until they have all been deleted" in review.content
    assert not ConfigurationChangeRequest.objects.exists()
    response = post(
        browser, URL, {"action": "confirm", "preview": field(review, "preview")}
    )
    request = ConfigurationChangeRequest.objects.get()
    # The intake audit entry is written with the request itself.
    assert AuditEvent.objects.filter(
        event_type="config_request_staged", subject_id=request.pk
    ).exists()
    apply(store, response)
    assert AuditEvent.objects.filter(
        event_type="config_request_applied", subject_id=request.pk
    ).exists()
    # The status sits under the key's own page, by name.
    status = browser.get(response["Location"]).content
    assert f'<li><a href="{URL}">Backup encryption key</a></li>'.encode() in status
    assert f'<a href="{URL}">Return to Backup encryption key</a>'.encode() in status
    assert configured(store) == [{"public_key": public}]
    shown = browser.get(URL).content
    assert fingerprint.encode() in shown and b"Set on this page" in shown
    # The backup profile seals the next set to it.
    with backup_login(), transaction.atomic():
        assert backup.configured_recipient().fingerprint == fingerprint
    # A second replacement updates the same record rather than adding one.
    private2, public2 = keypair()
    apply(
        store,
        post(
            browser,
            URL,
            {
                "action": "confirm",
                "preview": field(replace_key(browser, private2, public2), "preview"),
            },
        ),
    )
    assert configured(store) == [{"public_key": public2}]
    # Pasting the in-use key again, or its private half, is refused.
    again = post(browser, URL, {"action": "challenge", "public_key": public2})
    assert again.status_code == 400 and b"already the backup" in again.content
    leaked = base64.b64encode(bytes(private2)).decode()
    refused = post(browser, URL, {"action": "challenge", "public_key": leaked})
    assert refused.status_code == 400 and b"PRIVATE key" in refused.content
    assert leaked.encode() not in refused.content
    # Every Administrator is told: the home page's security events name the
    # change, who made it and both key IDs, audited like a widened rule.
    events = PolicySecurityEvent.objects.filter(kind="backup_key_replaced")
    assert [(e.before_roles, e.after_roles) for e in events.order_by("created_at")] == [
        (["d" * 16], [fingerprint]),
        ([fingerprint], [backup_sealing.parse_public_key(public2).fingerprint]),
    ]
    assert (
        AuditEvent.objects.filter(
            event_type="policy_security_event", subject_id__in=events.values("id")
        ).count()
        == 2
    )
    home = browser.get("/admin/").content.decode()
    assert "Backup encryption key replaced" in home
    assert f"<code>{fingerprint}</code>" in home


def private_key_text(*, top_bit):
    """A new pair's private key text whose top bit is (or is not) set."""
    while True:
        private, _ = backup_sealing.generate_keypair()
        if bool(base64.b64decode(private)[31] & 0x80) is top_bit:
            return private.strip()


def test_a_pasted_private_key_is_refused_or_never_shown_back(auth_service, google):
    """The likelier mistake, the new pair's private key, never reaches the page."""
    browser, _ = signed_in()
    flagged = private_key_text(top_bit=True)
    refused = post(browser, URL, {"action": "challenge", "public_key": flagged})
    assert refused.status_code == 400 and b"PRIVATE key" in refused.content
    assert flagged.encode() not in refused.content
    # Half of private keys look like public keys; such a paste gets a
    # challenge nobody can answer, and the page (intent included) never
    # carries the pasted text.
    hidden = private_key_text(top_bit=False)
    page = post(browser, URL, {"action": "challenge", "public_key": hidden})
    assert page.status_code == 200
    assert hidden.encode() not in page.content
    from django.core import signing

    from parishkit.stewardship.accounts.backup_key import PROOF_SALT

    intent = signing.loads(field(page, "intent"), salt=PROOF_SALT)
    assert hidden not in json.dumps(intent)
    with pytest.raises(backup_sealing.SealError):
        backup_sealing.open_proof(
            challenge_of(page),
            backup_sealing.PrivateKey(base64.b64decode(hidden)),
        )


def test_a_stale_sign_in_steps_up_after_the_code_then_reviews(auth_service, google):
    """Time at the key machine costs a Google step-up, not the proof."""
    store = auth_service.store
    browser, _ = signed_in()
    private, public = keypair()
    page = post(browser, URL, {"action": "challenge", "public_key": public})
    code = backup_sealing.open_proof(challenge_of(page), private)
    stale_sign_in()
    held = post(
        browser,
        URL,
        {"action": "preview", "intent": field(page, "intent"), "code": code},
    )
    assert held.status_code == 200 and b"Code accepted" in held.content
    assert b'name="preview"' not in held.content
    # Still stale: the page keeps asking, and keeps the proved change.
    assert b"Code accepted" in browser.get(URL).content
    signed_in(browser)
    review = browser.get(URL)
    fingerprint = backup_sealing.parse_public_key(public).fingerprint
    assert fingerprint.encode() in review.content
    # The change is used once: reloading shows the ordinary page.
    assert b"Code accepted" not in browser.get(URL).content
    apply(
        store,
        post(browser, URL, {"action": "confirm", "preview": field(review, "preview")}),
    )
    assert configured(store) == [{"public_key": public}]


def test_confirming_needs_a_recent_sign_in(auth_service, google):
    """A review left open past the fresh window cannot be confirmed."""
    browser, _ = signed_in()
    private, public = keypair()
    review = replace_key(browser, private, public)
    stale_sign_in()
    response = post(
        browser, URL, {"action": "confirm", "preview": field(review, "preview")}
    )
    assert response.status_code == 403
    assert not ConfigurationChangeRequest.objects.exists()


def test_starting_a_key_change_needs_a_recent_sign_in(auth_service, google):
    """The Google step-up comes before the offline proof, not after it."""
    browser, _ = signed_in()
    _, public = keypair()
    stale_sign_in()
    assert b"confirm your Google sign-in" in browser.get(URL).content
    response = post(browser, URL, {"action": "challenge", "public_key": public})
    assert response.status_code == 403
    assert "PKBKP1:" not in response.content.decode()


def test_a_proof_is_bound_to_its_administrator_and_settings(auth_service, google):
    """A forged, stale or tampered intent is refused and records nothing."""
    store = auth_service.store
    browser, _ = signed_in()
    private, public = keypair()
    page = post(browser, URL, {"action": "challenge", "public_key": public})
    intent = field(page, "intent")
    code = backup_sealing.open_proof(challenge_of(page), private)
    forged = post(
        browser, URL, {"action": "preview", "intent": intent + "x", "code": code}
    )
    assert forged.status_code == 400
    extra = post(
        browser,
        URL,
        {"action": "preview", "intent": intent, "code": code, "public_key": public},
    )
    assert extra.status_code == 400
    version = store.active()
    record = version.document()["sections"]["parish"][0]
    change(
        store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": record["id"],
                "values": {"name": "Changed Parish"},
            }
        ],
    )
    stale = post(browser, URL, {"action": "preview", "intent": intent, "code": code})
    assert stale.status_code == 409
    # Only the parish-name change above was ever requested.
    assert ConfigurationChangeRequest.objects.count() == 1


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_the_backup_key_page_is_admin_only(auth_service, google, role):
    """Readers cannot see or start a key change."""
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    _, public = keypair()
    assert browser.get(URL).status_code == 403
    response = post(browser, URL, {"action": "challenge", "public_key": public})
    assert response.status_code == 403


def test_the_web_reads_the_newest_backup_key_and_nothing_else():
    """The page's fallback fingerprint comes from the backup record (read only)."""
    from types import SimpleNamespace

    recorded(recipient_fingerprint="f" * 16)
    configuration = SimpleNamespace(
        active_configuration=SimpleNamespace(canonical_document={"sections": {}})
    )
    with task_login(ServiceRole.WEB, exact=True), transaction.atomic():
        status = key_status(configuration)
    assert (status.source, status.fingerprint) == ("installed", "f" * 16)
