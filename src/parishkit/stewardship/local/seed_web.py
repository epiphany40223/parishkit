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
"""

import logging
from dataclasses import dataclass
from importlib import import_module
from ipaddress import ip_address
from uuid import UUID, uuid4

from parishkit.config import ConfigError

from .seed_timeline import PLEDGE_AMOUNTS

LOGGER = logging.getLogger(__name__)
# The seeder acts from the VM's loopback: the limiter keys its budgets by
# source address, and Family logins share one; the family_ip allowance (100
# in 10 minutes) covers the default parish, and each Family's login runs
# minutes apart in real time anyway.
SEEDER_ADDRESS = "127.0.0.1"
LOCAL_HOST = "localhost:8443"


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
