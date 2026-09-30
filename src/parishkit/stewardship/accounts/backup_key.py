"""Set or replace the public key backups are sealed to, from the Admin portal.

Backups are sealed to an X25519 public key whose private half the parish
keeps off the server (see ``backup_sealing``). The operator installs the
first public key by hand as the ``backup_data`` file; this page lets an
Administrator store a replacement as the one setting of the key-less
``backup_key`` integration, which every later backup seals to (#198).

A pasted key is admitted only after the Administrator proves they hold its
private key: the page seals a short random code to it, and they type back
the code ``pk-stewardship backup-prove`` prints on their key machine. The
code's keyed hash and the pasted text, encrypted under a server key, travel
in the page's signed intent, so nothing unproved is stored here and nothing
pasted is ever shown back: a private key pasted by mistake cannot pass the
proof and never reaches the page source. The change itself is an ordinary
configuration request: its intake audit is written in the same transaction,
the configuration installer applies it, and the activation records a
security event that every Administrator is told about.

Starting a change and confirming it both need a Google sign-in from the
last five minutes. A proved change whose sign-in went stale while the
Administrator was at the key machine is kept in the session (a proved
public key only) across the Google step-up, then reviewed.
"""

import base64
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from django.core import signing
from django.shortcuts import render
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey
from nacl.secret import SecretBox

from parishkit.stewardship.backup_sealing import (
    SealError,
    fingerprint,
    looks_private,
    normalize_code,
    parse_public_key,
    public_text,
    seal_proof,
)
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import confirm, editable_configuration, form_action, sign_preview
from .request_admission import historical_record_id
from .sessions import FreshAuthenticationRequired, require_fresh

TARGET = "backup_key"
LABEL = _("Backup encryption key")
SALT = "stewardship-backup-key-preview-v1"
PROOF_SALT = "stewardship-backup-key-proof-v1"
# Long enough to reach the key machine, short enough that a stolen page or
# session is not a standing authority to change the key.
PROOF_SECONDS = 900
# Where a proved change waits across the Google step-up (see module docs).
RESUME_KEY = "stewardship-backup-key-resume"
# A pasted key or typed code is short, and a signed intent a few hundred
# characters; anything longer is not one.
MAX_FIELD = 200
MAX_INTENT = 2000


@dataclass(frozen=True)
class KeyStatus:
    """Which key the next backup seals to, as far as the web can tell.

    ``source`` is "configured" (set on this page), "installed" (the file the
    operator installed; its fingerprint is the newest backup's) or "unknown"
    (installed by hand and no backup has run yet).
    """

    source: str
    fingerprint: str | None
    public_key: str | None = None
    backup_at: datetime | None = None


def configured_key(configuration):
    """The ``backup_key`` record in the applied configuration, or None."""
    for record in configuration.active_configuration.canonical_document["sections"].get(
        "integrations", []
    ):
        if record["values"]["kind"] == TARGET:
            return record
    return None


def key_status(configuration):
    """Describe the key in use: the configured one, else the newest backup's."""
    record = configured_key(configuration)
    if record is not None:
        text = record["values"]["settings"]["public_key"]
        return KeyStatus(
            "configured", parse_public_key(text).fingerprint, public_key=text
        )
    from parishkit.stewardship.jobs.backup_models import BackupRun

    newest = (
        BackupRun.objects.order_by("-completed_at", "-id")
        .values_list("recipient_fingerprint", "completed_at")
        .first()
    )
    if newest is None:
        return KeyStatus("unknown", None)
    return KeyStatus("installed", newest[0], backup_at=newest[1])


def _proof(nonce, code):
    """The code's keyed hash; the server key keeps it from being guessed offline."""
    return salted_hmac(PROOF_SALT, f"{nonce}:{code}").hexdigest()


def _box():
    """The server-keyed box that hides the pasted text inside the intent."""
    key = salted_hmac(PROOF_SALT + "-box", "key", algorithm="sha256").digest()
    return SecretBox(key)


@sensitive_variables("text", "raw")
def check_new_key(text, status):
    """Admit a pasted public key for proof; return its Recipient.

    Refuses anything that is not one usable public key, the key already in
    use, and (the one mistake that can be caught) the current key's private
    half: a private key's text looks like a public key, and pasting it here
    would put it on the server.
    """
    if looks_private(text):
        raise ValueError(
            _(
                "This is a PRIVATE key, not a public key. Never paste a private "
                "key into any web page. It was not saved, but treat it as "
                "exposed: make a new key pair and paste only its public_key."
            )
        )
    try:
        recipient = parse_public_key(text)
    except SealError:
        raise ValueError(
            _(
                "This is not a backup public key. Paste only the public_key "
                "value backup-keygen printed: one line of 44 characters "
                "ending in =."
            )
        ) from None
    raw = bytes(recipient.public)
    if status.fingerprint is not None and (
        fingerprint(PrivateKey(raw).public_key) == status.fingerprint
    ):
        raise ValueError(
            _(
                "This is the PRIVATE key for the current backup key, not a "
                "public key. Never paste a private key into any web page. It "
                "was not saved, but treat it as exposed: make a new key pair "
                "and replace the key."
            )
        )
    if recipient.fingerprint == status.fingerprint:
        raise ValueError(_("This key is already the backup encryption key."))
    return recipient


def start_proof(actor, configuration, recipient):
    """Seal a new code to the key; return (challenge, signed intent)."""
    challenge, code = seal_proof(recipient)
    nonce = secrets.token_hex(16)
    sealed = _box().encrypt(public_text(recipient).encode("ascii"))
    intent = signing.dumps(
        {
            "actor": str(actor.identity),
            "base": configuration.active_configuration.digest,
            # Encrypted: until the proof passes it may be a private key.
            "key": base64.b64encode(sealed).decode("ascii"),
            "fingerprint": recipient.fingerprint,
            "nonce": nonce,
            "proof": _proof(nonce, code),
            # Kept so a mistyped code can be tried again against the same
            # challenge; it is sealed, so it reveals nothing.
            "challenge": challenge,
        },
        salt=PROOF_SALT,
    )
    return challenge, intent


def load_intent(actor, configuration, intent):
    """Open a signed intent that belongs to this Administrator and settings.

    It cannot be replayed by someone else, across another settings change or
    after ``PROOF_SECONDS``.
    """
    value = signing.loads(intent, salt=PROOF_SALT, max_age=PROOF_SECONDS)
    if value["actor"] != str(actor.identity):
        raise PermissionError("The key check belongs to another Administrator.")
    if value["base"] != configuration.active_configuration.digest:
        raise StaleRecordError("The settings changed; start the key change again.")
    return value


def finish_proof(actor, configuration, intent, code):
    """Check the typed code; return the proved key text, or None.

    The key is decrypted only once the code matches, so an unproved paste
    never leaves the intent's ciphertext.
    """
    value = load_intent(actor, configuration, intent)
    if not constant_time_compare(
        _proof(value["nonce"], normalize_code(code)), value["proof"]
    ):
        return None
    return proved_key(value)


def proved_key(value):
    """Decrypt the key of an intent whose proof passed."""
    try:
        sealed = base64.b64decode(value["key"], validate=True)
        text = _box().decrypt(sealed).decode("ascii")
    except (ValueError, CryptoError):
        raise ValueError("Invalid key change intent.") from None
    if parse_public_key(text).fingerprint != value["fingerprint"]:
        raise ValueError("Invalid key change intent.")
    return text


def _patch(configuration, record, text):
    """Add the ``backup_key`` record, or replace its key."""
    if record is not None:
        return [
            {
                "operation": "update",
                "section": "integrations",
                "id": record["id"],
                "values": {"settings": {"public_key": text}},
            }
        ]
    return [
        {
            "operation": "add",
            "section": "integrations",
            "id": historical_record_id(configuration.active_configuration.pk, TARGET)
            or str(uuid4()),
            "values": {
                "kind": TARGET,
                "settings": {"public_key": text},
                "credential_fingerprint": None,
            },
        }
    ]


def _render(request, configuration, *, status=200, **context):
    """The key page: the key in use, and whichever step of a change is next."""
    try:
        require_fresh(request)
        fresh = True
    except FreshAuthenticationRequired:
        fresh = False
    response = render(
        request,
        "stewardship/backup-key.html",
        {
            "label": LABEL,
            "breadcrumb_label": LABEL,
            "current": key_status(configuration),
            "fresh": fresh,
            **context,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _field(request, name, limit=MAX_FIELD):
    """One bounded posted text field."""
    value = request.POST.get(name, "")
    if len(value) > limit:
        raise ValueError("Invalid key change fields.")
    return value


def _review(request, service, configuration, actor, text):
    """Show the proved change for confirmation: old and new key IDs."""
    record = configured_key(configuration)
    patch = _patch(configuration, record, text)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    from .request_patch import build_candidate

    build_candidate(base, patch, candidate_id=uuid4())
    current = key_status(configuration)
    return render(
        request,
        "stewardship/integration-preview.html",
        {
            "target": TARGET,
            "label": LABEL,
            "breadcrumb_label": LABEL,
            "configuration": configuration,
            "changes": [
                {
                    "label": _("Key fingerprint"),
                    "before": current.fingerprint or _("Not known yet"),
                    "after": parse_public_key(text).fingerprint,
                }
            ],
            "preview": sign_preview(
                actor=actor, configuration=configuration, patch=patch, salt=SALT
            ),
        },
    )


def _fresh(request):
    """Whether the Administrator signed in with Google in the last five minutes."""
    try:
        require_fresh(request)
    except FreshAuthenticationRequired:
        return False
    return True


def _step_up(request, configuration):
    """Ask for the Google step-up and come back to review a proved change."""
    return _render(request, configuration, proved=True, next=request.path)


def _resume(request, service, configuration, actor):
    """Review a change proved before the step-up, once it is fresh again.

    The session holds only the signed intent of a proved change, whose key
    is therefore a real public key. It is used once; a stale or foreign one
    is dropped and the page starts over.
    """
    intent = request.session.get(RESUME_KEY)
    if not _fresh(request):
        return _step_up(request, configuration)
    del request.session[RESUME_KEY]
    try:
        text = proved_key(load_intent(actor, configuration, intent))
    except (ValueError, PermissionError, StaleRecordError, signing.BadSignature):
        return _render(request, configuration)
    return _review(request, service, configuration, actor, text)


def backup_key_page(request, service, actor):
    """Show the key page, or take one step of a key change.

    - GET shows the key in use and the field for a new public key, or
      resumes a proved change after the Google step-up.
    - ``challenge`` checks the pasted key and shows its sealed code. It needs
      a recent Google sign-in, so the step-up happens before the offline work.
    - ``preview`` checks the typed code and shows the change to confirm; if
      the sign-in went stale meanwhile, it asks for the step-up first.
    - ``confirm`` needs a recent sign-in again and records the configuration
      request, like any settings change.
    """
    configuration = editable_configuration(service)
    if request.method != "POST":
        if isinstance(request.session.get(RESUME_KEY), str):
            return _resume(request, service, configuration, actor)
        return _render(request, configuration)
    action = request.POST.get("action")
    if action == "confirm":
        form_action(request.POST, preview_fields=set())
        require_fresh(request)
        return confirm(
            request,
            service,
            actor,
            salt=SALT,
            current_scope=lambda service: (editable_configuration(service), None),
        )
    if action == "challenge":
        if set(request.POST) - {"action", "csrfmiddlewaretoken", "public_key"}:
            raise ValueError("Invalid key change fields.")
        require_fresh(request)
        status = key_status(configuration)
        try:
            recipient = check_new_key(_field(request, "public_key"), status)
        except ValueError as error:
            return _render(request, configuration, status=400, key_error=error.args[0])
        challenge, intent = start_proof(actor, configuration, recipient)
        return _render(
            request,
            configuration,
            new_fingerprint=recipient.fingerprint,
            challenge=challenge,
            intent=intent,
        )
    form_action(request.POST, preview_fields={"intent", "code"})
    intent = _field(request, "intent", MAX_INTENT)
    text = finish_proof(actor, configuration, intent, _field(request, "code"))
    if text is None:
        # Show the same challenge again: its code is still the right one.
        value = load_intent(actor, configuration, intent)
        return _render(
            request,
            configuration,
            status=400,
            new_fingerprint=value["fingerprint"],
            challenge=value["challenge"],
            intent=intent,
            code_error=_(
                "That code does not match. Check that you used the private key "
                "that belongs to this public key, and type the code exactly as "
                "backup-prove printed it."
            ),
        )
    if not _fresh(request):
        request.session[RESUME_KEY] = intent
        return _step_up(request, configuration)
    return _review(request, service, configuration, actor, text)
