"""Login-rule edits as previewed, confirmed, installed requests under real roles."""

import re
from html import unescape
from uuid import uuid4

import psycopg
import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone

from parishkit.stewardship.accounts import configuration_installation as installer
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import _status
from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.request_models import (
    ConfigurationChangeRequest,
    ConfigurationRequestCheckpoint,
)
from parishkit.stewardship.accounts.runtime_models import ConfigurationActivation
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_process import next_configuration_request
from parishkit.stewardship.storage import StorageInvariantError

from ..policy_factory import address, assignment, domain
from .auth_builders import auth_runtime, signed_in
from .test_background_grants_postgresql import task_login
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_user_views_postgresql import add_rules, row

pytestmark = pytest.mark.django_db(transaction=True)
PAGE = "/admin/users"
URL = "/admin/users/rules"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def post(browser, values):
    """Use the genuine CSRF cookie; roles may repeat, as ticked boxes do."""
    return browser.post(
        URL, values | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
    )


def proposal(store, **values):
    """A preview submission against the applied digest, as the page's forms send it."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "operation": "set",
        "roles": [],
    } | values


def token(response):
    """Confirm only server-rendered signed intent rather than a fabricated patch."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def applied(store, browser, values):
    """Preview and confirm as the web role; install as the installer owner."""
    with web():
        signed = token(post(browser, values))
        response = post(browser, {"action": "confirm", "preview": signed})
    assert response.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "applied"
    return request


def page(browser):
    """The Portal users page as the Administrator sees it now."""
    with web():
        response = browser.get(PAGE)
    assert response.status_code == 200
    return response.content.decode()


def rules(store):
    """The applied login rules by target."""
    return {
        record["values"].get("email") or record["values"]["domain"]: record["values"]
        for record in store.active().document()["sections"]["login_rules"]
        # Assignments share an address's email; only rules carry roles.
        if record["values"]["kind"] != "assignment"
    }


def test_rules_are_added_changed_and_removed_through_reviewed_requests(
    auth_service, google
):
    """Each change is one installed request; provenance names that request."""
    store = auth_service.store
    add_rules(store, domain("example.org", roles=("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    body = page(browser)
    assert store.active().digest in body and "Review role change" in body
    assert 'name="operation" value="remove"' in body
    # A new exact address, with the roles ticked in any order and case.
    request = applied(
        store,
        browser,
        proposal(
            store,
            kind="address",
            identity="New.Staff@Example.org",
            roles=["staff", "ministry_leader"],
        ),
    )
    created = rules(store)["new.staff@example.org"]
    assert created["roles"] == ["ministry_leader", "staff"]
    assert created["creation_origin"] == "manual"
    assert created["creation_operation"] == str(request.pk)
    assert created["grants"] == {
        "ministry_leader": {"manual": str(request.pk)},
        "staff": {"manual": str(request.pk)},
    }
    # Widening the domain rule keeps the existing role and adds one.
    applied(
        store,
        browser,
        proposal(
            store,
            kind="domain",
            identity="example.org",
            roles=["staff", "ministry_leader"],
        ),
    )
    assert rules(store)["example.org"]["roles"] == ["ministry_leader", "staff"]
    # Shrinking to an explicit deny drops the removed roles' origins.
    applied(
        store,
        browser,
        proposal(store, kind="address", identity="new.staff@example.org"),
    )
    denied = rules(store)["new.staff@example.org"]
    assert denied["roles"] == [] and denied["grants"] == {}
    assert "Explicit deny" in row(page(browser), "new.staff@example.org")
    # Removal is one request too, and the row is gone afterwards.
    applied(
        store,
        browser,
        proposal(
            store, kind="address", identity="new.staff@example.org", operation="remove"
        ),
    )
    assert "new.staff@example.org" not in rules(store)
    assert "new.staff@example.org" not in page(browser)
    # The Administrator who made every change is still one.
    assert rules(store)["admin@example.org"]["roles"] == ["administrator"]


def test_a_signed_review_confirmed_twice_is_one_request(auth_service, google):
    """A lost response and a resubmitted confirmation never make a second grant."""
    store = auth_service.store
    before = ConfigurationChangeRequest.objects.count()
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        signed = token(
            post(
                browser,
                proposal(
                    store, kind="address", identity="twice@example.org", roles=["staff"]
                ),
            )
        )
        first = post(browser, {"action": "confirm", "preview": signed})
        again = post(browser, {"action": "confirm", "preview": signed})
    assert first.status_code == again.status_code == 302
    assert first["Location"] == again["Location"]
    assert ConfigurationChangeRequest.objects.count() == before + 1
    request = ConfigurationChangeRequest.objects.get(
        pk=first["Location"].rsplit("/", 1)[-1]
    )
    # The provenance the patch carries is the one request the installer checks.
    receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "applied"
    assert rules(store)["twice@example.org"]["creation_operation"] == str(request.pk)


def test_activation_requires_the_confirming_administrator_still_to_be_one(
    auth_service, google, monkeypatch
):
    """A confirmed grant is not applied for an actor revoked since confirmation."""
    store = auth_service.store
    add_rules(store, address("second@example.org"))
    browser, login = signed_in()
    assert login.status_code == 302
    admin = PortalUser.objects.get(email="admin@example.org")
    with web():
        signed = token(
            post(
                browser,
                proposal(
                    store,
                    kind="address",
                    identity="grant@example.org",
                    roles=["administrator"],
                ),
            )
        )
        queued = post(browser, {"action": "confirm", "preview": signed})
    assert queued.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=queued["Location"].rsplit("/", 1)[-1]
    )
    # The digest is unchanged, but the actor's identity is disabled: the
    # installer refuses at activation, and the policy is untouched.
    PortalUser.objects.filter(pk=admin.pk).update(
        disabled=True, version=F("version") + 1
    )
    receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "failed" and receipt.failure_code == "actor_unauthorized"
    assert "grant@example.org" not in rules(store)
    # The same for an actor whose Administrator rule another Administrator
    # removed meanwhile: the base moved, so it is stale before it is anything.
    PortalUser.objects.filter(pk=admin.pk).update(
        disabled=False, version=F("version") + 1
    )
    with web():
        signed = token(
            post(
                browser,
                proposal(
                    store, kind="address", identity="later@example.org", roles=["staff"]
                ),
            )
        )
        queued = post(browser, {"action": "confirm", "preview": signed})
    assert queued.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=queued["Location"].rsplit("/", 1)[-1]
    )
    google[0].update(email="second@example.org", sub="second-subject")
    other, login = signed_in()
    assert login.status_code == 302
    applied(
        store,
        other,
        proposal(store, kind="address", identity="admin@example.org", roles=["staff"]),
    )
    receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "failed" and receipt.failure_code == "stale_base"
    assert "later@example.org" not in rules(store)
    # An identity disabled after the installer's first check but before the
    # activation is caught by the locked recheck inside that transaction: the
    # activation rolls back, the base YAML is selected again, and the request
    # fails with nothing applied.
    google[0].update(email="second@example.org", sub="second-subject")
    second = PortalUser.objects.get(email="second@example.org")
    with web():
        signed = token(
            post(
                other,
                proposal(
                    store, kind="address", identity="raced@example.org", roles=["staff"]
                ),
            )
        )
        queued = post(other, {"action": "confirm", "preview": signed})
    assert queued.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=queued["Location"].rsplit("/", 1)[-1]
    )
    base = store.active()
    original = installer.DatabaseMaterializer.activate

    def racing(self, digest):
        """Disable the confirming Administrator once the YAML is selected."""
        PortalUser.objects.filter(pk=second.pk).update(
            disabled=True, version=F("version") + 1
        )
        return original(self, digest)

    monkeypatch.setattr(installer.DatabaseMaterializer, "activate", racing)
    receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "failed" and receipt.failure_code == "actor_unauthorized"
    assert store.active() == base and "raced@example.org" not in rules(store)
    assert not ConfigurationActivation.objects.filter(request=request).exists()


@pytest.mark.usefixtures("config_role")
def test_a_refused_activation_is_recovered_and_recorded_under_real_roles(
    auth_service, google, monkeypatch
):
    """The real installer role applies and refuses; every crash window resumes."""
    store = auth_service.store
    add_rules(store, address("second@example.org"))
    browser, login = signed_in()
    assert login.status_code == 302
    admin = PortalUser.objects.get(email="admin@example.org")

    def queued(identity):
        """One confirmed rule change, still to be installed."""
        with web():
            signed = token(
                post(
                    browser,
                    proposal(store, kind="address", identity=identity, roles=["staff"]),
                )
            )
            response = post(browser, {"action": "confirm", "preview": signed})
        assert response.status_code == 302
        return ConfigurationChangeRequest.objects.get(
            pk=response["Location"].rsplit("/", 1)[-1]
        )

    def install(request):
        """Install as the restricted configuration installer, never the owner."""
        with as_config_installer():
            admit_configuration_database()
            return install_request(store, request_id=request.pk, correlation_id=uuid4())

    def set_disabled(value):
        """Flip the confirming Administrator's identity."""
        PortalUser.objects.filter(pk=admin.pk).update(
            disabled=value, version=F("version") + 1
        )

    # The plain activation-time read needs only the installer's SELECT grant.
    assert install(queued("plain@example.org")).state == "applied"
    assert "plain@example.org" in rules(store)
    # A crash after the candidate YAML is selected and before the activation
    # leaves the request at yaml_activated with the candidate selected. The
    # recovery pass runs the same actor recheck and, the actor disabled since,
    # refuses, restores the base and records the refusal.
    request, base = queued("crashed@example.org"), store.active()
    original = installer.DatabaseMaterializer.activate

    def crashing(self, digest):
        """Stop where a crash would, once the YAML is selected."""
        self.checkpoint("yaml_activated")
        raise StorageInvariantError("simulated crash before activation")

    monkeypatch.setattr(installer.DatabaseMaterializer, "activate", crashing)
    with pytest.raises(StorageInvariantError):
        install(request)
    monkeypatch.setattr(installer.DatabaseMaterializer, "activate", original)
    assert store.active().digest == request.candidate_digest
    set_disabled(True)
    receipt = install(request)
    assert receipt.state == "failed" and receipt.failure_code == "actor_unauthorized"
    assert store.active() == base and "crashed@example.org" not in rules(store)
    assert not ConfigurationActivation.objects.filter(request=request).exists()
    # The checkpoint trigger admits that refusal from yaml_activated alone: not
    # a stale base, and not the same code written by the web role.
    set_disabled(False)
    stranded, later = queued("stranded@example.org"), queued("later@example.org")
    monkeypatch.setattr(installer.DatabaseMaterializer, "activate", crashing)
    with pytest.raises(StorageInvariantError):
        install(stranded)
    monkeypatch.setattr(installer.DatabaseMaterializer, "activate", original)

    def record(code):
        """One more checkpoint after yaml_activated, as a caller might try."""
        ConfigurationRequestCheckpoint.objects.create(
            request=stranded,
            sequence=_status(stranded).sequence + 1,
            state="failed",
            failure_code=code,
            actor_id=stranded.actor_id,
            correlation_id=uuid4(),
        )

    refused = "Invalid configuration installer transition"
    with pytest.raises(IntegrityError, match=refused), transaction.atomic():
        record("stale_base")
    with web(), pytest.raises(IntegrityError, match=refused), transaction.atomic():
        record("actor_unauthorized")
    assert _status(stranded).state == "yaml_activated"
    # The refusal is committed by the activation transaction itself, before the
    # base YAML is restored. A crash between the two leaves the refused
    # request failed with its candidate still selected; the actor authorized
    # again by then changes nothing, since the refusal is already recorded.
    restore = installer._restore_refused

    def crashed_before_restore(request):
        """Refuse the request and stop where a crash would, the refusal durable."""
        set_disabled(True)

        def crashing_restore(store, check):
            if _status(request).state == "failed":
                raise StorageInvariantError("simulated crash before the restore")

        monkeypatch.setattr(installer, "_restore_refused", crashing_restore)
        with pytest.raises(StorageInvariantError):
            install(request)
        monkeypatch.setattr(installer, "_restore_refused", restore)
        set_disabled(False)
        refusal = _status(request)
        assert refusal.state == "failed"
        assert refusal.failure_code == "actor_unauthorized"
        assert store.active().digest == request.candidate_digest

    # A request queued before the crash restores the base first, then applies.
    crashed_before_restore(stranded)
    assert install(later).state == "applied"
    assert "later@example.org" in rules(store)
    assert "stranded@example.org" not in rules(store)
    assert install(stranded).failure_code == "actor_unauthorized"
    assert not ConfigurationActivation.objects.filter(request=stranded).exists()
    # With the queue empty, the terminal request never selected again and the
    # web refusing every page while file and database disagree, the
    # installer's idle pass finishes the restore with no request in hand.
    idle, base = queued("idle@example.org"), store.active()
    monkeypatch.setattr(installer.DatabaseMaterializer, "activate", crashing)
    with pytest.raises(StorageInvariantError):
        install(idle)
    monkeypatch.setattr(installer.DatabaseMaterializer, "activate", original)
    crashed_before_restore(idle)
    assert next_configuration_request() is None
    with web():
        assert browser.get(PAGE).status_code == 503
    with as_config_installer():
        admit_configuration_database()
        installer.restore_refused(store, admit=admit_configuration_database)
    assert store.active() == base
    with web():
        assert browser.get(PAGE).status_code == 200
    assert "idle@example.org" not in rules(store)
    # The installer's column grant serves the share lock alone: the identity
    # trigger refuses even an update that changes nothing.
    with (
        as_config_installer(),
        pytest.raises(IntegrityError, match="advance the record version"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_portal_user SET id=id WHERE id=%s", [admin.pk]
        )
    # The activation's share lock really holds an identity change off until
    # it commits: a second connection's disable waits behind it, then lands.
    settings = connection.settings_dict
    with (
        psycopg.connect(
            host=settings["HOST"],
            port=settings["PORT"],
            user=settings["USER"],
            password=settings["PASSWORD"],
            dbname=settings["NAME"],
            autocommit=False,
        ) as other,
        as_config_installer(),
        transaction.atomic(),
    ):
        assert installer.actor_authorized(stranded, lock=True) is True
        with other.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                cursor.execute(
                    "UPDATE stewardship_portal_user SET disabled=true, "
                    "version=version+1 WHERE id=%s",
                    [admin.pk],
                )
    assert not PortalUser.objects.get(pk=admin.pk).disabled


def test_a_review_signed_for_one_administrator_is_refused_for_another(
    auth_service, google
):
    """The signed review binds its actor; another Administrator cannot confirm it."""
    store = auth_service.store
    add_rules(store, address("second@example.org"))
    requests = ConfigurationChangeRequest.objects.count()
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        signed = token(
            post(
                browser,
                proposal(
                    store, kind="address", identity="new@example.org", roles=["staff"]
                ),
            )
        )
    google[0].update(email="second@example.org", sub="second-subject")
    other, login = signed_in()
    assert login.status_code == 302
    with web():
        refused = other.post(
            URL,
            {
                "csrfmiddlewaretoken": other.cookies["csrftoken"].value,
                "action": "confirm",
                "preview": signed,
            },
        )
    assert refused.status_code == 403
    assert ConfigurationChangeRequest.objects.count() == requests


def test_a_seeded_grant_keeps_its_origin_through_the_route(
    tmp_path, settings, real_limiter, google
):
    """Widening a seeded address keeps its seeded origin beside the manual one."""
    held = assignment("chair@example.org", ministry=9, seeded=True)
    service = auth_runtime(
        tmp_path,
        settings,
        real_limiter,
        [
            address(),
            address("chair@example.org", ("ministry_leader",), seeded=True),
            held,
        ],
    )
    store = service.store
    browser, login = signed_in()
    assert login.status_code == 302
    request = applied(
        store,
        browser,
        proposal(
            store,
            kind="address",
            identity="chair@example.org",
            roles=["ministry_leader", "staff"],
        ),
    )
    chair = rules(store)["chair@example.org"]
    assert chair["roles"] == ["ministry_leader", "staff"]
    assert set(chair["grants"]["ministry_leader"]) == {"chair-seed"}
    assert chair["grants"]["staff"] == {"manual": str(request.pk)}
    assert chair["creation_origin"] == "chair-seed"


def test_the_review_counts_reach_as_the_page_does(auth_service, google):
    """Domain reach is the page's column; address reach is read for that address."""
    store = auth_service.store
    add_rules(
        store,
        domain("example.org", roles=("staff",)),
        address("exact@example.org", roles=("staff",)),
    )
    # Four identities presented the example.org claim; only one is authorized
    # through the rule: the exact address is replaced, the alias domain's suffix
    # does not match, and a disabled identity cannot sign in. A consumer account
    # outside every configured domain is recorded too, from a refused attempt.
    for email, hosted, disabled in (
        ("colleague@example.org", "example.org", False),
        ("exact@example.org", "example.org", False),
        ("alias@other.example", "example.org", False),
        ("off@example.org", "example.org", True),
        ("person@gmail.com", None, False),
        ("person@gmail.com", None, True),
    ):
        PortalUser.objects.create(
            google_subject=str(uuid4()),
            email=email,
            hosted_domain=hosted,
            verified_at=timezone.now(),
            disabled=disabled,
        )
    browser, login = signed_in()
    assert login.status_code == 302
    assert "<td>1</td>" in row(page(browser), "example.org")
    with web():
        widened = post(
            browser,
            proposal(
                store,
                kind="domain",
                identity="example.org",
                roles=["staff", "ministry_leader"],
            ),
        )
        assert b"1 recorded Google account is authorized" in widened.content
        # A rule that does not exist yet reaches the usable identities that
        # presented its claim from a matching address and have no exact rule.
        fresh = post(
            browser,
            proposal(store, kind="domain", identity="new.example", roles=["staff"]),
        )
        assert b"0 recorded Google accounts are authorized" in fresh.content
        PortalUser.objects.create(
            google_subject=str(uuid4()),
            email="someone@new.example",
            hosted_domain="new.example",
            verified_at=timezone.now(),
        )
        fresh = post(
            browser,
            proposal(store, kind="domain", identity="new.example", roles=["staff"]),
        )
        assert b"1 recorded Google account is authorized" in fresh.content
        # The consumer account is outside every domain rule and has no rule
        # yet, so the page's index never loaded it; the review still counts it.
        consumer = post(
            browser,
            proposal(
                store, kind="address", identity="Person@gmail.com", roles=["staff"]
            ),
        )
        assert (
            b"1 usable recorded Google identity is at this address" in consumer.content
        )
        assert b"2 usable" not in consumer.content
        exact = post(
            browser,
            proposal(
                store, kind="address", identity="exact@example.org", operation="remove"
            ),
        )
        assert b"1 usable recorded Google identity is at this address" in exact.content


def test_refusals_explain_without_echoing_and_guards_hold(auth_service, google):
    """Closed refusals, a stale digest, the last Administrator, and denial."""
    store = auth_service.store
    add_rules(store, domain("example.org", roles=("staff",)))
    # Installing that rule made a request of its own; nothing below adds one.
    requests = ConfigurationChangeRequest.objects.count()
    browser, login = signed_in()
    assert login.status_code == 302
    refusals = (
        (dict(kind="domain", identity="gmail.com", roles=["staff"]), "consumer"),
        (dict(kind="domain", identity="not a domain", roles=["staff"]), "not valid"),
        (dict(kind="domain", identity="new.example", roles=[]), "at least one role"),
        (dict(kind="domain", identity="example.org", roles=["staff"]), "already"),
        (
            dict(kind="address", identity="x@example.org", operation="remove"),
            "no longer",
        ),
        # Removing the only Administrator's role leaves an invalid policy.
        (
            dict(kind="address", identity="admin@example.org", roles=["staff"]),
            "exact-address Administrator",
        ),
        (
            dict(kind="address", identity="admin@example.org", operation="remove"),
            "exact-address Administrator",
        ),
    )
    with web():
        for values, expected in refusals:
            response = post(browser, proposal(store, **values))
            assert response.status_code == 400, values
            body = response.content.decode()
            # The refusal keeps the Admin chrome, whose own text names the
            # parish's testing address; the refusal itself names no target.
            refusal = re.search(
                r'<section class="flow panel">.*?</section>', body, flags=re.S
            ).group(0)
            assert expected in refusal and "Return to Portal users" in refusal
            assert values["identity"] not in refusal and "gmail" not in refusal.lower()
            assert response["Cache-Control"] == "no-store"
        # A preview drawn from an older policy is stale, never applied to a newer one.
        stale = proposal(
            store, kind="domain", identity="other.example", roles=["staff"]
        )
        stale["base_digest"] = "0" * 64
        old = post(browser, stale)
        assert old.status_code == 409 and b"changed since" in old.content
        assert b"Return to Portal users" in old.content
        # A review signed against the applied policy is refused at confirmation
        # once another change has activated: the shared admission checks the
        # digest again under the work transaction, and nothing is requested.
        signed = token(
            post(
                browser,
                proposal(
                    store, kind="domain", identity="late.example", roles=["staff"]
                ),
            )
        )
    add_rules(store, domain("meanwhile.example", roles=("staff",)))
    requests += 1
    with web():
        late = post(browser, {"action": "confirm", "preview": signed})
        assert late.status_code == 409 and b"changed since" in late.content
        assert "late.example" not in rules(store)
        # Unknown fields, a query string and the wrong method are refused outright.
        extra = proposal(store, kind="domain", identity="a.example", roles=["staff"])
        assert post(browser, extra | {"extra": "x"}).status_code == 400
        queried = browser.post(
            URL + "?kind=domain",
            {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value} | extra,
        )
        assert queried.status_code == 400
        assert browser.get(URL).status_code == 405
        assert browser.post(URL, {"action": "preview"}).status_code == 403  # No CSRF.
        # A tampered preview is refused, and nothing was requested by any of this.
        assert (
            post(browser, {"action": "confirm", "preview": "forged"}).status_code == 400
        )
        assert ConfigurationChangeRequest.objects.count() == requests
    # With a second Administrator in place, giving up one's own role is allowed
    # and said plainly; granting Administrator is called the expansion it is.
    add_rules(store, address("second@example.org"))
    with web():
        own = post(
            browser,
            proposal(
                store, kind="address", identity="admin@example.org", roles=["staff"]
            ),
        )
        assert own.status_code == 200
        assert b"removes your own Administrator role" in own.content
        assert b"High-impact expansion" not in own.content
        gone = post(
            browser,
            proposal(
                store, kind="address", identity="admin@example.org", operation="remove"
            ),
        )
        assert gone.status_code == 200
        assert b"removes your own exact-address rule" in gone.content
        promotion = post(
            browser,
            proposal(
                store,
                kind="address",
                identity="new@example.org",
                roles=["administrator"],
            ),
        )
        assert b"High-impact expansion" in promotion.content
        assert b"grants Administrator to an exact address" in promotion.content
    # A Ministry leader never reaches the editor at all.
    add_rules(store, address("leader@example.org", roles=("ministry_leader",)))
    google[0].update(email="leader@example.org", sub="leader-subject")
    other, login = signed_in()
    assert login.status_code == 302
    with web():
        refused = other.post(
            URL,
            {"csrfmiddlewaretoken": other.cookies["csrftoken"].value} | extra,
        )
        assert refused.status_code == 403
    # The second Administrator's and the leader's rules were two more installed
    # requests; the previews, refusals and the leader's attempt added none.
    assert ConfigurationChangeRequest.objects.count() == requests + 2
    assert PortalUser.objects.filter(email="leader@example.org").exists()
