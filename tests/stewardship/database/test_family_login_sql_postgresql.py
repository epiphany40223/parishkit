"""SQL creates Family sessions only after proving a credential (#306 M3).

Web has no INSERT on the Family session table. The SECURITY DEFINER login
function re-proves the presented code MACs or link token against the stored
digests under the Family admission rules, and creates the row itself.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.db.models import F

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.credential_database import admit_grants
from parishkit.stewardship.accounts.family_authentication import _mint_session
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.sessions import FAMILY_ABSOLUTE, database_now
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    FamilySession,
    RehearsalCredential,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import runtime_functions, runtime_grants

from .auth_builders import unguarded
from .test_family_auth_postgresql import family_service, login  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def fresh_key():
    """A new live Django session, as the sign-in saves before minting."""
    store = SessionStore()
    store.save()
    return store.session_key


def mint(**credential):
    """Call the login function in its own transaction; return the row id."""
    family = credential.pop("family")
    with transaction.atomic():
        return _mint_session(fresh_key(), family, **credential)


def test_web_cannot_insert_a_family_session_directly(family_service):  # noqa: F811
    """Only the definer function writes the table; web's INSERT is gone."""
    family = RehearsalCredential.objects.get().family_id
    tables, _ = runtime_grants(ServiceRole.WEB)
    assert "INSERT" not in tables["stewardship_family_session"]
    with web_login():
        key = fresh_key()
        with (
            pytest.raises(ProgrammingError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO stewardship_family_session(id,correlation_id,version,"
                "mode,authenticated_at,last_activity_at,expires_at,family_id,"
                "session_id,rehearsal_epoch_id,credential_epoch,presence_section) "
                "SELECT %s,%s,1,'testing',now(),now(),now()+interval '1 hour',"
                "%s,%s,k.rehearsal_epoch_id,d.family_link_epoch,'' "
                "FROM stewardship_campaign_credentials k "
                "CROSS JOIN stewardship_credential_deployment d",
                [uuid4(), uuid4(), family, key],
            )
    assert not FamilySession.objects.exists()


def test_login_function_requires_a_matching_credential(family_service):  # noqa: F811
    """Wrong, foreign or malformed credentials mint nothing; valid ones do."""
    credential = RehearsalCredential.objects.get()
    family = credential.family_id
    # Another Family's id: an eligible one when the fixture has one.
    other = (
        FamilyCampaign.objects.exclude(pk=family).values_list("pk", flat=True).first()
        or uuid4()
    )
    digests = family_service.rings.mac.lookups(
        family_service.campaign.pk, family_service.code
    )
    token = family_service.token
    forged = "test." + ("A" if token[5] != "A" else "B") + token[6:]
    with web_login():
        refused = [
            dict(family=family, digests={key: "0" * 64 for key in digests}),
            dict(family=other, digests=digests),
            dict(family=family, digests={}),
            dict(family=family, token=forged),
            dict(family=other, token=token),
            dict(family=family, token=token[5:]),
            dict(family=family, token="not a token"),
            dict(family=family, digests=digests, token=token),
            dict(family=family),
        ]
        for values in refused:
            assert mint(**values) is None, values
        assert not FamilySession.objects.exists()
        by_code = mint(family=family, digests=digests)
        by_link = mint(family=family, token=token)
    assert by_code is not None and by_link is not None and by_code != by_link
    rows = FamilySession.objects.filter(pk__in=[by_code, by_link])
    assert {row.family_id for row in rows} == {family}
    assert {row.mode for row in rows} == {"testing"}
    assert all(row.rehearsal_epoch_id == credential.epoch_id for row in rows)
    assert all(row.expires_at - row.authenticated_at == FAMILY_ABSOLUTE for row in rows)


def test_login_function_rechecks_admission_and_session_binding(
    family_service,  # noqa: F811
):
    """A dead or Admin session key, or a dirty population, refuse in SQL too."""
    family = RehearsalCredential.objects.get().family_id
    token = family_service.token
    with transaction.atomic():
        assert _mint_session("x" * 32, family, token=token) is None
    # A Django session that already carries Admin session metadata.
    admin_key = fresh_key()
    now = database_now()
    with unguarded():
        PortalSession.objects.create(
            session_id=admin_key,
            principal_id=uuid4(),
            authenticated_at=now,
            last_activity_at=now,
            expires_at=now + timedelta(hours=1),
        )
    with transaction.atomic():
        assert _mint_session(admin_key, family, token=token) is None
    CampaignCredentialState.objects.update(
        population_dirty=True, version=F("version") + 1
    )
    assert mint(family=family, token=token) is None
    assert not FamilySession.objects.exists()


def test_a_sql_refusal_is_the_uniform_denial(family_service, monkeypatch):  # noqa: F811
    """If SQL refuses after Python admitted, the whole sign-in rolls back."""
    from django.contrib.sessions.models import Session

    from parishkit.stewardship.accounts import family_authentication
    from parishkit.stewardship.audit.models import AuditEvent

    client, response = login(family_service.code)
    assert response.status_code == 302
    before = client.cookies["pk_family"].value
    sessions = Session.objects.count()
    monkeypatch.setattr(family_authentication, "_mint_session", lambda *a, **k: None)
    assert client.get("/access/" + family_service.token).status_code == 403
    _, response = login(family_service.code, client)
    assert response.status_code == 403
    assert Session.objects.count() == sessions
    assert FamilySession.objects.count() == 1
    assert AuditEvent.objects.filter(event_type="family_login").count() == 1
    # The browser keeps the session it had.
    assert client.cookies["pk_family"].value == before
    assert client.get("/family/").status_code == 200


def test_web_admission_requires_exactly_the_login_function(family_service):  # noqa: F811
    """Startup admission demands web's EXECUTE and refuses any other definer."""
    functions = runtime_functions(ServiceRole.WEB)
    # The worker's definer routines are the automation maintenance task's
    # session purge (ADM-11) and the export recovery's read-guard count
    # (#386); it never holds the Family login function.
    assert runtime_functions(ServiceRole.WORKER) == {
        "stewardship_admin_session_purge_v1(uuid[])",
        "stewardship_read_guard_kills_v1(uuid)",
        "stewardship_task_event_prune_v1(integer, integer, integer)",
    }
    tables, columns = runtime_grants(ServiceRole.WEB)
    allowed = {table: set(grants) for table, grants in tables.items()}
    for table, grants in columns.items():
        allowed.setdefault(table, set()).update(grants)
    with web_login():
        admit_grants(allowed, functions=functions)
        with pytest.raises(ConfigError, match="excessive"):
            admit_grants(allowed)
    with connection.cursor() as cursor:
        cursor.execute("CREATE ROLE pk_family_login_probe LOGIN NOINHERIT")
        cursor.execute("SET SESSION AUTHORIZATION pk_family_login_probe")
    try:
        with pytest.raises(ConfigError, match="missing"):
            admit_grants({}, functions=functions)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute("DROP ROLE pk_family_login_probe")


def test_family_activity_cannot_be_recorded_in_the_future(family_service):  # noqa: F811
    """Idle renewal stops at the statement clock, as for Admin sessions."""
    assert login(family_service.code)[1].status_code == 302
    row = FamilySession.objects.get()
    with pytest.raises(IntegrityError, match="future"), transaction.atomic():
        FamilySession.objects.filter(pk=row.pk).update(
            last_activity_at=database_now() + timedelta(minutes=30),
            version=F("version") + 1,
        )


def test_an_already_bound_session_key_is_refused(family_service):  # noqa: F811
    """A Django session that already has a Family session gets no second row."""
    family = RehearsalCredential.objects.get().family_id
    key = fresh_key()
    with transaction.atomic():
        assert _mint_session(key, family, token=family_service.token) is not None
    with transaction.atomic():
        assert _mint_session(key, family, token=family_service.token) is None
    assert FamilySession.objects.count() == 1
