"""Parish logos commands (#1865): ``branding preview`` and ``branding confirm``.

Parish logos (``admin:branding_settings``, #750, #1062) changes the parish's
four logo sizes in two steps: the upload, which stages the sizes and opens
the review dialog, and the dialog's **Apply**, which records the
configuration request. ``branding show`` (``admin_settings``) reads the
current logos; these two commands are the upload with its review and the
Apply, through the page's own functions, so the page and the command line
cannot drift:

- ``branding preview - [--size SIZE] --expected-version VERSION`` reads one
  image from standard input (after the preamble) and binds it to the page's
  own form (``branding_views.LogoUploadForm``): without ``--size`` it is
  Basic, one image for all four sizes; with it, Advanced for that one size,
  the other sizes keeping their current images. It stages the sizes through
  the page's ``branding_views.stage_upload`` (``stage_branding``) and builds
  the page's own review (``branding_views._review``), printing each size and
  ``preview.token``, the page's signed binding (its salt, bound to the
  staged bundle). Staging is a change, as the page's upload is: it records
  one ``admin_cmd_branding_preview`` event in the transaction that makes the
  staged logos ready. A refused image is ``invalid`` with the page's own
  words at ``branding.logo`` (or ``branding.<size>``).
- ``branding confirm --bundle ID --token TOKEN`` records the configuration
  request exactly as the dialog's Apply does, through
  ``admin_editing.confirm_intent`` with the page's salt and staged-upload
  scope (``branding_views._scope``), with one ``admin_cmd_branding_confirm``
  event in the request's own durable transaction. A repeat returns the
  original request and records nothing.

Staged logos belong to the sign-in that uploaded them. For the command line
that sign-in is the automation session (``branding_staging.sign_in``), so
a later command of the same session applies them for the upload's whole
day; logos staged in the
browser, or by another automation session, are ``not_available``. Neither
command is privileged or prompts, as the page asks for no recent sign-in and
shows no confirmation dialog: the reviewed token is the confirmation. The
output holds the logos' stored facts only (sizes, dimensions, checksums),
never image bytes.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel, Unavailable, _held, _recheck

# The largest image the page's form takes (``web.content.MAX_IMAGE_BYTES``,
# 5 MB); a test keeps them equal. Spelled out because the parser is built
# before Django is set up.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# The page's sizes, in its order (``branding_slots.SLOTS``); a test keeps
# them equal.
SIZES = ("large", "menu", "icon", "favicon")


@dataclass(frozen=True)
class BrandingPreview(ReadModel):
    """The staged logos as the page's review dialog draws them, and its token.

    ``version`` is the applied configuration's digest the logos were staged
    and reviewed against; ``bundle`` the staged upload ``branding confirm``
    names; ``mode`` ``basic`` (one image for every size) or ``advanced``
    (one size uploaded, the others kept). ``logos`` has one entry per size
    in the page's order: ``slot``, ``name`` (the page's label), the staged
    image's ``id``, ``width``, ``height``, ``size`` in bytes and ``sha256``,
    and ``changed``, whether this upload changes that size.
    ``preview.token`` is the signed binding ``branding confirm`` takes, valid
    for fifteen minutes.
    """

    version: str
    bundle: UUID
    mode: str
    logos: list
    preview: dict


@dataclass(frozen=True)
class BrandingConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def read_image(stream):
    """One image's bytes from standard input (after the preamble).

    At most one byte past the page's 5 MB limit is read, so a larger file
    reaches the page's own form, which refuses it in its own words.
    """
    data = stream.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise ValueError("No image was given on standard input.")
    return data


def logo_form(data, size, expected_version):
    """The page's upload form, bound as the page's post would bind it.

    Basic (``size`` None) posts the one image as ``logo``; Advanced posts it
    under its size's own field, every other size left empty. The file's
    name is server-owned, as the page never uses the original name.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from .accounts.branding_views import ADVANCED, BASIC, LogoUploadForm

    upload = SimpleUploadedFile("upload", data)
    return LogoUploadForm(
        {
            "base_digest": expected_version,
            "editor_mode": BASIC if size is None else ADVANCED,
        },
        {size or "logo": upload},
    )


def logo_fields(review):
    """Each staged size as ``branding preview`` prints it, in the page's order."""
    from .accounts.branding_slots import SLOTS

    staged = {asset.label: asset for asset in review["assets"]}
    return [
        {
            "slot": slot,
            "name": str(SLOTS[slot].title),
            "id": staged[slot].pk,
            "width": staged[slot].width,
            "height": staged[slot].height,
            "size": staged[slot].size,
            "sha256": staged[slot].sha256,
            "changed": staged[slot].changed,
        }
        for slot in SLOTS
    ]


def _recorder(caller, actor, event, written):
    """The callback that records the command's own event in the change."""
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action

    def record(*_):
        """Record the command's event, attributed to the automation session."""
        record_action(
            event,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        written.append(True)

    return record


def preview_branding(caller, service, data, *, size, expected_version, context):
    """``branding preview``: the page's upload and its review dialog.

    Admits as the page's upload does (``CONFIGURE``, recording activity),
    binds the image to the page's form and stages it through the page's own
    ``stage_upload``; the form's refusals are ``invalid`` with its messages,
    and a base other than ``expected_version`` is ``stale_version`` (nothing
    is staged). Then, as the page's ``_reviewed`` does, it reads the staged
    upload before the configuration and builds the page's review; settings
    that changed in between are ``stale_version``. Rechecks access before
    printing. Storage that cannot be used, or a configuration that cannot be
    read, is ``unavailable``, as the page answers 503. A refusal after
    staging leaves only an unapplied upload that expires within a day, so
    running the preview again is always safe.
    """
    from django.db import DatabaseError

    from parishkit.config import ConfigError

    from .accounts.admin_editing import editable_configuration
    from .accounts.branding_staging import staged_bundle
    from .accounts.branding_views import _review, stage_upload
    from .accounts.policy import Capability
    from .admin_changes import (
        InvalidChange,
        _admit_change,
        _ended_or_raise,
        form_problems,
    )
    from .audit.schemas import Action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    form = logo_form(data, size, expected_version)
    written = []
    try:
        bundle_id, refused = stage_upload(
            caller,
            service,
            form,
            record=_recorder(caller, actor, Action.ADMIN_CMD_BRANDING_PREVIEW, written),
        )
    except DatabaseError as error:
        # The ready transaction holds the command's event: a failure once
        # it was written may have struck the commit itself.
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except PermissionError as error:
        # A session that ended meanwhile is exit 5, not the page's refusal.
        _ended_or_raise(caller, service, actor, error)
    except (ConfigError, OSError):
        # Media storage or the configuration cannot be used now (the
        # page's 503): nothing usable was staged.
        raise Unavailable("Parish logos cannot be staged now.") from None
    if refused is not None:
        raise InvalidChange(form_problems("branding", refused.errors, "branding"))

    def review():
        """The staged logos, then the configuration, then the page's review."""
        staged = staged_bundle(caller, service, bundle_id)
        return _review(service, actor, editable_configuration(service), staged)

    try:
        result = _held(review)
    except OSError:
        # A staged file that cannot be read back (the page's 503).
        raise Unavailable("The staged logos cannot be read now.") from None
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    # Only now is the staged upload a usable preview; a refusal of its
    # review above keeps its own code (the inert upload expires).
    context["committed"] = True
    return BrandingPreview(
        version=expected_version,
        bundle=result["bundle"],
        mode=form.cleaned_data["editor_mode"],
        logos=logo_fields(result),
        preview={"token": result["preview"]},
    )


def confirm_branding(caller, service, bundle_id, *, token, context):
    """``branding confirm``: the review dialog's **Apply** for staged logos.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the page's salt for ``bundle_id`` (expired or out of date is
    ``stale_version``; altered, another bundle's or another page's
    ``invalid``; another Administrator's ``denied``) and the page's scope:
    logos staged by another sign-in, or gone, are ``not_available``; logos
    that expired or whose settings changed are ``stale_version``. It records
    the request with ``admin_cmd_branding_confirm`` in its own durable
    transaction. The configuration must be editable first (``_held``).
    ``context["committed"]`` and ``context["request_id"]`` follow ``parish
    confirm``'s rules, so a later error is ``outcome_unknown`` naming the
    request.
    """
    from django.core import signing
    from django.db import DatabaseError

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.branding_views import SALT, _scope
    from .accounts.configuration_requests import policy_operation_id
    from .admin_changes import _admit_change, _ended_or_raise
    from .admin_reads import config_request
    from .audit.schemas import Action
    from .observability import _guard_refusal
    from .web.refusals import UserFacingMissing

    actor = _admit_change(caller, service)
    _held(lambda: editable_configuration(service))
    salt = SALT + str(bundle_id)
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=salt)["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []
    record = _recorder(caller, actor, Action.ADMIN_CMD_BRANDING_CONFIRM, created)
    try:
        receipt = confirm_intent(
            caller,
            service,
            actor,
            token=token,
            salt=salt,
            link=None,
            current_scope=_scope(caller, bundle_id),
            attach=record,
        )
    except signing.BadSignature:
        # Altered, another bundle's or another page's (an expired one is stale).
        raise ValueError("The preview token is not valid.") from None
    except UserFacingMissing:
        # Staged by another sign-in (the browser, or another automation
        # session), or cleaned up: the page's "upload again" refusal.
        raise NotAvailable("These logos cannot be applied from this session.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return BrandingConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
