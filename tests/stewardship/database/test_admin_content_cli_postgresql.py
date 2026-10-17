"""``content list`` and ``content history``: the content reads (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
read the Pages and emails and Content history pages through the pages' own
pieces, so their rows match the pages'.
"""

from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.content_forms import EMAIL_LABELS, page_slots
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate

from ..content_factory import content
from .auth_builders import unguarded
from .automation_builders import paired
from .campaign_builders import add_draft
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_content_resave_lists_postgresql import stale_rows
from .test_content_views_postgresql import added, plant_stale

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on."""
    return runner(auth_service, monkeypatch)


def draft(store):
    """A real applied current draft campaign and its first mail schedule."""
    result, _, schedule = add_draft(store, store.active(), uuid4())
    assert result.state == "applied"
    return Campaign.objects.get(), schedule


def as_staff(monkeypatch):
    """Give the session's principal only Staff, as a non-Administrator.

    Pairing needs an Administrator, so the admission's principal is changed
    afterwards; the pages' ``CONFIGURE`` check refuses it.
    """
    real = sessions.authenticated_admin

    def staff(*arguments, **options):
        """The real admission, its principal holding only Staff."""
        principal = real(*arguments, **options)
        if principal is None:
            return None
        return replace(principal, roles=frozenset({"staff"}))

    monkeypatch.setattr(sessions, "authenticated_admin", staff)


def test_content_list_is_the_pages_list(admin):
    """A read-only session reads the catalog's rows in the page's order: a
    custom page, an email a schedule sends and an unused one; nothing is
    recorded."""
    store = admin.service.store
    campaign, schedule = draft(store)
    owner = str(campaign.pk)
    welcome = content(owner, html="<p>Hello {{ parish_name }}</p>")
    sent = content(owner, kind="email", slot="initial", subject="Sent")
    spare = content(owner, kind="email", slot="initial", subject="Spare")
    added(store, campaign, welcome, sent, spare, schedule=schedule, sent=sent)
    campaign.refresh_from_db()
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()

    code, document, _ = admin("content", "list", secret=reader)
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == owner
    assert result["version"] == store.active().digest
    slots = page_slots(campaign.active_configuration.values)
    assert [page["slot"] for page in result["pages"]] == list(slots)
    assert [page["name"] for page in result["pages"]] == [
        str(name) for name in slots.values()
    ]
    pages = {page["slot"]: page for page in result["pages"]}
    assert pages["welcome"] == {
        "slot": "welcome",
        "name": "Family welcome",
        "state": "edited",
        "resave": None,
    }
    assert pages["login_help"]["state"] == "empty"
    assert [email["slot"] for email in result["emails"]] == list(EMAIL_LABELS)
    emails = {email["slot"]: email for email in result["emails"]}
    initial = emails["initial"]
    assert initial["name"] == "Initial invitation"
    assert initial["scheduled"] and not initial["singleton"]
    revisions = {revision["subject"]: revision for revision in initial["revisions"]}
    assert set(revisions) == {"Sent", "Spare"}
    assert revisions["Sent"]["id"] == sent["id"]
    assert revisions["Sent"]["used_by"] and not revisions["Sent"]["deletable"]
    assert revisions["Spare"]["used_by"] == [] and revisions["Spare"]["deletable"]
    assert revisions["Spare"]["excerpt"].startswith("Welcome to {{ parish_name }}.")
    assert revisions["Spare"]["state"] == "edited"
    assert revisions["Spare"]["resave"] is None
    confirmation = emails["confirmation"]
    assert confirmation["singleton"] and not confirmation["scheduled"]
    # The page's GET records no event, and neither does the command.
    assert AuditEvent.objects.count() == events


def test_content_list_names_a_resave_notice(admin, monkeypatch):
    """Saved markup today's cleaning changes carries the page's notice: a
    page that only loses a comment, and an invitation that lost its code."""
    store = admin.service.store
    campaign, _ = draft(store)
    page, email = stale_rows(str(campaign.pk))
    plant_stale(store, monkeypatch, page, email)
    _, secret, _ = paired(admin.service)
    code, document, _ = admin("content", "list", secret=secret)
    assert code == 0, document
    result = document["result"]
    pages = {row["slot"]: row for row in result["pages"]}
    resave = pages["welcome"]["resave"]
    assert set(resave) == {"removed", "lost", "blocked"}
    assert resave["removed"] and resave["lost"] == [] and not resave["blocked"]
    emails = {row["slot"]: row for row in result["emails"]}
    (revision,) = emails["initial"]["revisions"]
    assert revision["id"] == email["id"]
    assert revision["resave"]["lost"] == ["{{ family_code }}"]
    assert revision["resave"]["blocked"]


def test_content_list_while_work_holds_the_campaign_is_stale(admin):
    """As the page refuses while background work holds the campaign."""
    store = admin.service.store
    campaign, _ = draft(store)
    # Reserving a real gate needs the exclusive maintenance window, so seed
    # the row past its guard, as the page's own test does.
    with unguarded():
        CampaignWorkGate.objects.create(
            campaign=campaign, request_id=uuid4(), initiated_by_id=uuid4()
        )
    _, secret, _ = paired(admin.service)
    code, document, _ = admin("content", "list", secret=secret)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert "result" not in document


@pytest.mark.parametrize("verb", ["list", "history"])
def test_no_current_campaign_is_not_available(admin, verb):
    """Without a campaign there is nothing to list."""
    _, secret, _ = paired(admin.service)
    code, document, _ = admin("content", verb, secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document


def test_content_history_and_one_revisions_sample(admin):
    """The retained entries in the page's order, and one revision's
    fictional sample; nothing is recorded."""
    store = admin.service.store
    campaign, _ = draft(store)
    owner = str(campaign.pk)
    welcome = content(owner, html="<p>Welcome to {{ parish_name }}.</p>")
    invite = content(owner, kind="email", slot="initial", subject="Come and see")
    added(store, campaign, welcome, invite)
    campaign.refresh_from_db()
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()

    code, document, _ = admin("content", "history", secret=reader)
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == owner
    assert result["state"] == campaign.state
    assert result["configuration_id"] == str(
        campaign.active_configuration.configuration.pk
    )
    # The page's order: kind, slot, subject.
    assert result["entries"] == [
        {
            "id": invite["id"],
            "kind": "email",
            "slot": "initial",
            "name": "Initial invitation",
            "subject": "Come and see",
        },
        {
            "id": welcome["id"],
            "kind": "page",
            "slot": "welcome",
            "name": "Family welcome",
            "subject": None,
        },
    ]
    assert result["revision"] is None

    code, document, _ = admin(
        "content", "history", "--revision", welcome["id"], secret=reader
    )
    assert code == 0, document
    revision = document["result"]["revision"]
    assert {key: revision[key] for key in ("id", "kind", "slot", "name")} == {
        "id": welcome["id"],
        "kind": "page",
        "slot": "welcome",
        "name": "Family welcome",
    }
    sample = revision["sample"]
    assert set(sample) == {"subject", "html", "text"}
    parish = campaign.active_configuration.configuration.parish.name
    assert f"Welcome to {parish}." in sample["html"]
    assert "{{" not in sample["text"]
    assert AuditEvent.objects.count() == events


def test_content_history_refuses_an_unknown_revision(admin):
    """An ID the campaign did not retain is not available."""
    draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    code, document, _ = admin(
        "content", "history", "--revision", str(uuid4()), secret=secret
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document


@pytest.mark.parametrize("verb", ["list", "history"])
def test_a_reader_who_is_not_an_administrator_is_denied(admin, monkeypatch, verb):
    """Staff cannot read content, as on the pages: denied, nothing listed."""
    draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    as_staff(monkeypatch)
    code, document, _ = admin("content", verb, secret=secret)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document


@pytest.mark.parametrize("verb", ["list", "history"])
def test_an_ended_session_reads_nothing(admin, verb):
    """A revoked session is exit 5, and nothing is printed."""
    draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document, _ = admin("content", verb, secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert "result" not in document


@pytest.mark.parametrize("held", [False, True])
def test_an_ended_session_outranks_the_lists_refusals(admin, held):
    """With no current campaign, or a held one, an ended session is still
    exit 5: the session is rechecked before the refusal is reported."""
    if held:
        campaign, _ = draft(admin.service.store)
        with unguarded():
            CampaignWorkGate.objects.create(
                campaign=campaign, request_id=uuid4(), initiated_by_id=uuid4()
            )
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document, _ = admin("content", "list", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
