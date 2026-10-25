"""A setup draft whose access step still grants a domain says how to fix it (#993).

A deployment bootstrapped before #993 may be part-way through setup with an
access step the earlier release saved, including a Staff hosted domain.
Domains no longer grant sign-in, so Review and Finish refuse that draft. The
refusal names the access step and links to it, rather than the generic "this
page address is not valid" text, and saving the step again clears it.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import json
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test import Client

from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_installation import (
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.accounts.setup_drafts import save_section

from ..configuration_factory import configuration_version
from ..policy_factory import legacy_bootstrap_document
from ..test_setup_forms import VALUES
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_preview_postgresql import complete_draft
from .test_setup_views_postgresql import setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def bootstrapped(tmp_path):
    """A deployment bootstrapped before #993: one exact-address sign-in rule."""
    deployment, actor = uuid4(), uuid4()
    store = AuthorityStore(tmp_path, validate_sections)
    root = configuration_version(legacy_bootstrap_document())
    prepare_initial_configuration(
        store,
        root,
        deployment_id=deployment,
        testing_recipient="admin@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    return store, root, deployment, actor


def test_review_and_finish_name_the_access_step_saved_with_a_domain(
    setup_http, monkeypatch, tmp_path, settings
):
    """Preview and Finish show the specific refusal and link to the access step."""
    request, status, media = complete_draft(setup_http, monkeypatch, tmp_path)
    settings.STEWARDSHIP_MEDIA_ROOT = media
    # The earlier release saved the access step with its domain lists. Since
    # #993 neither the code nor the draft guard trigger admits one, but the
    # migration leaves a saved row as it was; recreate that row with the
    # triggers off, as the test database's owner.
    legacy = VALUES["access"] | {
        "staff_domains": ["example.org"],
        "ministry_domains": [],
    }
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role=replica")
        cursor.execute(
            "UPDATE stewardship_setup_draft_section SET values=%s "
            "WHERE attempt_id=%s AND step='access'",
            [json.dumps(legacy), status.attempt_id],
        )
        assert cursor.rowcount == 1
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    with web_login():
        response = browser.get("/admin/setup/confirm")
        assert response.status_code == 400, response.content
        page = response.content.decode()
        assert "Domains no longer grant access." in page
        assert "save the step again" in page
        assert '<a href="/admin/setup/access">' in page
        assert "This page address is not valid" not in page
        # Saving the access step again, with addresses only, clears it.
        save_section(
            request,
            setup_http,
            status.attempt_id,
            step="access",
            values=VALUES["access"],
            # The direct row write above did not change the draft's version.
            expected_version=status.version,
        )
        assert browser.get("/admin/setup/confirm").status_code == 200
    assert not setup_http.configured()
