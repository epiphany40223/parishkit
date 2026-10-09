"""Testing send commands of the Admin automation command line (ADM-11 PR 6b).

``test sample-preview`` and ``test sample`` are the Preview and test email
page's review and its **Send this test email**: one fictional message from
an email revision, sent only to the configured Testing recipient, never to
a Family. They go through the page's own functions in
``accounts.campaign_mail`` (``prepare``, ``request_sample`` and
``recent_tests``), and the token is the page's own signed binding, so a
preview made on the page can be sent from the command line and the other
way round.

The page asks for no fresh sign-in. Its one acknowledgement ("A previous
test may have arrived; I want to send another test.") is required only
while an earlier test's outcome is unknown, so ``test sample`` asks for it
at the prompt only then (``admin_cli.sample_test``). It records one
``admin_cmd_test_sample`` event, in the send's own transaction, only when
the send is new.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_reads import NotAvailable, ReadModel, _held, _recheck

# The page's acknowledgement, asked at the prompt: the checkbox's words.
UNKNOWN_ACKNOWLEDGEMENT = (
    "A previous test may have arrived; I want to send another test."
)


def _admit(caller, store, *, final=False):
    """The page's admission (``CONFIGURE``); a ``final`` one writes nothing.

    The commands admit read-only (``final``): the page's own functions
    record the session's activity where the page does (``request_sample``'s
    ``principal``, once per send; the preview, like the page's view, never).
    ``command_scope`` repeats it under the work lock, read-only too. A
    session that ended is exit 5 (``session_ended``), not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import Capability, allows
    from .accounts.sessions import authenticated_admin

    principal = authenticated_admin(
        caller, store=store, activity=not final, read_only=final
    )
    if principal is None:
        raise SessionUnusable("session_ended")
    if not allows(principal, Capability.CONFIGURE):
        raise PermissionError("A test email needs the configure capability.")
    return principal


def _current_campaign(service):
    """The current campaign's id; ``not_available`` when there is none.

    A restore under review or an unfinished setup refuses with a
    ``ConfigError``, which the callers' ``_held`` reports as ``unavailable``.
    """
    from .accounts.admin_editing import editable_configuration

    campaign_id = editable_configuration(service).current_campaign_id
    if campaign_id is None:
        raise NotAvailable("There is no current campaign.")
    return campaign_id


@dataclass(frozen=True)
class SamplePreview(ReadModel):
    """The page's review of a sample test, and the token that sends it.

    ``subject`` is the fictional sample's subject, as the page shows it.
    The Testing recipient's address and the message body stay on the page:
    ``testing_recipient_set`` only says one is configured. ``pending`` and
    ``unknown`` are the page's flags; while ``unknown``, ``test sample`` asks
    for the page's acknowledgement. ``tests`` is the page's recent list.
    ``preview.token`` is valid for the page's preview lifetime.
    """

    campaign_id: UUID
    revision_id: UUID
    request_key: UUID
    subject: str
    testing_recipient_set: bool
    pending: bool
    unknown: bool
    tests: list
    preview: dict


def sample_preview_model(preview, tests, token, revision_id):
    """The command's projection of the page's preview and recent tests."""
    return SamplePreview(
        campaign_id=preview.row.campaign_id,
        revision_id=revision_id,
        request_key=preview.row.request_key,
        subject="[TEST] " + preview.sample.subject,
        testing_recipient_set=bool(preview.sample.recipient),
        pending=tests["pending"],
        unknown=tests["unknown"],
        tests=[
            {key: item[key] for key in ("id", "state", "created_at", "current")}
            for item in tests["items"]
        ],
        preview={"token": token},
    )


def preview_sample(caller, service, revision_id, *, request_key):
    """``test sample-preview``: the page's review of one email revision.

    ``request_key`` becomes the preview's key, so the token it signs is the
    page's own. An unknown revision is ``not_available``; one the current
    configuration cannot test now is ``stale_version``, as the page answers
    409. Records no event, as the page's view records none.
    """
    from django.core import signing
    from django.core.exceptions import ObjectDoesNotExist

    from .accounts.campaign_mail import SALT, prepare, recent_tests
    from .accounts.policy import Capability

    actor = _admit(caller, service.store, final=True)

    def step():
        """The page's preview and list, in its work transaction."""
        campaign_id = _current_campaign(service)
        try:
            preview = prepare(
                caller, service, campaign_id, revision_id, request_key=request_key
            )
        except ObjectDoesNotExist:
            raise NotAvailable("No such email revision.") from None
        token = signing.dumps(preview.binding(), salt=SALT)
        return sample_preview_model(
            preview, recent_tests(campaign_id, preview.row), token, revision_id
        )

    try:
        model = _held(step)
    except (NotAvailable, PermissionError) as error:
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        raise error
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def unknown_outcome(caller, service):
    """Whether an earlier test's outcome is unknown, so the page would ask.

    Read before the prompt, outside any transaction: no lock is held while a
    person reads and types. A test that becomes unknown afterwards is still
    refused by ``request_sample`` (``invalid``: preview again).
    """
    from .accounts.campaign_mail_models import CampaignMailTest

    _admit(caller, service.store, final=True)

    def step():
        """The page's flag for the current campaign."""
        return CampaignMailTest.objects.filter(
            campaign_id=_current_campaign(service), state="delivery_unknown"
        ).exists()

    return _held(step)


@dataclass(frozen=True)
class SampleTest(ReadModel):
    """The test a ``test sample`` queued, or found for its token's key.

    ``created`` is false when the token's key was already sent (a repeat,
    from the command line or the page), which returns the original test.
    """

    created: bool
    request_key: UUID
    test: dict


def sample_test_model(row, *, created):
    """The command's projection of a ``CampaignMailTest``; never the message."""
    return SampleTest(
        created=created,
        request_key=row.request_key,
        test={
            "id": row.pk,
            "state": row.state,
            "task_id": row.task_id,
            "created_at": row.created_at,
        },
    )


def send_sample(caller, service, *, token, acknowledge_unknown, context):
    """``test sample``: the page's **Send this test email** for a reviewed token.

    ``acknowledge_unknown`` is the page's checkbox, which the command line
    asked for at the prompt (or took from ``--yes``). Runs the page's
    ``request_sample`` in the delivery pages' command scope. An expired or
    out-of-date preview (including one whose revision has since left the
    active configuration), or a test already pending, is ``stale_version``;
    an altered token ``invalid``; another Administrator's token, or one for
    another campaign, ``denied``; an unknown outcome not acknowledged
    ``invalid``.

    ``context["request_id"]`` is the token's key once read, so an exit-6
    document names it. ``context["committed"]`` is set once the send may
    have committed (as for ``refresh start``).
    """
    from django.core import signing
    from django.core.exceptions import ObjectDoesNotExist

    from .accounts.campaign_mail import SALT, request_sample
    from .accounts.campaign_mail_models import CampaignMailTest
    from .accounts.policy import Capability
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal
    from .storage import StaleRecordError

    actor = _admit(caller, service.store, final=True)
    try:
        # The key only names the outcome; request_sample verifies the token.
        key = UUID(signing.loads(token, salt=SALT)["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(key)
    written = []

    def step():
        """Send inside the page's command scope, with the command's event."""
        written.clear()
        campaign_id = _current_campaign(service)
        with command_scope(caller, service, actor, admit=_admit):
            repeat = (
                key is not None
                and CampaignMailTest.objects.filter(
                    requested_by_id=actor.identity, request_key=key
                ).exists()
            )
            try:
                row = request_sample(
                    caller,
                    service,
                    campaign_id,
                    _revision(token),
                    preview_token=token,
                    acknowledge_unknown=acknowledge_unknown,
                )
            except ObjectDoesNotExist:
                # The revision (or the integration it needs) left the active
                # configuration after the preview. The send's transaction
                # rolled back, so nothing was sent: a stale preview, not an
                # unknown outcome.
                raise StaleRecordError(
                    "Review a fresh campaign test preview."
                ) from None
            if not repeat:
                record_action(
                    Action.ADMIN_CMD_TEST_SAMPLE,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
                written.append(True)
        return sample_test_model(row, created=not repeat)

    try:
        model = _held(step)
    except signing.BadSignature:
        raise ValueError("The preview token is not valid.") from None
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except (NotAvailable, PermissionError) as error:
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        raise error
    context["committed"] = True
    return model


def _revision(token):
    """The email revision a preview token names, for ``request_sample``.

    The binding names the template's content version, not its revision
    record, so this finds the record. A token that cannot be read or names
    nothing is ``invalid``; ``request_sample`` then verifies everything.
    """
    from django.core import signing

    from .accounts.campaign_mail import SALT
    from .accounts.content_models import ContentVersion

    try:
        template = UUID(signing.loads(token, salt=SALT)["template"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    record = (
        ContentVersion.objects.filter(pk=template)
        .values_list("record_id", flat=True)
        .first()
    )
    if record is None:
        raise ValueError("The preview token is not valid.")
    return record


# ------------------------------------------------------- chosen-Family tests
# ADM-11 PR 6c: Send to chosen Families. The worker renders each named
# Family's real message with its Testing code and link and sends it only to
# the Testing recipient; this process never sees a code, a link, rendered
# content or an address. Family names stay on the page.

# The page's acknowledgement, asked at the prompt: the checkbox's words.
FAMILIES_ACKNOWLEDGEMENT = (
    "I understand that these real Families' names and codes will be sent "
    "to the Testing recipient."
)


@dataclass(frozen=True)
class FamiliesPreview(ReadModel):
    """The page's review of a chosen-Family test, and the token that sends it.

    ``families`` lists each DUID given, in order, with ``eligible`` and
    ``eligibility`` (the page's reason code); never a name.
    ``credentials_ready`` is whether the Testing codes exist (an active
    rehearsal epoch); ``held`` whether a restore review or campaign work
    refuses sending for now. ``available``
    is how many more tests may be in progress. ``preview.token`` is valid
    for the page's preview lifetime. ``export`` is None unless ``--names``
    asked for the names: then it is the ``export status`` document of the
    export that holds them, for ``export fetch`` (#817).
    """

    campaign_id: UUID
    revision_id: UUID
    request_key: UUID
    families: list
    available: int
    in_progress: int
    held: bool
    credentials_ready: bool
    testing_recipient_set: bool
    preview: dict
    export: dict | None = None


def families_preview_model(preview, token, revision_id, export=None):
    """The command's projection of the page's ``FamilyTestPreview``."""
    return FamiliesPreview(
        campaign_id=preview.campaign.pk,
        revision_id=revision_id,
        request_key=preview.request_key,
        families=[
            {
                "duid": choice.duid,
                "eligible": choice.eligible,
                "eligibility": choice.reason,
            }
            for choice in preview.families
        ],
        available=preview.available,
        in_progress=preview.in_progress,
        held=preview.held,
        credentials_ready=preview.epoch_id is not None,
        testing_recipient_set=bool(preview.testing_recipient),
        preview={"token": token},
        export=export,
    )


def preview_families(caller, service, revision_id, duids, *, request_key):
    """``test families-preview``: the page's review of the chosen Families.

    ``duids`` is the page's input, at most ten distinct DUIDs
    (``parse_family_duids``). An unknown revision is ``not_available``; an
    email no current schedule uses, or a campaign that is not the current
    Testing draft, is ``denied``, as the page refuses. Records no event.
    """
    from django.core import signing
    from django.core.exceptions import ObjectDoesNotExist

    from .accounts.campaign_family_test import SALT, parse_family_duids, prepare
    from .accounts.policy import Capability

    parsed = parse_family_duids(" ".join(str(duid) for duid in duids))
    if not parsed:
        raise ValueError("Name at least one Family DUID.")
    actor = _admit(caller, service.store, final=True)

    def step():
        """The page's preview, in its work transaction."""
        campaign_id = _current_campaign(service)
        try:
            preview = prepare(
                caller,
                service,
                campaign_id,
                revision_id,
                parsed,
                request_key=request_key,
            )
        except ObjectDoesNotExist:
            raise NotAvailable("No such email revision.") from None
        except LookupError as error:
            # Not used by a current schedule: the page's refusal.
            raise PermissionError(str(error)) from None
        token = signing.dumps(preview.binding(), salt=SALT)
        return families_preview_model(preview, token, revision_id)

    try:
        model = _held(step)
    except (NotAvailable, PermissionError) as error:
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        raise error
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def preview_families_with_names(
    caller, service, revision_id, duids, *, request_key, zone, context
):
    """``test families-preview --names``: the review, and its names as a file.

    The page shows each Family's name beside its DUID; the command line
    never prints one (#817). This runs the page's own ``prepare`` and, in
    the same transaction, captures the names it read
    (``snapshot_family_names``) into a ``family_test_names`` export keyed by
    the preview's request key, so the names reach only the file the worker
    renders and ``export fetch`` downloads. The document is the preview's,
    with ``export`` the export's ``export status`` document.

    Unlike the plain review this changes state, so it admits as a form post
    does (recording activity, so a full-scope session) and runs in the
    export commands' scope: the work order first (``prepare`` joins it, and
    the export lock is then implied), then the command session's row, with
    the Administrator rechecked before and after. It records the export
    service's ``export_requested`` and, only for a new export,
    ``admin_cmd_export_family_test_names``. The same key again with the same
    revision, DUIDs and time zone returns the same export (``export``
    unchanged, no new event); the same key for another selection is
    ``invalid``, as the export forms' 409.
    """
    from django.core import signing
    from django.core.exceptions import ObjectDoesNotExist

    from .accounts.campaign_family_test import SALT, parse_family_duids, prepare
    from .accounts.policy import Capability
    from .admin_exports import (
        _change,
        _command_event,
        _command_scope,
        _status_document,
    )
    from .audit.schemas import Action
    from .reports.export_models import ExportRequest
    from .reports.family_test_names import create_family_test_names_export

    parsed = parse_family_duids(" ".join(str(duid) for duid in duids))
    if not parsed:
        raise ValueError("Name at least one Family DUID.")
    actor = _admit(caller, service.store)
    context["request_id"] = str(request_key)

    def step(written):
        """The page's preview and the names' export, in one transaction."""
        with _command_scope(caller, service, actor):
            campaign_id = _current_campaign(service)
            try:
                preview = prepare(
                    caller,
                    service,
                    campaign_id,
                    revision_id,
                    parsed,
                    request_key=request_key,
                )
            except ObjectDoesNotExist:
                raise NotAvailable("No such email revision.") from None
            except LookupError as error:
                # Not used by a current schedule: the page's refusal.
                raise PermissionError(str(error)) from None
            repeat = ExportRequest.objects.filter(
                requester_id=actor.identity, request_key=request_key
            ).exists()
            job = create_family_test_names_export(
                service.store,
                actor.identity,
                campaign_id=campaign_id,
                revision_id=revision_id,
                families=[(choice.duid, choice.name) for choice in preview.families],
                browser_timezone=zone,
                request_key=request_key,
            )
            if not repeat:
                _command_event(caller, actor, Action.ADMIN_CMD_EXPORT_FAMILY_TEST_NAMES)
                written.append(True)
            document = _status_document(service, actor, job.pk)
        token = signing.dumps(preview.binding(), salt=SALT)
        return families_preview_model(preview, token, revision_id, export=document)

    try:
        return _change(caller, service, actor, step, context=context, conflicts=False)
    except NotAvailable:
        # As the plain review: the session is checked before the refusal.
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        raise


@dataclass(frozen=True)
class FamiliesTest(ReadModel):
    """The tickets a ``test families`` created, or found for its token's key.

    One ticket per Family, ``sequence`` in the preview's DUID order; no DUID
    or Family id. ``created`` is false for a repeat, which sends nothing.
    """

    created: bool
    request_key: UUID
    tickets: list


def families_test_model(rows, *, created, request_key):
    """The command's projection of the page's ``FamilyMailTest`` tickets."""
    return FamiliesTest(
        created=created,
        request_key=request_key,
        tickets=[
            {
                "id": row.pk,
                "sequence": row.sequence,
                "state": row.state,
                "task_id": row.task_id,
            }
            for row in rows
        ],
    )


def families_count(token):
    """How many Families a token names, for the prompt; None if unreadable."""
    from django.core import signing

    from .accounts.campaign_family_test import SALT

    try:
        return len(signing.loads(token, salt=SALT)["families"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None


def send_families(caller, service, *, token, context):
    """``test families``: the page's confirmation, with the box ticked.

    The command line asked for the page's acknowledgement first. Runs the
    page's ``request_tests`` in the delivery pages' command scope, where
    ``require_fresh`` takes the automation branch (ADM-11 PR 5a) and records
    the fresh-gate event and notice. The token's revision is the email it
    names. Refusals are as ``request_tests`` raises them: a stale preview
    (including a revision since gone from the active configuration),
    credentials not ready, held work, an ineligible Family or too many in
    progress are ``stale_version``; an altered token ``invalid``; another
    Administrator's token, or an email no current schedule uses any more,
    ``denied``. Every refusal rolls back, so none is ``outcome_unknown``.
    """
    from django.core import signing
    from django.core.exceptions import ObjectDoesNotExist

    from .accounts.campaign_family_test import SALT, request_tests
    from .accounts.policy import Capability
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.family_mail_models import FamilyMailTest
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal
    from .storage import StaleRecordError

    actor = _admit(caller, service.store, final=True)
    try:
        binding = signing.loads(token, salt=SALT)
        key, revision_id = UUID(binding["key"]), UUID(binding["template"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    context["request_id"] = str(key)
    written = []

    def step():
        """Send inside the page's command scope, with the command's event."""
        written.clear()
        campaign_id = _current_campaign(service)
        # The fresh-gate notice names the campaign.
        caller.campaign_id = campaign_id
        with command_scope(caller, service, actor, admit=_admit):
            repeat = FamilyMailTest.objects.filter(
                requested_by_id=actor.identity, request_key=key
            ).exists()
            try:
                rows = request_tests(
                    caller,
                    service,
                    campaign_id,
                    revision_id,
                    preview_token=token,
                    acknowledge=True,
                )
            except ObjectDoesNotExist:
                # The revision left the active configuration after the
                # preview. The transaction rolled back and no ticket exists:
                # a stale preview, not an unknown outcome.
                raise StaleRecordError("Review a fresh Family test preview.") from None
            except LookupError as error:
                # No current schedule uses the email any more: the page's
                # refusal, as for the preview. Nothing was written.
                raise PermissionError(str(error)) from None
            if not repeat:
                record_action(
                    Action.ADMIN_CMD_TEST_FAMILIES,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
                written.append(True)
        return families_test_model(rows, created=not repeat, request_key=key)

    try:
        model = _held(step)
    except signing.BadSignature:
        raise ValueError("The preview token is not valid.") from None
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except (NotAvailable, PermissionError) as error:
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        raise error
    context["committed"] = True
    return model


@dataclass(frozen=True)
class FamiliesStatus(ReadModel):
    """The page's Recent Family tests, without DUIDs (as delivery documents)."""

    campaign_id: UUID
    tickets: list


def families_status_model(campaign_id, items):
    """The command's projection of ``recent_tickets``; the DUID is left out."""
    return FamiliesStatus(
        campaign_id=campaign_id,
        tickets=[
            {
                key: item[key]
                for key in (
                    "id",
                    "created_at",
                    "request_key",
                    "sequence",
                    "state",
                    "message_state",
                )
            }
            for item in items
        ],
    )


def read_families_status(caller, service):
    """``test status``: the page's Recent Family tests, for any session.

    Admits passively with the page's ``CONFIGURE`` capability and records no
    event, as the page's status region records none.
    """
    from .accounts.campaign_family_test import recent_tickets
    from .accounts.policy import Capability
    from .admin_reads import _admit as admit_read
    from .campaigns.work_locks import read_transaction

    def step():
        """Admit, read in one snapshot, recheck."""
        actor = admit_read(caller, service.store, Capability.CONFIGURE)
        with read_transaction():
            campaign_id = _current_campaign(service)
            items = recent_tickets(campaign_id)
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return families_status_model(campaign_id, items)

    return _held(step)
