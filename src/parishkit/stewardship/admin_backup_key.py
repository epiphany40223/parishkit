"""The Backup encryption key's steps from the command line (ADM-11 PR 10).

The **Backup encryption key** page (``admin:integration_settings`` for
``backup_key``, ``accounts.backup_key``) changes the public key backups are
sealed to in three steps, and so do these commands, through the page's own
pieces so the two cannot drift:

- ``integration backup-key challenge`` is the page's first step: the new
  public key (sent by the host wrapper after the preamble, never as an
  argument) is checked by the page's ``check_new_key`` and a code is sealed
  to it by ``start_proof``. It prints the sealed ``challenge`` line, which
  ``pk-stewardship backup-prove`` opens on the key machine, and the page's
  signed ``intent``, in which the pasted key is encrypted under a server
  key exactly as in the page's hidden field.
- ``integration backup-key preview`` is the page's code check
  (``finish_proof``) and review (``review_change``): the typed code, also
  sent after the preamble, proves the Administrator holds the private key.
  It prints the old and new fingerprints, as the page's review shows them,
  and the page's signed preview token.
- ``integration backup-key confirm`` is the page's Confirm: the page's
  configuration request (``confirm_intent`` with the page's salt), with the
  fresh gate under the work lock and, in the request's durable
  transaction, ``admin_cmd_integration_backup_key_confirm`` and a
  ``policy_change`` notice observing ``automation_policy_change``. The
  configuration installer applies it, and the activation records the
  ``backup_key_replaced`` security event every Administrator is told
  about, as for the page.

The page asks for a Google sign-in within five minutes before the challenge
and at Confirm; a live full-scope automation session stands in for it
(``require_fresh``), so the page's step-up resume has no counterpart here.
The challenge and preview record no event, as the page's steps record none.
No private key, typed code or pasted text is ever printed, logged or
recorded; the only key facts printed are the fingerprints the page shows.
"""

from dataclasses import dataclass

from django.db import DatabaseError

from .admin_changes import InvalidChange, _admit_change, _ended_or_raise, _problem
from .admin_reads import ReadModel, _held, _recheck, config_request

# The page's limit on its pasted key and typed code fields
# (``backup_key.MAX_FIELD``, which a test keeps equal), in bytes: both are
# short ASCII text.
FIELD_LIMIT = 200


@dataclass(frozen=True)
class BackupKeyChallenge(ReadModel):
    """``integration backup-key challenge``: the page's challenge step.

    ``current`` is the key in use as the page's "Key in use" shows it
    (``source`` configured, installed or unknown; its ``fingerprint``; for
    an installed key, ``backup_at``, the newest backup's time).
    ``fingerprint`` is the new key's, ``challenge`` the code sealed to it
    and ``intent`` the signed intent ``integration backup-key preview``
    takes, valid for fifteen minutes.
    """

    version: str
    current: dict
    fingerprint: str
    challenge: str
    intent: str


@dataclass(frozen=True)
class BackupKeyPreview(ReadModel):
    """``integration backup-key preview``: the page's review of a proved key.

    ``changes`` has the one row the page's review shows: ``fingerprint``
    ``before`` (None where the page says "Not known yet") and ``after``.
    ``preview.token`` is the signed binding ``integration backup-key
    confirm`` takes, valid for fifteen minutes.
    """

    version: str
    changes: list
    preview: dict


@dataclass(frozen=True)
class BackupKeyConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def _field_problem(field, message):
    """One ``invalid`` problem at ``field``, in fixed words."""
    return _problem(field, {"code": "invalid", "message": message}, field)


def line_text(value, field):
    """The text the page's ``field`` would hold, from the wrapper's bytes.

    More than the page's limit, or bytes that are not UTF-8, is ``invalid``
    at ``field`` in fixed words that never quote the value.
    """
    if len(value) > FIELD_LIMIT:
        raise InvalidChange([_field_problem(field, "This is too long.")])
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidChange([_field_problem(field, "This is not text.")]) from None


def _current(status):
    """The key in use as the page's "Key in use" panel shows it."""
    return {
        "source": status.source,
        "fingerprint": status.fingerprint,
        "backup_at": status.backup_at,
    }


def _fresh_then(caller, service, step):
    """Admit as the page's post, pass its fresh gate, then run ``step``.

    ``step(actor)`` runs in one read snapshot after the page's recent
    sign-in is stood in for by the live full-scope session (the session's
    ``FOR SHARE`` lock needs a writable transaction, so it is checked just
    before the snapshot; neither step has a command type, so nothing is
    recorded). Access is checked again afterwards, as the page does.
    """
    from django.db import transaction

    from .accounts.policy import Capability
    from .accounts.sessions import require_fresh
    from .campaigns.work_locks import read_transaction

    actor = _admit_change(caller, service)

    def run():
        """The fresh gate, then the step in one snapshot."""
        with transaction.atomic():
            require_fresh(caller)
        with read_transaction():
            return step(actor)

    try:
        model = _held(run)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def challenge(caller, service, text):
    """``integration backup-key challenge``: check a new key, seal a code to it.

    The page's ``check_new_key``: a private key (the top bit set, or the
    private half of the key in use), a text that is not one public key, or
    the key already in use is ``invalid`` at ``public_key`` in the page's
    words, which never quote it. Then the page's ``start_proof``. Changes
    nothing.
    """
    from .accounts.admin_editing import editable_configuration
    from .accounts.backup_key import check_new_key, key_status, start_proof

    def step(actor):
        """Check the key and seal the code against this configuration."""
        configuration = editable_configuration(service)
        status = key_status(configuration)
        try:
            recipient = check_new_key(text, status)
        except ValueError as error:
            raise InvalidChange(
                [_field_problem("public_key", str(error.args[0]))]
            ) from None
        sealed, intent = start_proof(actor, configuration, recipient)
        return BackupKeyChallenge(
            version=configuration.active_configuration.digest,
            current=_current(status),
            fingerprint=recipient.fingerprint,
            challenge=sealed,
            intent=intent,
        )

    return _fresh_then(caller, service, step)


def _open_intent(actor, configuration, intent, code):
    """The page's ``finish_proof``: the proved key text, or a refusal.

    An expired intent, or one made before the settings changed, is
    ``stale_version`` (start again with ``challenge``); an altered one is
    ``invalid`` at ``intent``; another Administrator's is ``denied``; a code
    that does not match is ``invalid`` at ``code`` in the page's words (the
    same intent may be tried again).
    """
    from django.core import signing

    from .accounts.backup_key import MAX_INTENT, finish_proof
    from .storage import StaleRecordError

    if len(intent) > MAX_INTENT:
        raise InvalidChange([_field_problem("intent", "This is not a key check.")])
    try:
        text = finish_proof(actor, configuration, intent, code)
    except signing.SignatureExpired:
        raise StaleRecordError(
            "The key check expired; start again with challenge."
        ) from None
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise InvalidChange(
            [_field_problem("intent", "This is not a key check.")]
        ) from None
    if text is None:
        raise InvalidChange(
            [
                _field_problem(
                    "code",
                    "That code does not match. Check that you used the private "
                    "key that belongs to this public key, and type the code "
                    "exactly as backup-prove printed it.",
                )
            ]
        )
    return text


def preview(caller, service, *, intent, code):
    """``integration backup-key preview``: prove the key, review the change.

    The page's code check (``_open_intent``), then its review
    (``review_change``) against the same snapshot. A resulting document the
    configuration refuses is ``invalid``. Changes nothing.
    """
    from parishkit.config import ConfigError

    from .accounts.admin_editing import editable_configuration
    from .accounts.backup_key import review_change

    def step(actor):
        """Check the code and build the page's review."""
        configuration = editable_configuration(service)
        text = _open_intent(actor, configuration, intent, code)
        try:
            before, after, token = review_change(service, configuration, actor, text)
        except ConfigError:
            # Raised past _held, which would read it as unavailable.
            raise ValueError("The configuration refuses this change.") from None
        return BackupKeyPreview(
            version=configuration.active_configuration.digest,
            changes=[{"field": "fingerprint", "before": before, "after": after}],
            preview={"token": token},
        )

    return _fresh_then(caller, service, step)


def prompt_lines(caller, service, token):
    """The confirmation prompt's summary, from the token's reviewed patch.

    Names the old and new fingerprints, as the page's review shows them,
    and what the change means. A token that will be refused is refused
    here, before the prompt: an altered or foreign one is ``invalid``, an
    expired one, or one reviewed before the settings changed, is
    ``stale_version``, another Administrator's is ``denied``.
    ``confirm_intent`` repeats these checks under the work lock.
    """
    from uuid import UUID

    from django.core import signing

    from .accounts.admin_editing import editable_configuration
    from .accounts.backup_key import SALT, key_status
    from .accounts.request_models import ConfigurationChangeRequest
    from .backup_sealing import SealError, parse_public_key
    from .campaigns.work_locks import read_transaction
    from .web.refusals import expired_preview, load_preview

    try:
        intent = load_preview(token, salt=SALT)
        [operation] = intent["patch"]
        after = parse_public_key(
            operation["values"]["settings"]["public_key"]
        ).fingerprint
    except (signing.BadSignature, KeyError, TypeError, ValueError, SealError):
        raise ValueError("The preview token is not valid.") from None
    actor = _admit_change(caller, service)
    if intent["actor"] != str(actor.identity):
        _ended_or_raise(
            caller,
            service,
            actor,
            PermissionError("Preview belongs to another Administrator."),
        )

    def read():
        """The key in use and the settings version, in one snapshot."""
        with read_transaction():
            configuration = editable_configuration(service)
            return (
                key_status(configuration).fingerprint,
                configuration.active_configuration.digest,
            )

    before, digest = _held(read)
    # An identical retry returns the original receipt even after the change
    # was installed (confirm_intent does the same), so only a token with no
    # request yet can be stale.
    repeat = ConfigurationChangeRequest.objects.filter(
        actor_id=actor.identity, request_key=UUID(intent["key"])
    ).exists()
    if digest != intent["base"] and not repeat:
        raise expired_preview(None)
    return [
        f"This replaces the backup encryption key: fingerprint "
        f"{before or 'not known yet'} becomes {after}.",
        "Every Administrator is told. Backups already made stay encrypted "
        "with the old key: keep its private key until they are gone.",
    ]


def confirm(caller, service, *, token, context):
    """``integration backup-key confirm``: the page's Confirm.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the page's salt (expired or out of date is ``stale_version``, altered or
    another page's ``invalid``, another Administrator's ``denied``), passes
    the page's fresh gate again under the work lock and records the request
    with ``admin_cmd_integration_backup_key_confirm`` and a ``policy_change``
    notice in its own durable transaction. ``context["committed"]`` and
    ``context["request_id"]`` follow ``integration confirm``'s rules, so a
    later error is ``outcome_unknown`` naming the request.
    """
    from uuid import UUID

    from django.core import signing

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.automation_sessions import notify, observe
    from .accounts.backup_key import SALT
    from .accounts.configuration_requests import policy_operation_id
    from .admin_integration_actions import _command_event
    from .audit.schemas import Action
    from .jobs.operational_content import IncidentKind
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    _held(lambda: editable_configuration(service))
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=SALT)["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []

    def attach(request, extra):
        """Record the command's event and the policy notice with the request."""
        _command_event(Action.ADMIN_CMD_INTEGRATION_BACKUP_KEY_CONFIRM, caller, actor)
        # Replacing the backup key tells every Administrator
        # (admin-automation specification, "Notifications").
        notify(
            "policy_change",
            caller.automation_session,
            command_type=caller.command_type,
            actor_id=actor.identity,
        )
        observe(IncidentKind.AUTOMATION_POLICY_CHANGE)
        created.append(request.pk)

    try:
        receipt = confirm_intent(
            caller,
            service,
            actor,
            token=token,
            salt=SALT,
            link=None,
            current_scope=lambda service: (editable_configuration(service), None),
            attach=attach,
            # The page asks for a recent sign-in at Confirm; here it is
            # checked under the work lock, in the request's transaction.
            fresh=True,
        )
    except signing.BadSignature:
        # Altered, or signed for another page (an expired one is stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return BackupKeyConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
