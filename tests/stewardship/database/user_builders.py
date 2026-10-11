"""Shared builders for the Users page's PostgreSQL tests (#952).

- ``web`` runs browser requests under the real restricted web login.
- ``add_rules`` installs login records through the real configuration owner.
- ``identity`` and ``signed_in_before`` record Google identities.
- ``convert`` turns a deployment's sign-in rules into users through the real
  offline upgrade, keeping every Administrator.
- ``user_change`` reviews one New or Edit user change through the page and
  applies it, then installs it as the configuration installer does.
"""

import re
from html import unescape
from uuid import uuid4

from parishkit.stewardship.accounts import users_upgrade
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from .campaign_builders import change
from .role_grants import seed_user
from .test_background_grants_postgresql import task_login
from .test_users_upgrade_postgresql import arguments as upgrade_arguments

USERS = "/admin/users/"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def add_rules(store, *records):
    """Install login records through the real configuration owner, before sign-in.

    Records come first so every sign-in in a case is decided by the final
    policy; a session that outlives a policy change is refused by the
    reload, not ended.
    """
    receipt = change(
        store,
        store.active(),
        uuid4(),
        [
            {"operation": "add", "section": "login_rules", **record}
            for record in records
        ],
    )
    assert receipt.state == "applied"


def identity(email, *, hosted=None, disabled=False):
    """A verified Google identity that never opened a session."""
    return seed_user(
        google_subject=str(uuid4()),
        email=email,
        hosted_domain=hosted,
        disabled=disabled,
    )


def signed_in_before(email, *, hosted):
    """A Google identity that signed in to the Admin portal before the users swap.

    Sign-in reads only users since #952 slice 4, so a hosted-domain
    colleague's earlier sign-in is recorded as that sign-in left it: the
    identity and its admin_login audit event.
    """
    portal = identity(email, hosted=hosted)
    AuditEvent.objects.create(event_type="admin_login", actor_id=portal.pk)
    return portal


def convert(store):
    """Convert the active sign-in rules to users, keeping every Administrator."""
    administrators = sorted(
        record["values"]["email"]
        for record in store.active().document()["sections"]["login_rules"]
        if record["values"]["kind"] == "address"
        and "administrator" in record["values"]["roles"]
    )
    receipt = users_upgrade.upgrade_users(store, **upgrade_arguments(administrators))
    assert receipt.state == "applied"


def users(store):
    """The active users as {email: (name, role)}."""
    return {
        record["values"]["email"]: (record["values"]["name"], record["values"]["role"])
        for record in store.active().document()["sections"]["login_rules"]
    }


def user_id(store, email):
    """The active users-list record ID of one address."""
    return next(
        record["id"]
        for record in store.active().document()["sections"]["login_rules"]
        if record["values"]["email"] == email
    )


def post(browser, path, values):
    """Post with the genuine Admin CSRF cookie."""
    return browser.post(
        path, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def review(store, browser, path, values):
    """Post New or Edit user's Review as the page's form does."""
    with web():
        return post(
            browser,
            path,
            {"action": "preview", "base_digest": store.active().digest} | values,
        )


def token(response):
    """The signed review the page rendered, never a fabricated patch."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def installed(store, response):
    """Install the change a 302 to Change status names; return its request."""
    assert response.status_code == 302, response.content
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "applied"
    return request


def user_change(store, browser, path, values):
    """Review and Apply one user change through the page; install it."""
    signed = token(review(store, browser, path, values))
    with web():
        response = post(browser, USERS, {"action": "confirm", "preview": signed})
    return installed(store, response)
