"""The seeder's real web entry points (#476): Admin go-live and Family events.

Everything here calls the production code the Admin and Family pages call,
inside a process configured like the web service (``configure_web_runtime``)
and under the web identity. Requests are real Django request objects built
with ``RequestFactory``; the Admin session is created and made fresh through
``establish_identity``, the identity core the Google callback and the LOCAL
test sign-in share, so sessions, audit and step-up behave as after a real
sign-in. Family sessions are minted by ``issue_family`` from the Family's
code, the form baseline by ``issue_baseline``, presence by the real heartbeat
view and submissions by ``submit_family``. No row is written by anything but
production code.

The module also drives the setup wizard through the same service layer the
wizard pages call (``begin_setup``, ``save_section``, ``stage_credential``,
``start_source_load``, ``prepare_preview``, ``request_sample``,
``freeze_setup``): a LOCAL-only convenience for an unattended install, since
the environment is otherwise set up by a developer in the browser.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from ipaddress import ip_address
from uuid import UUID, uuid4

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
WIZARD_ACCESS = {
    "staff_domains": ["example.test"],
    "ministry_domains": [],
    "staff_addresses": [],
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


def _session_store():
    """A fresh Django session for one browser-like actor."""
    from django.conf import settings

    return import_module(settings.SESSION_ENGINE).SessionStore()


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
    request.session = session if session is not None else _session_store()
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
            from parishkit.stewardship.campaigns.family_identity import code_context

            row = FamilyCampaign.objects.get(pk=family_id, campaign=self.campaign)
            if row.code_ciphertext is None:
                raise SeedWebRefused(f"Family {family_id} has no code yet.")
            code = self.service.general.decrypt(
                row.code_ciphertext, context=code_context(row.pk)
            ).decode()
            actor = self.actors[family_id] = FamilyActor(family_id, code)
        return actor

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


def build_answers(form, answers):
    """The browser's complete answer payload for a form, from its projection.

    Every Member field is answered with its current value (the form's own
    ``browser_value``), the household with blank addresses, and the timeline's
    abstract answers map onto the modules the campaign enabled: a pledge onto
    the financial section, Ministry interest onto one adult's join choices, a
    census edit onto a changed email, an opt-out onto the household flag and
    free text onto additional information.
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
        "proposed_members": {},
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
        payload["ministries"] = {"members": entries, "proposed_members": {}}
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
    )


def resume_wizard(service, email):
    """The seeding Admin's unfinished setup attempt and its live session, or None.

    A wizard run that stopped part-way leaves an attempt that only its own
    session may continue (``begin_setup`` refuses a second attempt). The
    seeder's Admin is a known identity, so its attempt's Django session is
    reopened from the PortalSession row and the run continues where it was.
    """
    from django.conf import settings

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
    store = import_module(settings.SESSION_ENGINE).SessionStore
    return new_request(session=store(session_key=session.session_id)), attempt.pk


def run_wizard(
    request,
    service,
    *,
    wait,
    fake_key=LOCAL_PARISHSOFT_KEY,
    campaign_dates,
    attempt_id=None,
):
    """Complete the initial setup wizard for the LOCAL deployment unattended.

    The steps are the wizard pages' own service calls, in the wizard's order:
    the parish, access and mail settings, the ParishSoft credential (the
    fake's key and organization), the source load (the worker loads from the
    fake; ``wait`` blocks until the attempt collects again), the Workspace
    credential (the mail-catcher document), the first campaign from the
    loaded catalog with every module enabled, default pages and emails, the
    Initial schedule, a generated parish logo through the real branding
    staging, the preview, the sample mail (sent to Mailpit by mail-dispatch)
    and the final confirmation. The caller then waits for
    ``setup_is_complete``. The share options are part of the campaign values;
    the wizard's shares page only reviews them.
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
        """The attempt collects again once the worker's load has finished."""
        row = SetupAttempt.objects.get(pk=attempt_id)
        if row.state == "collecting":
            return True
        if row.state not in {"loading", "collecting"}:
            raise SeedWebRefused(f"The setup source load ended in {row.state}.")
        return "the setup source load"

    wait("the setup source load", loaded)
    wizard_campaign(request, service, attempt_id, campaign_dates=campaign_dates)
    return wizard_confirm(request, service, attempt_id, wait=wait)


def wizard_collect(request, service, *, fake_key=LOCAL_PARISHSOFT_KEY):
    """The wizard up to the source load: public settings and both credentials."""
    from parishkit.stewardship.accounts.setup_credentials import stage_credential
    from parishkit.stewardship.accounts.setup_drafts import save_section
    from parishkit.stewardship.accounts.setup_source import start_source_load
    from parishkit.stewardship.accounts.setup_staging import begin_setup
    from parishkit.stewardship.mail_catcher import MAIL_CATCHER_DOCUMENT

    status = begin_setup(request, service)
    attempt_id = status.attempt_id
    for step, values in (
        ("parish", WIZARD_PARISH),
        ("access", WIZARD_ACCESS),
        ("mail", WIZARD_MAIL),
        ("slack", WIZARD_SLACK),
        ("testing", WIZARD_TESTING),
    ):
        status = save_section(
            request,
            service,
            attempt_id,
            step=step,
            values=values,
            expected_version=status.version,
        )
    status, _ = stage_credential(
        request,
        service,
        attempt_id,
        target="parishsoft",
        candidate=fake_key.encode() + b"\n",
        expected_version=status.version,
        organization_id=LOCAL_ORGANIZATION_ID,
    )
    status, _ = stage_credential(
        request,
        service,
        attempt_id,
        target="google_workspace",
        candidate=MAIL_CATCHER_DOCUMENT,
        expected_version=status.version,
    )
    start_source_load(request, service, attempt_id, expected_version=status.version)
    return attempt_id


def campaign_values(catalog, campaign_dates):
    """The first campaign: every module, the catalog's Ministries and funds."""
    from parishkit.stewardship.accounts.share_forms import default_share_options

    funds = [int(key) for key, _ in catalog.funds]
    start, end = campaign_dates
    return {
        "name": "Annual stewardship campaign",
        "year_label": str(end.year),
        "timezone": catalog.timezone,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        # The canonical order is alphabetical (EnabledModules.to_list).
        "modules": ["census", "financial", "ministry"],
        # Sorted unique DUIDs; the catalog lists Ministries by name.
        "ministry_duids": sorted({int(key) for key, _ in catalog.ministries}),
        "financial": {
            "start": f"{end.year + 1}-01-01",
            "end": f"{end.year + 1}-12-31",
            "comparison_start": f"{end.year}-01-01",
            "comparison_end": f"{end.year}-12-31",
            "fund_duids": funds[:1],
            "comparison_fund_duids": funds[1:2] or funds[:1],
            "overlap_confirmed": False,
        },
        "share_options": default_share_options(),
        "content_versions": {},
        "additional_information": True,
    }


def wizard_campaign(request, service, attempt_id, *, campaign_dates):
    """After the load: the first campaign, default content, the Initial, a logo."""
    from parishkit.stewardship.accounts.content_forms import applicable_slots
    from parishkit.stewardship.accounts.setup_campaign import campaign_catalog
    from parishkit.stewardship.accounts.setup_content import (
        FILL_UNSET,
        default_updates,
    )
    from parishkit.stewardship.accounts.setup_drafts import (
        save_section,
        save_sections,
        view_draft,
    )

    status = view_draft(request, service, attempt_id).status
    catalog = campaign_catalog(request, service, attempt_id)
    campaign = campaign_values(catalog, campaign_dates)
    status = save_section(
        request,
        service,
        attempt_id,
        step="campaign",
        values={"source_result": str(catalog.result_id), "campaign": campaign},
        expected_version=status.version,
    )
    draft = view_draft(request, service, attempt_id)
    updates = default_updates(
        draft.sections, campaign, str(attempt_id), which=FILL_UNSET
    )
    emails = {
        f"{kind}_{slot}" for kind, slot in applicable_slots(campaign) if kind == "email"
    }
    if not emails <= set(updates):
        raise SeedWebRefused("The default content left an email template unset.")
    status = save_sections(
        request, service, attempt_id, updates=updates, expected_version=status.version
    )
    initial = updates["email_initial"]
    start, _ = campaign_dates
    status = save_section(
        request,
        service,
        attempt_id,
        step="schedules",
        values={
            "records": [
                {
                    "id": str(uuid4()),
                    "values": {
                        "campaign_id": str(attempt_id),
                        "kind": "initial",
                        "date": (start + timedelta(days=1)).isoformat(),
                        "time": "10:00:00",
                        "weekday": None,
                        "subject": initial["values"]["subject"],
                        "template_version": initial["id"],
                    },
                }
            ]
        },
        expected_version=status.version,
    )
    bundle = stage_logo(request, service, attempt_id)
    return save_section(
        request,
        service,
        attempt_id,
        step="branding",
        values={"bundle_id": str(bundle)},
        expected_version=status.version,
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
    """Review, send the sample mail, wait for the catcher to take it, confirm."""
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
