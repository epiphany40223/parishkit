"""The seeder's real web entry points (#476): Admin go-live and Family events.

Everything here calls the production code the Admin and Family pages call,
inside a process configured like the web service (``configure_web_runtime``)
and under the web identity. Requests are real Django request objects built
with ``RequestFactory``; the Admin session is created and made fresh through
``establish_identity``, the identity core the Google callback and the LOCAL
test sign-in share, so sessions, audit and step-up behave as after a real
sign-in. Family sessions are minted by ``issue_family`` from the Family's
code, the form baseline by ``issue_baseline``, presence by the real heartbeat
view and submissions by ``submit_family``. In Testing mode the Family signs in
with the rehearsal code its chosen-Family Testing send carried
(``RehearsalFamilyWebDriver``), and that send is made through the page's own
review and confirmation (``family_tests``). No row is written by anything but
production code.

The module also drives the setup wizard through the same service layer the
wizard pages call (``begin_setup``, ``save_section``, ``stage_credential``,
``start_source_load``, ``prepare_preview``, ``request_sample``,
``freeze_setup``), and then Create the campaign through that page's own
form, review and confirmation (``create_campaign``): a LOCAL-only
convenience for an unattended install, since the environment is otherwise
set up by a developer in the browser.
"""

import logging
from dataclasses import dataclass
from importlib import import_module
from ipaddress import ip_address
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from parishkit.config import ConfigError

from . import LOCAL_ORGANIZATION_ID, LOCAL_PARISHSOFT_KEY
from .seed_timeline import PLEDGE_AMOUNTS

LOGGER = logging.getLogger(__name__)
# The seeder acts from the VM's loopback: the limiter keys its budgets by
# source address, and Family logins share one; the family_ip allowance (100
# in 10 minutes) covers the default parish, and each Family's login runs
# minutes apart in real time anyway.
SEEDER_ADDRESS = "127.0.0.1"
LOCAL_HOST = "localhost:8443"
# Wizard values for the unattended LOCAL install; every address is synthetic.
WIZARD_PARISH = {
    "name": "Synthetic Parish",
    "website": "https://parish.example.test/",
    "timezone": "America/New_York",
    "phone": "+15025551234",
    "online_giving_url": "",
}
# Sign-in is by address only (#952): one synthetic Staff user (#993).
WIZARD_ACCESS = {
    "staff_addresses": ["staff@example.test"],
    "ministry_addresses": [],
    "admin_addresses": [],
}
WIZARD_MAIL = {
    "delegated_email": "stewardship@example.test",
    "sender": "stewardship@example.test",
    "sender_name": "Synthetic Parish Stewardship",
    "reply_to": "office@example.test",
}
WIZARD_SLACK = {"enabled": False, "channel_id": ""}
WIZARD_TESTING = {"testing_recipient": "testing@example.test"}


class SeedWebRefused(ConfigError):
    """A real entry point refused the seeder's request."""


def _session_store(path):
    """A fresh Django session for one browser-like actor at ``path``.

    As the web middleware does, an Admin path gets the Admin store, which
    keeps only its key's digest (#995); a Family path gets Django's store.
    """
    from django.conf import settings

    from parishkit.stewardship.accounts.admin_session_store import SessionStore
    from parishkit.stewardship.web.namespaces import ADMIN_PREFIX

    if path.startswith(ADMIN_PREFIX):
        return SessionStore()
    return import_module(settings.SESSION_ENGINE).SessionStore()


def _reopened_admin_store(stored):
    """Reopen an Admin session from its stored key, for the seeder only.

    The database holds only a digest of an Admin key (#995), so the cookie
    key cannot be recovered. The seeder, which already runs with the
    database's own rights, reopens its own unfinished wizard session by
    using the stored key as is. Nothing on the web path constructs this.
    """
    from parishkit.stewardship.accounts.admin_session_store import SessionStore

    class Reopened(SessionStore):
        def _db_key(self, session_key):
            return session_key

    return Reopened(session_key=stored)


def new_request(path="/admin/", *, method="get", data=None, session=None):
    """A real WSGIRequest as the web middleware would present it to a view.

    The client address and the session are what the middleware chain sets;
    nothing else about the request is special. ``session`` reuses an actor's
    existing Django session (the same cookie, in browser terms).
    """
    from django.test import RequestFactory

    factory = RequestFactory(HTTP_HOST=LOCAL_HOST, REMOTE_ADDR=SEEDER_ADDRESS)
    if method == "post":
        request = factory.post(path, data or {}, secure=True)
    else:
        request = factory.get(path, secure=True)
    request.session = session if session is not None else _session_store(path)
    request.client_address = ip_address(SEEDER_ADDRESS)
    request.internal_request = False
    return request


# Admin.
def sign_in_admin(request, email):
    """Sign the Admin in (or step up) through the shared identity core.

    This is exactly what the LOCAL test sign-in does after consuming its
    token: the subject is ``local-test:<email>``, the recovery epoch is the
    current one and freshness is now. A redirect means success; any other
    response is the core's uniform denial.
    """
    from django.http import HttpResponseRedirect

    from parishkit.stewardship.accounts.authentication import establish_identity
    from parishkit.stewardship.accounts.local_sign_in import SUBJECT_PREFIX
    from parishkit.stewardship.accounts.policy_schema import normalized_email
    from parishkit.stewardship.accounts.sessions import database_now, revocation_epoch

    address = normalized_email(email)
    response = establish_identity(
        request,
        SUBJECT_PREFIX + address,
        address,
        None,
        authenticated_at=database_now(),
        recovery_epoch=revocation_epoch(),
        destination=None,
    )
    if not isinstance(response, HttpResponseRedirect):
        raise SeedWebRefused(f"The Admin sign-in was refused ({response.status_code}).")
    return request


def admin_runtime():
    """The web process's authentication runtime (store, limiter, setup marker)."""
    from parishkit.stewardship.accounts.authentication import runtime

    return runtime()


def family_runtime():
    """The web process's Family runtime (store, limiter and keyrings)."""
    from parishkit.stewardship.accounts.family_authentication import runtime

    return runtime()


# Readiness and go-live.
def request_full_refresh(request, service):
    """Ask for the full ParishSoft refresh an Admin's "Refresh now" requests."""
    from parishkit.stewardship.accounts.admin_editing import principal
    from parishkit.stewardship.observability import current_correlation
    from parishkit.stewardship.source.requests import request_refresh

    actor = principal(request, service)

    def authorize(scope):
        """The same Admin, still authorized, under the locked scope."""
        return principal(request, service, passive=True).identity == actor.identity

    return request_refresh(
        command_id=uuid4(),
        cause="manual",
        actor_id=actor.identity,
        correlation_id=current_correlation(),
        authorize=authorize,
    )


def import_leader(request, service, leaders, email):
    """Import one ParishSoft Ministry leader as a user, as the Users page does.

    ``leaders`` is ``read_leaders``' answer under this Admin's session and
    ``email`` the chosen address. This is the page's import review
    (``leader_import_views.user_leader_import``: ``plan_import`` against the
    applied users in one read-only snapshot, signed with ``leader_import``)
    followed by its Apply (``confirm_intent`` with the Users page's audit
    attachment), so the user is a Ministry leader and the change is one
    configuration request the installer applies. Returns its status.
    """
    from django.urls import reverse

    from parishkit.stewardship.accounts.admin_editing import (
        confirm_intent,
        principal,
        sign_preview,
    )
    from parishkit.stewardship.accounts.leader_import import plan_import
    from parishkit.stewardship.accounts.policy import Capability
    from parishkit.stewardship.accounts.user_views import (
        SALT,
        _audit_change,
        _scope,
        _users,
    )
    from parishkit.stewardship.campaigns.work_locks import read_transaction

    actor = principal(request, service, capability=Capability.MANAGE_USERS)
    with read_transaction():
        configuration, records = _users(service)
        plan = plan_import(records, leaders, [email])
        if not plan.patch:
            raise SeedWebRefused("The chosen Ministry leader cannot be imported.")
        token = sign_preview(
            actor=actor,
            configuration=configuration,
            patch=plan.patch,
            salt=SALT,
            extra={"leader_import": True},
        )
    return confirm_intent(
        request,
        service,
        actor,
        token=token,
        salt=SALT,
        link=reverse("admin:users"),
        current_scope=_scope,
        capability=Capability.MANAGE_USERS,
        attach=_audit_change(actor),
    )


def sample_mail(request, service, campaign_id, revision_id):
    """Send the sample of one campaign email to the Testing recipient.

    This is the campaign email page's "send a sample" action (``prepare`` is
    its review, ``request_sample`` its send, the signed preview between them
    the page's own binding), and the test go-live readiness requires of a
    Family template: an accepted ``CampaignMailTest`` for the Initial or a
    Reminder revision under the current configuration and credential.
    """
    from django.core import signing

    from parishkit.stewardship.accounts.campaign_mail import (
        SALT,
        prepare,
        request_sample,
    )

    preview = prepare(request, service, campaign_id, revision_id)
    token = signing.dumps(preview.binding(), salt=SALT)
    return request_sample(
        request, service, campaign_id, revision_id, preview_token=token
    )


def start_go_live(request, service, campaign_id):
    """Verify readiness and start the Testing cleanup (the real go-live page)."""
    from parishkit.stewardship.accounts.go_live_commands import (
        start_cleanup,
        verify_preview,
    )

    inputs, verified, token = verify_preview(request, service, campaign_id)
    if inputs.problems or not verified or token is None:
        raise SeedWebRefused(
            "Go-live readiness is not met: "
            + (", ".join(inputs.problems) or "origin unverified")
        )
    if inputs.target_state != "scheduled":
        raise SeedWebRefused(
            f"Go-live would activate as {inputs.target_state}, not scheduled."
        )
    return start_cleanup(
        request, service, campaign_id, preview_token=token, acknowledge=True
    )


def prepare_links(request, service, campaign_id, transition_id):
    """Start link preparation with the page's own signed control."""
    from parishkit.stewardship.accounts.activation_progress import control, progress
    from parishkit.stewardship.web.contracts import PageWindow

    context = progress(
        request, service, campaign_id, transition_id, window=PageWindow(1, 25)
    )
    if context["prepare"] is None:
        raise SeedWebRefused("Link preparation is not available for this cleanup.")
    return control(
        request, service, campaign_id, transition_id, token=context["prepare"]
    )


def confirm_production(request, service, campaign_id, transition_id, preparation_id):
    """Verify the confirmation preview and confirm, as the confirmation page does."""
    from parishkit.stewardship.accounts import confirmation_commands

    preview, verified, token = confirmation_commands.verify_preview(
        request, service, campaign_id, transition_id, preparation_id
    )
    if preview.problems or not verified or token is None:
        raise SeedWebRefused(
            "Production confirmation is not ready: "
            + (", ".join(preview.problems) or "origin unverified")
        )
    return confirmation_commands.confirm(
        request,
        service,
        campaign_id,
        transition_id,
        preparation_id,
        token=token,
        typed="Production",
    )


# Families.
@dataclass
class FamilyActor:
    """One Family's browser: its request (session) and the form it opened."""

    family_id: UUID
    code: str
    request: object = None
    form: object = None


class FamilyWebDriver:
    """Runs the timeline's Family events through the real Family entry points.

    Families are keyed by ``FamilyCampaign`` id. Each Family keeps one Django
    session across its events, as a browser would; a later session event (a
    re-submission) signs in again. Codes are read from the campaign rows the
    way the Admin's code report reads them, with the web's own keyring.
    """

    def __init__(self, service, campaign):
        """Drive ``campaign``'s Families with the web's Family runtime (keyrings)."""
        self.service = service
        self.campaign = campaign
        self.actors = {}

    def run(self, event):
        """Dispatch one event to the method named by its kind."""
        return getattr(self, event.kind)(event.family, event.data)

    def _actor(self, family_id):
        """The Family's actor, created with its decrypted code on first use."""
        actor = self.actors.get(family_id)
        if actor is None:
            from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

            row = FamilyCampaign.objects.get(pk=family_id, campaign=self.campaign)
            actor = self.actors[family_id] = FamilyActor(family_id, self._code(row))
        return actor

    def _code(self, row):
        """The Family's live code, decrypted with the web's own keyring."""
        from parishkit.stewardship.campaigns.family_identity import code_context

        if row.code_ciphertext is None:
            raise SeedWebRefused(f"Family {row.pk} has no code yet.")
        return self.service.general.decrypt(
            row.code_ciphertext, context=code_context(row.pk)
        ).decode()

    def session(self, family_id, data):
        """Sign the Family in with its code: the "link followed" event."""
        from parishkit.stewardship.accounts.family_authentication import (
            issue_family,
            lookup,
        )

        actor = self._actor(family_id)
        request = new_request("/", method="post", data={"code": actor.code})
        identity = lookup(self.service, code=actor.code)
        if identity is None or identity[0] != family_id:
            raise SeedWebRefused(f"Family {family_id}'s code was not recognized.")
        if not issue_family(request, self.service, identity, code=actor.code):
            raise SeedWebRefused(f"Family {family_id}'s sign-in was refused.")
        actor.request, actor.form = request, None
        return actor

    def _request(self, family_id, path, **options):
        """A new request in the Family's existing session (its browser tab)."""
        actor = self._actor(family_id)
        if actor.request is None:
            raise SeedWebRefused(f"Family {family_id} has no session yet.")
        return new_request(path, session=actor.request.session, **options)

    def baseline(self, family_id, data):
        """Open the form: the answer-free baseline the form page requests."""
        from parishkit.stewardship.campaigns.work_locks import work_transaction
        from parishkit.stewardship.responses.baselines import issue_baseline

        request = self._request(family_id, "/family/form", method="post")
        with work_transaction():
            form = issue_baseline(request, self.service)
        self._actor(family_id).form = form
        return form

    def presence(self, family_id, data):
        """One presence heartbeat for a section, through the real view."""
        from parishkit.stewardship.accounts.presence import heartbeat

        request = self._request(
            family_id,
            "/family/presence",
            method="post",
            data={"section": data["section"]},
        )
        response = heartbeat(request)
        if response.status_code != 200:
            raise SeedWebRefused(
                f"Presence for Family {family_id} answered {response.status_code}."
            )
        return response

    def submission(self, family_id, data):
        """Submit the form with answers built from its own authorized projection."""
        from parishkit.stewardship.campaigns.work_locks import work_transaction
        from parishkit.stewardship.responses.submission import submit_family

        actor = self._actor(family_id)
        if actor.form is None:
            self.baseline(family_id, data)
        request = self._request(family_id, "/family/submit", method="post")
        for _attempt in range(2):
            payload = build_answers(actor.form, data.get("answers", {}))
            with work_transaction():
                result = submit_family(
                    request,
                    self.service,
                    baseline_id=actor.form.baseline.pk,
                    payload=payload,
                )
            if result.refreshed is None:
                actor.form = None
                return result
            # The source moved under the form: review the refreshed baseline
            # once, as the browser would, then submit again.
            actor.form = result.refreshed
        raise SeedWebRefused(f"Family {family_id}'s submission kept needing review.")


class RehearsalFamilyWebDriver(FamilyWebDriver):
    """The Family driver in Testing mode, signing in with rehearsal codes (#1413).

    In Testing mode the Family sign-in accepts only the rehearsal code of the
    campaign's active rehearsal epoch, which the worker creates when it
    prepares a chosen-Family Testing send and puts in that message. The code
    is read from the Family's rehearsal credential and decrypted with the
    web's keyring under the rehearsal code context, as the live code is read
    from the campaign row; every later step is the base driver's.
    """

    def __init__(self, service, campaign, epoch_id):
        """Drive ``campaign``'s Families with ``epoch_id``'s rehearsal codes."""
        super().__init__(service, campaign)
        self.epoch_id = epoch_id

    def _code(self, row):
        """The Family's rehearsal code for this epoch; refused if it has none."""
        from parishkit.stewardship.campaigns.credential_models import (
            RehearsalCredential,
        )
        from parishkit.stewardship.campaigns.rehearsals import code_context

        credential = RehearsalCredential.objects.filter(
            family=row, epoch_id=self.epoch_id
        ).first()
        if credential is None:
            raise SeedWebRefused(f"Family {row.pk} has no Testing code yet.")
        return self.service.general.decrypt(
            credential.code_ciphertext, context=code_context(credential.pk)
        ).decode()


def family_tests(request, service, campaign_id, revision_id, duids):
    """Send chosen-Family Testing sends of one email, as that page does (#1413).

    The page's review (``campaign_family_test.prepare``, signed with its own
    salt) followed by its confirmation (``request_tests``, acknowledged), so
    each Family gets one ticket that the worker prepares with its Testing
    code and mail-dispatch sends to the Testing recipient only. The caller
    signs in again first: the confirmation needs a fresh sign-in. Returns
    the tickets.
    """
    from django.core import signing

    from parishkit.stewardship.accounts.campaign_family_test import (
        SALT,
        prepare,
        request_tests,
    )

    preview = prepare(request, service, campaign_id, revision_id, tuple(duids))
    refused = sorted(
        {choice.reason for choice in preview.families if not choice.eligible}
    )
    if refused or preview.epoch_id is None or preview.held:
        raise SeedWebRefused(
            "The chosen-Family Testing send is unavailable: "
            + (", ".join(refused) or "no rehearsal epoch, or campaign work held")
        )
    return request_tests(
        request,
        service,
        campaign_id,
        revision_id,
        preview_token=signing.dumps(preview.binding(), salt=SALT),
        acknowledge=True,
    )


# A proposed new household Member's invented first names (#498); the
# surname follows the household's.
NEW_MEMBER_NAMES = ("Avery", "Jordan", "Riley", "Quinn", "Rowan", "Emerson")


def proposed_member(inputs, members):
    """One invented new household Member: (its local id, its form answers).

    The id is a UUID derived from the Family's DUID, so a resubmission after a
    refreshed baseline proposes the same person, not a second one. Every
    ordinary Member field is answered, as the form requires: a first name,
    the household's surname, an adult's birth date (a new Member may join
    a Ministry), a gender and a language.
    """
    from parishkit.stewardship.responses.member_census import MEMBER_FIELDS

    duid = inputs.family_duid
    identity = str(uuid5(NAMESPACE_URL, f"parishkit-local-seed:member:{duid}"))
    surname = next(
        (m["last_name"] for _, m in sorted(members.items()) if m.get("last_name")),
        "Sample",
    )
    answers = {field.name: "" for field in MEMBER_FIELDS} | {
        "first_name": NEW_MEMBER_NAMES[duid % len(NEW_MEMBER_NAMES)],
        "last_name": surname,
        "birth_date": "1994-03-15",
        "gender": ("Female", "Male")[duid % 2],
        "language": "English",
    }
    return identity, answers


def build_answers(form, answers):
    """The browser's complete answer payload for a form, from its projection.

    Every Member field is answered with its current value (the form's own
    ``browser_value``), the household with blank addresses, and the timeline's
    abstract answers map onto the modules the campaign enabled: a pledge onto
    the financial section, Ministry interest onto one adult's join choices, a
    census edit onto a changed email or a proposed new Member (who joins the
    first offered Ministry), an opt-out onto the household flag and free text
    onto additional information.
    """
    from parishkit.stewardship.responses.member_census import (
        MEMBER_FIELDS,
        browser_value,
    )

    inputs = form.inputs
    definitions = {field.name: field for field in MEMBER_FIELDS}
    members = {}
    for field in inputs.fields:
        if field.entity == "member" and field.field in definitions:
            members.setdefault(str(field.identity), {})[field.field] = browser_value(
                definitions[field.field], field.source
            )
    for identifier in inputs.member_duids:
        members.setdefault(str(identifier), {})
    census = "census" in inputs.modules
    if census and answers.get("census_edit") == "changed_email":
        for key in sorted(members):
            if "email" in members[key]:
                members[key]["email"] = f"family{key}@example.test"
                break
    proposed = {}
    if census and answers.get("census_edit") == "proposed_member":
        identity, member = proposed_member(inputs, members)
        proposed[identity] = member
    blank = {
        "line1": "",
        "line2": "",
        "city": "",
        "region": "",
        "postal_code": "",
        "country": "",
    }
    payload = {
        "family": {
            "home_address": dict(blank),
            "mailing_address": dict(blank),
            "mailing_same_as_home": False,
            "email_opt_out": True if answers.get("email_opt_out") else None,
        }
        if census
        else {},
        "members": members if census else {key: {} for key in members},
        "proposed_members": proposed,
        "ministries": {},
        "additional_information": answers.get("information", "") or "",
    }
    if inputs.ministries is not None:
        chosen = [int(value) for value in answers.get("ministry_interest", [])]
        offered = [option.duid for option in inputs.ministries.options]
        joins = [duid for duid in offered if duid in chosen] or (
            offered[:1] if chosen else []
        )
        entries = {}
        for member, current in inputs.ministries.memberships:
            join = (
                [duid for duid in joins if duid not in current] if not entries else []
            )
            entries[str(member)] = {"join": join, "leave": []}
        # A proposed Member may only join; every one needs an entry.
        payload["ministries"] = {
            "members": entries,
            "proposed_members": {
                identity: {"join": offered[:1]} for identity in proposed
            },
        }
    if "financial" in inputs.modules:
        pledge = answers.get("pledge")
        if pledge is None:
            payload["financial"] = {
                "annual_pledge": "0",
                "frequency": "",
                "shares": {},
                "cannot_give": False,
            }
        else:
            options = inputs.financial.definition.options
            shares = {}
            for option in options:
                if not option.free_text:
                    shares[option.id] = ""
                    break
            payload["financial"] = {
                "annual_pledge": str(int(pledge)),
                "frequency": "monthly" if pledge >= PLEDGE_AMOUNTS[2] else "annual",
                "shares": shares,
                "cannot_give": False,
            }
    return payload


# The setup wizard, through its service layer.
def stage_logo(request, service, attempt_id):
    """Stage a small generated logo as the wizard's logo page does; return its id."""
    from io import BytesIO

    from django.conf import settings
    from PIL import Image

    from parishkit.stewardship.accounts.branding_staging import stage_branding
    from parishkit.stewardship.web.content import prepare_graphics

    image = BytesIO()
    Image.new("RGB", (256, 128), (36, 72, 120)).save(image, format="PNG")
    image.seek(0)
    return stage_branding(
        request,
        service,
        settings.STEWARDSHIP_MEDIA_ROOT,
        prepare_graphics(image),
        base_digest=service.store.active().digest,
        setup_attempt_id=attempt_id,
        mode="basic",
    )


def resume_wizard(service, email):
    """The seeding Admin's unfinished setup attempt and its live session, or None.

    A wizard run that stopped part-way leaves an attempt that only its own
    session may continue (``begin_setup`` refuses a second attempt). The
    seeder's Admin is a known identity, so its attempt's Django session is
    reopened from the PortalSession row and the run continues where it was.
    """

    from parishkit.stewardship.accounts.local_sign_in import SUBJECT_PREFIX
    from parishkit.stewardship.accounts.models import PortalSession, PortalUser
    from parishkit.stewardship.accounts.policy_schema import normalized_email
    from parishkit.stewardship.accounts.setup_models import SetupAttempt

    user = PortalUser.objects.filter(
        google_subject=SUBJECT_PREFIX + normalized_email(email)
    ).first()
    if user is None:
        return None
    attempt = (
        SetupAttempt.objects.filter(
            owner_id=user.pk, state__in=["collecting", "loading", "frozen"]
        )
        .order_by("-created_at")
        .first()
    )
    if attempt is None:
        return None
    session = PortalSession.objects.filter(
        pk=attempt.session_id, revoked_at__isnull=True
    ).first()
    if session is None:
        return None
    return new_request(session=_reopened_admin_store(session.session_id)), attempt.pk


def run_wizard(
    request,
    service,
    *,
    wait,
    fake_key=LOCAL_PARISHSOFT_KEY,
    attempt_id=None,
):
    """Complete the initial setup wizard for the LOCAL deployment unattended.

    The steps are the wizard pages' own service calls, in the wizard's order
    (``setup_wizard.PAGES``, #1353): the parish, mail and Testing settings,
    the Workspace credential (the mail-catcher document), the Slack setting
    (off), a generated parish logo through the real branding staging, the
    access lists, the ParishSoft credential (the fake's key and organization),
    the source load (the worker loads from the fake; ``wait`` blocks until the
    attempt collects again), the fixed setup test mail (sent to Mailpit by
    mail-dispatch), the preview and the final confirmation. The caller then
    waits for ``setup_is_complete``. Setup collects no campaign (#142); the
    seeder's ``campaign`` step creates it afterwards (``create_campaign``).
    """
    from parishkit.stewardship.accounts.setup_models import SetupAttempt

    def state():
        """The resumed attempt's state, or None for a fresh run."""
        if attempt_id is None:
            return None
        return SetupAttempt.objects.get(pk=attempt_id)

    row = state()
    if row is not None and row.state == "frozen":
        # Confirmed already; only the installer's finish is outstanding.
        return None
    if row is None or row.source_task_id is None:
        attempt_id = wizard_collect(request, service, fake_key=fake_key)

    def loaded():
        """The attempt collects again once the worker's load has finished.

        A failed load also returns it to collecting (#1353), so the bound
        Task's state says which: a failed load stops the run.
        """
        row = SetupAttempt.objects.select_related("source_task").get(pk=attempt_id)
        if row.state == "collecting" and row.source_task_id is not None:
            if row.source_task.state != "succeeded":
                raise SeedWebRefused(
                    f"The setup source load ended in {row.source_task.state}."
                )
            return True
        if row.state not in {"loading", "collecting"}:
            raise SeedWebRefused(f"The setup source load ended in {row.state}.")
        return "the setup source load"

    wait("the setup source load", loaded)
    return wizard_confirm(request, service, attempt_id, wait=wait)


def wizard_collect(request, service, *, fake_key=LOCAL_PARISHSOFT_KEY):
    """The wizard's data-entry pages, in page order, ending with the source load.

    The order follows ``setup_wizard.PAGES`` (#1353): ParishSoft connection
    and Load parish data come last, after the logo and access pages, so the
    load is the only step left to wait for before the review.
    """
    from parishkit.stewardship.accounts.setup_credentials import stage_credential
    from parishkit.stewardship.accounts.setup_drafts import save_section
    from parishkit.stewardship.accounts.setup_source import start_source_load
    from parishkit.stewardship.accounts.setup_staging import begin_setup
    from parishkit.stewardship.mail_catcher import MAIL_CATCHER_DOCUMENT

    status = begin_setup(request, service)
    attempt_id = status.attempt_id

    def save(step, values):
        """Save one public settings page on the latest ``status`` version."""
        return save_section(
            request,
            service,
            attempt_id,
            step=step,
            values=values,
            expected_version=status.version,
        )

    for step, values in (
        ("parish", WIZARD_PARISH),
        ("mail", WIZARD_MAIL),
        ("testing", WIZARD_TESTING),
    ):
        status = save(step, values)
    status, _ = stage_credential(
        request,
        service,
        attempt_id,
        target="google_workspace",
        candidate=MAIL_CATCHER_DOCUMENT,
        expected_version=status.version,
    )
    status = save("slack", WIZARD_SLACK)
    status = wizard_branding(request, service, attempt_id)
    status = save("access", WIZARD_ACCESS)
    status, _ = stage_credential(
        request,
        service,
        attempt_id,
        target="parishsoft",
        candidate=fake_key.encode() + b"\n",
        expected_version=status.version,
        organization_id=LOCAL_ORGANIZATION_ID,
    )
    start_source_load(request, service, attempt_id, expected_version=status.version)
    return attempt_id


def wizard_branding(request, service, attempt_id):
    """A generated parish logo, saved as the Parish logos page does."""
    from parishkit.stewardship.accounts.setup_drafts import save_section, view_draft

    status = view_draft(request, service, attempt_id).status
    bundle = stage_logo(request, service, attempt_id)
    return save_section(
        request,
        service,
        attempt_id,
        step="branding",
        values={"bundle_id": str(bundle)},
        expected_version=status.version,
    )


def campaign_fields(form, campaign_dates):
    """The Create the campaign form, filled in as an Administrator would.

    Every module, every Ministry the form offers, and the first two funds
    (the second only for the comparison period, the first again when there
    is one fund). ``form`` is the unbound creation form, whose initial
    values (the Parish timezone and the applied configuration's digest) are
    kept; ``campaign_dates`` is the campaign's (start, end).
    """
    funds = [value for value, _ in form.fields["fund_duids"].choices]
    start, end = campaign_dates
    return {
        "name": "Annual stewardship campaign",
        # The year Families pledge for: the financial period below.
        "year_label": str(end.year + 1),
        "timezone": form.initial["timezone"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "census": "on",
        "ministry": "on",
        "financial_enabled": "on",
        "ministry_duids": [value for value, _ in form.fields["ministry_duids"].choices],
        "financial_start": f"{end.year + 1}-01-01",
        "financial_end": f"{end.year + 1}-12-31",
        "comparison_start": f"{end.year}-01-01",
        "comparison_end": f"{end.year}-12-31",
        "fund_duids": funds[:1],
        "comparison_fund_duids": funds[1:2] or funds[:1],
        "additional_information": "on",
        "base_digest": form.initial["base_digest"],
    }


def create_campaign(request, service, campaign_dates):
    """Create the deployment's campaign through Create the campaign (#142).

    The page's own steps: its admission (no campaign may exist), its form
    over the current source's Ministries and funds, its Review
    (``creation_review``, which adds the default text for every page and
    email) and its Apply (``confirm_intent`` with the page's salt and
    confirmation scope). Returns the configuration request's status; the
    configuration installer then applies it, which makes the campaign
    current. Its Families and codes come from the full refresh the caller
    requests afterwards, as the page's "Load the campaign's ParishSoft data"
    does.
    """
    from django.urls import reverse

    from parishkit.stewardship.accounts import campaign_create_views as page
    from parishkit.stewardship.accounts.admin_editing import (
        confirm_intent,
        principal,
    )
    from parishkit.stewardship.accounts.campaign_views import _state
    from parishkit.stewardship.campaigns.work_locks import work_transaction

    actor = principal(request, service)
    with work_transaction():
        state = _state(service)
        if not page._admitted(state):
            raise SeedWebRefused("Create the campaign is unavailable.")
        configuration, source = state[0], state[2]
        blank = page.creation_form(configuration, source)
        form = page.creation_form(
            configuration, source, campaign_fields(blank, campaign_dates)
        )
        review = page.creation_review(service, actor, state, form)
        if review is None:
            raise SeedWebRefused(
                f"Create the campaign refused the form: {form.errors.as_json()}"
            )
    return confirm_intent(
        request,
        service,
        actor,
        token=review["preview"],
        salt=page.SALT,
        link=reverse("admin:campaign_create"),
        current_scope=page._scope,
        first_campaign=True,
    )


def preview_token(request, service):
    """The signed review the confirmation page carries, from the current draft."""
    from django.core import signing

    from parishkit.stewardship.accounts.setup_preview import (
        PREVIEW_SALT,
        prepare_preview,
    )

    return signing.dumps(prepare_preview(request, service).binding(), salt=PREVIEW_SALT)


def wizard_confirm(request, service, attempt_id, *, wait):
    """Send the sample mail, wait for the catcher to take it, then confirm the
    current preview (the wizard's Test email, Review and Finish setup, #1476)."""
    from parishkit.stewardship.accounts.setup_confirmation import freeze_setup
    from parishkit.stewardship.accounts.setup_delivery_models import SetupMailDelivery
    from parishkit.stewardship.accounts.setup_mail import request_sample

    confirm_request = new_request("/admin/setup/confirm", session=request.session)
    sample = request_sample(
        confirm_request,
        service,
        preview_token=preview_token(confirm_request, service),
        request_key=uuid4(),
    )

    def sampled():
        """The sample mail reached the catcher (or failed) through mail-dispatch."""
        row = SetupMailDelivery.objects.get(pk=sample.identifier)
        if row.state == "accepted":
            return True
        if row.state not in {"queued", "submitting"}:
            raise SeedWebRefused(f"The setup sample mail ended in {row.state}.")
        return "the setup sample mail"

    wait("the setup sample mail", sampled)
    return freeze_setup(
        confirm_request, service, preview_token=preview_token(confirm_request, service)
    )
