"""Suspended-seed review decisions as previewed, confirmed, installed requests."""

import re
from html import unescape
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.chair_models import ChairAssignmentReview
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.policy_models import MinistryAssignment
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.deployment import ServiceRole

from ..test_source_corpus import source
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_chair_reconciliation_postgresql import configured
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_current_chair_postgresql import publish
from .test_policy_postgresql import user
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_user_views_postgresql import URL as PAGE
from .test_user_views_postgresql import row

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/users/reviews"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def post(browser, values):
    """Use the genuine CSRF cookie."""
    return browser.post(
        URL, values | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
    )


def token(response):
    """Confirm only server-rendered signed intent."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def decision(store, **values):
    """A preview submission against the applied digest, as the page's forms send it."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "identity": "valid@example.org",
    } | values


def decided(store, browser, values):
    """Preview and confirm as the web role; install under the restricted installer."""
    with web():
        signed = token(post(browser, values))
        response = post(browser, {"action": "confirm", "preview": signed})
    assert response.status_code == 302, response.content
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    with as_config_installer():
        admit_configuration_database()
        receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "applied", receipt
    return request


def suspended(tmp_path, settings, real_limiter):
    """A confirmed seed the promoted source then stops showing: one open review."""
    from parishkit.stewardship.accounts.authentication import AuthRuntime

    store, seed, _ = configured(tmp_path, manual_scope=False)
    data = source()
    data.members[3]["emailAddress"] = ""
    publish(data)
    assert ChairAssignmentReview.objects.filter(closed_by__isnull=True).count() == 1
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(store, real_limiter, lambda: True)
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "OAUTH_PKCE_ENABLED": True,
            "APPS": [
                {
                    "client_id": "synthetic-client",
                    "secret": "synthetic-secret",
                    "key": "",
                }
            ],
        },
    }
    return store, seed


def review_row(body):
    """The suspended-review row for the seeded address."""
    section = body[body.index('id="chair-reviews"') :]
    return row(section[: section.index('id="chair-suggestions"')], "valid@example.org")


def address_row(body):
    """The exact-address rule row, which precedes the review table."""
    end = body.find('id="chair-reviews"')
    return row(body if end < 0 else body[:end], "valid@example.org")


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_a_suspended_seed_is_listed_and_restored_as_an_administrator_entry(
    tmp_path, settings, real_limiter, google
):
    """The row states the suspension; a restore keeps scope and closes the review."""
    store, seed = suspended(tmp_path, settings, real_limiter)
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        body = browser.get(PAGE).content.decode()
    listed = review_row(body)
    assert "Choir (Ministry DUID 4)" in listed and "DUID 3" in listed
    assert "no longer shows this Member" in listed
    assert "not a current Chairperson of any active Ministry" in listed
    assert 'name="decision" value="restore"' in listed
    with web():
        # A restore or removal without a reason is not understood, and a
        # reason carrying an address is refused before anything is signed.
        refused = post(browser, decision(store, decision="restore", ministry_duid=4))
        assert refused.status_code == 400
        personal = post(
            browser,
            decision(
                store, decision="restore", ministry_duid=4, reason="ask c@example.org"
            ),
        )
        assert personal.status_code == 400
        # A promotion between preview and confirmation makes the review stale:
        # the episodes it rests on are the source's, not the policy's.
        signed = token(
            post(
                browser,
                decision(store, decision="restore", ministry_duid=4, reason="Stale"),
            )
        )
    data = source()
    data.members[3]["emailAddress"] = ""
    publish(data)
    with web():
        stale = post(browser, {"action": "confirm", "preview": signed})
        assert stale.status_code == 409
    assert not AuditContext.objects.filter(
        event__event_type="chair_review_decided"
    ).exists()
    request = decided(
        store,
        browser,
        decision(store, decision="restore", ministry_duid=4, reason="Chairs by name"),
    )
    assignments = list(
        MinistryAssignment.objects.filter(
            configuration_id=store.active().version_id, email="valid@example.org"
        ).values_list("ministry_duid", "source")
    )
    assert assignments == [(4, "manual")]
    # The fixture's own history holds an earlier, returned episode; the one
    # this suspension opened closes as removed when the request activates.
    review = ChairAssignmentReview.objects.filter(
        assignment_record_id=seed.record_id
    ).latest("created_at")
    assert review.closed_by_id is not None
    assert review.close_reason == "assignment_removed"
    assert not ChairAssignmentReview.objects.filter(closed_by__isnull=True).exists()
    account = user("valid@example.org")
    principal = current_principal(store, account.pk)
    assert "ministry_leader" in principal.roles and principal.ministries == {4}
    (context,) = AuditContext.objects.filter(
        event__event_type="chair_review_decided"
    ).values_list("context", flat=True)
    assert context["decision"] == "restore" and context["ministry_duid"] == 4
    assert context["review_reason"] == "Chairs by name"
    assert "valid@example.org" not in str(context)
    assert (
        AuditContext.objects.get(
            event__event_type="chair_review_decided"
        ).event.subject_id
        == request.pk
    )
    with web():
        assert 'id="chair-reviews"' not in browser.get(PAGE).content.decode()


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_a_removed_seed_leaves_the_rule_and_closes_the_review(
    tmp_path, settings, real_limiter, google
):
    """Removal drops only the seed; the seeded role then reads as suspended."""
    store, seed = suspended(tmp_path, settings, real_limiter)
    browser, login = signed_in()
    assert login.status_code == 302
    decided(
        store,
        browser,
        decision(store, decision="remove", ministry_duid=4, reason="Left the parish"),
    )
    assert not MinistryAssignment.objects.filter(
        configuration_id=store.active().version_id, email="valid@example.org"
    ).exists()
    review = ChairAssignmentReview.objects.filter(
        assignment_record_id=seed.record_id
    ).latest("created_at")
    assert review.close_reason == "assignment_removed"
    assert not ChairAssignmentReview.objects.filter(closed_by__isnull=True).exists()
    with web():
        body = browser.get(PAGE).content.decode()
    assert 'id="chair-reviews"' not in body
    assert "The Ministry leader role is suspended" in row(body, "valid@example.org")
    with web():
        # Deciding again about a seed that no longer exists is refused.
        again = post(
            browser,
            decision(store, decision="remove", ministry_duid=4, reason="Again"),
        )
        assert again.status_code == 400
        assert "no longer exists" in again.content.decode()


def test_the_sql_context_guard_holds_the_decision_fields_to_the_same_rule():
    """Independent SQL prevents raw inserts from evading the closed Python schema."""
    import json

    from django.db import connection

    from ..test_chair_review import AUDIT_DECISION_CASES

    with connection.cursor() as cursor:
        for context, valid in AUDIT_DECISION_CASES:
            cursor.execute(
                "SELECT stewardship_safe_context_v1('action', %s::jsonb)",
                [json.dumps(context)],
            )
            assert cursor.fetchone()[0] is valid, context
        cursor.execute(
            "SELECT stewardship_safe_context_v1('email', %s::jsonb)",
            [json.dumps({"review_reason": "x"})],
        )
        assert cursor.fetchone()[0] is False


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_an_active_seed_cannot_be_restored_or_removed_here(
    tmp_path, settings, real_limiter, google
):
    """The decisions belong to the suspended list; a confirmed seed is refused."""
    from parishkit.stewardship.accounts.authentication import AuthRuntime

    store, _, _ = configured(tmp_path, manual_scope=False)
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(store, real_limiter, lambda: True)
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "OAUTH_PKCE_ENABLED": True,
            "APPS": [
                {
                    "client_id": "synthetic-client",
                    "secret": "synthetic-secret",
                    "key": "",
                }
            ],
        },
    }
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        assert 'id="chair-reviews"' not in browser.get(PAGE).content.decode()
        for name in ("restore", "remove"):
            refused = post(
                browser, decision(store, decision=name, ministry_duid=4, reason="x")
            )
            assert refused.status_code == 400
            assert "is not suspended" in refused.content.decode()
    assert not AuditContext.objects.filter(
        event__event_type="chair_review_decided"
    ).exists()


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_a_member_chairing_another_ministry_is_said_so(
    tmp_path, settings, real_limiter, google
):
    """Elsewhere means another active Ministry, never the one under review."""
    from copy import deepcopy

    store, _ = suspended(tmp_path, settings, real_limiter)
    # The Member returns with a valid address but chairs Ushers, not Choir.
    data = source()
    data.ministry_types[9] = {"id": 9, "name": "Ushers"}
    data.ministry_type_memberships[9] = deepcopy(data.ministry_type_memberships[4])
    data.ministry_type_memberships[4]["membership"] = []
    publish(data)
    assert ChairAssignmentReview.objects.filter(closed_by__isnull=True).count() == 1
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        body = browser.get(PAGE).content.decode()
    assert "a current Chairperson of another active Ministry" in review_row(body)
    assert store.active() is not None


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_keeping_the_role_independently_survives_the_source(
    tmp_path, settings, real_limiter, google
):
    """A manual origin beside the seed keeps the role after the Chairperson goes."""
    store, seed = suspended(tmp_path, settings, real_limiter)
    account = user("valid@example.org")
    assert "ministry_leader" not in current_principal(store, account.pk).roles
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        body = browser.get(PAGE).content.decode()
    assert 'name="decision" value="keep_role"' in address_row(body)
    decided(store, browser, decision(store, decision="keep_role"))
    principal = current_principal(store, account.pk)
    assert "ministry_leader" in principal.roles and principal.ministries == frozenset()
    with web():
        body = browser.get(PAGE).content.decode()
    kept = address_row(body)
    assert "Ministry leader (Administrator entry; Parish source Chairperson)" in kept
    assert 'name="decision" value="keep_role"' not in kept
    # The seed itself is still suspended and still awaiting review.
    assert ChairAssignmentReview.objects.filter(closed_by__isnull=True).count() == 1
    with web():
        again = post(browser, decision(store, decision="keep_role"))
        assert again.status_code == 400
        assert "already" in again.content.decode()
