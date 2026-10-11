"""A fresh install finishes setup in the users form, with no upgrade-users (#993).

``bootstrap --admin-email`` writes a users bootstrap (``bootstrap-users-v10``)
whose one record is the initial Administrator user, and the setup wizard's
``initial-setup-users-v10`` request over it activates a ``users-v10``
configuration. So the services' users-form startup check passes from the
first boot, the Administrator signs in before, during and after setup, and
offline Admin-access recovery during setup adds an Administrator user (D2).
"""

# ruff: noqa: F811 -- imported pytest fixtures.

import re
from contextlib import nullcontext
from functools import partial
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.accounts.operator_recovery import recover_admin
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import AddressRule
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import (
    authenticated_admin,
    database_now,
    issue_admin,
)
from parishkit.stewardship.accounts.setup_install_models import SetupCompletion
from parishkit.stewardship.accounts.setup_staging import begin_setup
from parishkit.stewardship.accounts.users_schema import require_users_form
from parishkit.stewardship.source.setup_completion import complete_setup

from .credential_builders import keys
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import config_role  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_final_loading_postgresql import load, prepared, queued
from .test_setup_loading_postgresql import pages
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


def runtime():
    """The deployment's runtime row with its active configuration."""
    return SystemConfiguration.objects.select_related("active_configuration").get()


def sign_in(service, email, subject):
    """Sign one address in as an Admin and return its policy actor.

    The same steps as a verified Google sign-in: the PortalUser insert and
    its verification share one transaction (#389), then the Admin session
    insert passes SQL's session admission, which reads only users (#952).
    """
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    with transaction.atomic():
        user, _ = PortalUser.objects.get_or_create(
            google_subject=subject,
            defaults={"email": email, "verified_at": database_now()},
        )
    request = RequestFactory().post("/admin/setup")
    request.session = SessionStore()
    issue_admin(request, user.pk, store=service.store, authenticated_at=database_now())
    return request, authenticated_admin(request, store=service.store)


def export_authorized(email):
    """SQL's own Administrator test for this address's portal user."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_export_authorized_v1(id, true) "
            "FROM stewardship_portal_user WHERE email=%s",
            [email],
        )
        return cursor.fetchone()[0]


def test_a_fresh_install_finishes_setup_with_an_administrator_user(
    setup_service, monkeypatch, tmp_path, config_role
):
    """Bootstrap, then the setup wizard, then start: no upgrade-users in between."""
    bootstrap = runtime()
    assert bootstrap.active_configuration.validation_schema == "bootstrap-users-v10"
    # Startup admits the users bootstrap itself, not only an exemption.
    require_users_form(bootstrap)
    with web_login():
        _, actor = sign_in(setup_service, "admin@example.org", "synthetic-setup-admin")
    assert allows(actor, Capability.CONFIGURE)

    _, attempt, identifier = prepared(setup_service, monkeypatch, tmp_path)
    ring = keys()
    task = queued(setup_service, identifier)
    fake_provider(monkeypatch, pages())
    finalize = partial(
        complete_setup,
        store=setup_service.store,
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
    )
    completed = load(
        setup_service,
        task,
        tmp_path / "parishsoft" / "credential",
        finalize=lambda execution, claim, snapshot: finalize(
            execution, claim, snapshot.pk
        ),
    )
    assert SetupCompletion.objects.get() == completed

    configured = runtime()
    assert configured.active_configuration_id == completed.activation.configuration_id
    assert configured.active_configuration.validation_schema == "users-v10"
    assert configured.current_campaign_id == attempt.attempt_id
    # The setup request was the users format, and nothing converted anything.
    assert set(
        ConfigurationChangeRequest.objects.values_list("request_schema", flat=True)
    ) == {"initial-setup-users-v10"}
    # Web, workers and scheduler start: the configuration is in the users form.
    require_users_form(configured)
    users = {
        rule.email: (rule.name, rule.role, rule.roles)
        for rule in AddressRule.objects.filter(
            configuration=configured.active_configuration
        )
    }
    assert users["admin@example.org"] == (
        "admin",
        "administrator",
        ["administrator", "ministry_leader", "staff"],
    )
    # Each address the access step listed is one user with its highest role.
    assert users["another@example.net"][1] == "administrator"
    assert users["staff@example.net"][1] == "staff"
    assert users["chair@example.net"][1] == "ministry_leader"
    assert not AddressRule.objects.filter(
        configuration=configured.active_configuration, role__isnull=True
    ).exists()

    # The first Administrator signs in again after setup, through the SQL
    # that reads only users, and keeps every Administrator capability.
    with web_login():
        _, actor = sign_in(setup_service, "admin@example.org", "synthetic-setup-admin")
        assert allows(actor, Capability.CONFIGURE)
        assert export_authorized("admin@example.org")


def test_recovery_during_setup_adds_an_administrator_user(setup_service):
    """A lost first Administrator is recovered before setup finishes (D2).

    Recovery keeps the users bootstrap, so startup still passes, the new
    Administrator signs in, and that Administrator can start setup.
    """
    deployment = runtime().pk
    status = recover_admin(
        setup_service.store,
        operation_id=uuid4(),
        operator_name="Test operator",
        reason="The first Administrator lost their account",
        deployment_id=deployment,
        target_email="Rescue@Example.org",
        confirmed_email="rescue@example.org",
        correlation_id=uuid4(),
        offline_interlock=nullcontext,  # Internal fixture; CLI tests use real flock.
        name="Rescue Admin",
    )
    assert status.state == "applied"
    recovered = runtime()
    assert recovered.active_configuration.validation_schema == "bootstrap-users-v10"
    require_users_form(recovered)
    assert set(
        AddressRule.objects.filter(
            configuration=recovered.active_configuration
        ).values_list("email", "name", "role")
    ) == {
        ("admin@example.org", "admin", "administrator"),
        ("rescue@example.org", "Rescue Admin", "administrator"),
    }
    assert ConfigurationChangeRequest.objects.get().request_schema == (
        "operator-recovery-users-v10"
    )
    with web_login():
        request, actor = sign_in(setup_service, "rescue@example.org", "rescue-subject")
        assert allows(actor, Capability.CONFIGURE)
        assert begin_setup(request, setup_service).state == "collecting"


MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
SETUP_USERS = (MIGRATIONS / "migrations/0055_setup_users.sql").read_text(
    encoding="utf-8"
)
CHECK = SETUP_USERS[SETUP_USERS.index("DO $check$") :]
# Every function 0055 replaces, with the file holding its body before 0055.
EARLIER = {
    "stewardship_policy_complete_v1": "migrations/0047_portal_users.sql",
    "stewardship_policy_projection_v1": "migrations/0047_portal_users.sql",
    "stewardship_users_form_v1": "migrations/0047_portal_users.sql",
    "stewardship_refresh_slot_due_v1": "migrations/0014_slot_decisions.sql",
}
FIXTURE = Path(__file__).with_name("pre_setup_users_functions.sql")
REPLACED = sorted(
    {
        *EARLIER,
        *re.findall(
            r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(",
            FIXTURE.read_text(encoding="utf-8"),
            re.M,
        ),
    }
)


def earlier_definition(name):
    """The function's real body before 0055, as CREATE OR REPLACE."""
    source = (
        (MIGRATIONS / EARLIER[name]).read_text(encoding="utf-8")
        if name in EARLIER
        else FIXTURE.read_text(encoding="utf-8")
    )
    start = re.search(
        rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", source, re.M
    ).start()
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(source, start)
    end = source.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    return "CREATE OR REPLACE FUNCTION" + re.sub(
        r"^CREATE (?:OR REPLACE )?FUNCTION", "", source[start:end], count=1
    )


def check_refuses(statement, message):
    """Run ``statement`` and then 0055's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    assert len(REPLACED) == 21
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)


@pytest.mark.parametrize("name", REPLACED)
def test_the_closing_check_refuses_each_earlier_definition(name):
    """With any one function still on its pre-0055 body, the migration refuses."""
    earlier = earlier_definition(name)
    assert "users-v10" not in earlier.replace("'users-v10'", "")
    check_refuses(earlier, "a replaced function is not installed as declared")


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER FUNCTION public.stewardship_bootstrap_empty_database() SECURITY INVOKER",
        "GRANT EXECUTE ON FUNCTION public.stewardship_bootstrap_empty_database() "
        "TO PUBLIC",
        "ALTER FUNCTION public.stewardship_setup_request_binding_v1() SECURITY DEFINER",
    ],
)
def test_the_closing_check_refuses_a_changed_definer(statement):
    """Only the empty-database guard runs as its owner, and PUBLIC cannot call it."""
    check_refuses(statement, "lost or gained SECURITY DEFINER")


def test_the_closing_check_refuses_the_earlier_constraints():
    """Both CHECK constraints must admit the new names."""
    earlier = (MIGRATIONS / "migrations/0047_portal_users.sql").read_text(
        encoding="utf-8"
    )
    [constraint] = [
        line
        for line in earlier.splitlines()
        if 'ADD CONSTRAINT "configuration_validation_schema"' in line
    ]
    check_refuses(
        'ALTER TABLE "stewardship_configuration_version" '
        'DROP CONSTRAINT "configuration_validation_schema";' + constraint,
        "users bootstrap CHECKs are not installed as declared",
    )
