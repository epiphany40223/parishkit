"""``pk-stewardship admin files delete`` and ``files rename`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. Both commands
are the Hosted files page's row actions through the page's own
``hosted_file_uses.delete_many`` and ``rename``, so the guards, the stored
copies and the page's audit rows are the page's.
"""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import hosted_file_storage as storage
from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.accounts.hosted_file_models import HostedFile
from parishkit.stewardship.audit.models import AuditEvent

from .. import hosted_file_samples as samples
from ..content_factory import content
from .automation_builders import paired
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_hosted_files_postgresql import (
    ORIGIN,
    draft,
    media,  # noqa: F401
    pending,
    uploaded,
    without_triggers,
)

pytestmark = pytest.mark.django_db(transaction=True)

DELETE = "admin_cmd_files_delete"
RENAME = "admin_cmd_files_rename"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def delete(admin, secret, *ids, yes=False, answer=b""):
    """``files delete`` for ``ids``, with ``--yes`` or ``answer`` at the prompt."""
    options = ("--yes",) if yes else ()
    return admin(
        "files",
        "delete",
        *map(str, ids),
        *options,
        secret=secret,
        answer=answer,
    )


def rename(admin, secret, file_id, slug):
    """``files rename FILE_ID --slug SLUG``."""
    return admin("files", "rename", str(file_id), "--slug", slug, secret=secret)


def events(kind):
    """How many events of ``kind`` are recorded."""
    return AuditEvent.objects.filter(event_type=kind).count()


def as_staff(monkeypatch):
    """Give every admission the session's principal holding only Staff.

    Pairing needs an Administrator, so the page's ``CONFIGURE`` check is
    shown a Staff principal this way.
    """
    real = sessions.authenticated_admin

    def staff(*arguments, **options):
        """The real admission, its principal holding only Staff."""
        principal = real(*arguments, **options)
        if principal is None:
            return None
        return replace(principal, roles=frozenset({"staff"}))

    monkeypatch.setattr(sessions, "authenticated_admin", staff)


def test_unused_files_are_deleted_at_the_prompt(admin, media):  # noqa: F811
    """The prompt names the files; the page's events and the command's; the
    stored copies are gone; a repeat is already deleted and records nothing."""
    browser, secret, row = paired(admin.service)
    guide = uploaded(browser, "guide.pdf", samples.pdf(), slug="guide")
    picnic = uploaded(browser, "picnic.png", samples.image("PNG"), slug="picnic")
    kept = uploaded(browser, "kept.pdf", samples.pdf(), slug="kept")

    # Refused prompts change nothing.
    for answer in (b"", b"no\n"):
        code, refused, errors = delete(
            admin, secret, guide.pk, picnic.pk, answer=answer
        )
        assert code == 4 and refused["error"]["code"] == "confirmation_required"
        assert "Delete 2 hosted file(s): guide, picnic." in errors
        assert "cannot be undone" in errors
    assert HostedFile.objects.count() == 3 and events("hosted_file_deleted") == 0

    code, document, _ = delete(admin, secret, guide.pk, picnic.pk, answer=b"yes\n")
    assert code == 0, document
    assert document["result"] == {
        "deleted": [str(guide.pk), str(picnic.pk)],
        "already_deleted": [],
    }
    assert list(HostedFile.objects.values_list("pk", flat=True)) == [kept.pk]
    for gone in (guide, picnic):
        assert not (media / storage.DIRECTORY / gone.pk.hex).exists()
    assert (media / storage.DIRECTORY / kept.pk.hex).exists()
    # The page's event per file is the Administrator's; the command's names
    # the session. A deletion is not a fresh-gated action: no notice.
    deleted = AuditEvent.objects.filter(event_type="hosted_file_deleted")
    assert {event.subject_id for event in deleted} == {guide.pk, picnic.pk}
    assert {event.actor_id for event in deleted} == {row.principal_id}
    [event] = AuditEvent.objects.filter(event_type=DELETE)
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    assert event.auditcontext.context == {"outcome": "succeeded"}
    assert events("automation_fresh_gate") == 0
    assert not AutomationNotice.objects.exclude(kind="approved").exists()

    # A repeat (after an unknown outcome, say) asks nothing and records
    # nothing; with a file still there, only that one is deleted.
    code, document, errors = delete(admin, secret, guide.pk)
    assert code == 0, document
    assert document["result"] == {"deleted": [], "already_deleted": [str(guide.pk)]}
    assert "Type yes" not in errors
    assert events(DELETE) == 1 and events("hosted_file_deleted") == 2
    code, document, _ = delete(admin, secret, guide.pk, kept.pk, yes=True)
    assert code == 0, document
    assert document["result"] == {
        "deleted": [str(kept.pk)],
        "already_deleted": [str(guide.pk)],
    }
    assert not HostedFile.objects.exists() and events(DELETE) == 2


def test_a_file_in_use_is_refused_before_the_prompt(admin, media):  # noqa: F811
    """A chosen file in use refuses the whole deletion, naming the use."""
    browser, secret, _ = paired(admin.service)
    store = admin.service.store
    campaign, _ = draft(store)
    used = uploaded(browser, "used.pdf", samples.pdf(), slug="used")
    free = uploaded(browser, "free.pdf", samples.pdf(), slug="free")
    pending(
        store,
        content(str(campaign.pk), slot="login_help", html="<p>{{ file.used }}</p>"),
    )
    code, document, errors = delete(admin, secret, free.pk, used.pk, yes=True)
    assert code == 1 and document["error"]["code"] == "invalid", document
    [field] = document["error"]["fields"]
    assert field["field"] == str(used.pk) and field["code"] == "invalid"
    assert "A change that is still being applied" in field["message"]
    assert "Type yes" not in errors
    assert HostedFile.objects.count() == 2
    assert events("hosted_file_deleted") == 0 and events(DELETE) == 0


def test_a_file_used_meanwhile_is_the_pages_refusal(
    admin,
    media,  # noqa: F811
    monkeypatch,
):
    """A use that appears after the prompt is caught by the page's own
    recheck: nothing is deleted and nothing is recorded."""
    from parishkit.stewardship import admin_hosted_files

    browser, secret, _ = paired(admin.service)
    store = admin.service.store
    campaign, _ = draft(store)
    used = uploaded(browser, "used.pdf", samples.pdf(), slug="used")
    free = uploaded(browser, "free.pdf", samples.pdf(), slug="free")
    pending(
        store,
        content(str(campaign.pk), slot="login_help", html="<p>{{ file.used }}</p>"),
    )
    # The prompt's read saw the files unused.
    monkeypatch.setattr(
        admin_hosted_files,
        "preview_deletion",
        lambda caller, service, ids: list(HostedFile.objects.filter(pk__in=ids)),
    )
    code, document, _ = delete(admin, secret, free.pk, used.pk, yes=True)
    assert code == 1 and document["error"]["code"] == "invalid", document
    # The field is the file in use, by its ID, naming the use.
    [field] = document["error"]["fields"]
    assert field["field"] == str(used.pk) and field["code"] == "invalid"
    assert "A change that is still being applied" in field["message"]
    assert HostedFile.objects.count() == 2
    assert events("hosted_file_deleted") == 0 and events(DELETE) == 0


def test_a_use_gone_again_names_every_chosen_file(
    admin,
    media,  # noqa: F811
    monkeypatch,
):
    """A use the page saw that is gone by the reread still names file IDs:
    every chosen one, with the page's general message."""
    from parishkit.stewardship.accounts import hosted_file_uses
    from parishkit.stewardship.accounts.hosted_files import HostedFileError

    browser, secret, _ = paired(admin.service)
    first = uploaded(browser, "a.pdf", samples.pdf(), slug="first")
    second = uploaded(browser, "b.pdf", samples.pdf(), slug="second")

    def refuse(actor, file_ids, *, record=None):
        """The page's refusal, as if a use came and went."""
        raise HostedFileError("in_use")

    monkeypatch.setattr(hosted_file_uses, "delete_many", refuse)
    code, document, _ = delete(admin, secret, first.pk, second.pk, yes=True)
    assert code == 1 and document["error"]["code"] == "invalid", document
    fields = document["error"]["fields"]
    assert [field["field"] for field in fields] == [str(first.pk), str(second.pk)]
    assert all("nothing was deleted" in field["message"] for field in fields)
    assert HostedFile.objects.count() == 2 and events(DELETE) == 0


def test_a_file_in_unsent_mail_is_refused(admin, media):  # noqa: F811
    """A queued campaign email test linking a file holds it: the deletion is
    refused before the prompt, naming the waiting email, and nothing changes."""
    browser, secret, _ = paired(admin.service)
    mailed = uploaded(browser, "mailed.pdf", samples.pdf(), slug="mailed")
    without_triggers(
        "stewardship_campaign_mail_test",
        "INSERT INTO public.stewardship_campaign_mail_test (id, correlation_id, "
        "version, requested_by_id, request_key, fingerprint, mail, state, "
        "campaign_id, configuration_id, task_id, template_id) VALUES "
        "(%s, %s, 1, %s, %s, %s, %s, 'queued', %s, %s, %s, %s)",
        [
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            "0" * 64,
            json.dumps({"html": f'<a href="{ORIGIN}/files/{mailed.token}">'}),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        ],
    )
    code, document, errors = delete(admin, secret, mailed.pk, yes=True)
    assert code == 1 and document["error"]["code"] == "invalid", document
    [field] = document["error"]["fields"]
    assert field["field"] == str(mailed.pk) and field["code"] == "invalid"
    assert "1 email(s) waiting to be sent" in field["message"]
    assert "Type yes" not in errors
    assert HostedFile.objects.filter(pk=mailed.pk).exists()
    assert (media / storage.DIRECTORY / mailed.pk.hex).exists()
    assert events("hosted_file_deleted") == 0 and events(DELETE) == 0


def test_deletion_usage_and_refused_sessions(
    admin,
    media,  # noqa: F811
    monkeypatch,
):
    """Repeated IDs are a usage error; a read-only session and Staff are
    denied; an ended session is exit 5; nothing is deleted."""
    browser, secret, _ = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    stored = uploaded(browser, "a.pdf", samples.pdf())
    code, document, _ = delete(admin, secret, stored.pk, stored.pk, yes=True)
    assert code == 2 and document["error"]["code"] == "usage", document
    code, document, _ = delete(admin, reader, stored.pk, yes=True)
    assert code == 1 and document["error"]["code"] == "denied", document
    _, ended, _ = paired(admin.service)
    revoke(admin.service, ended)
    code, document, _ = delete(admin, ended, stored.pk, yes=True)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    as_staff(monkeypatch)
    code, document, _ = delete(admin, secret, stored.pk, yes=True)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert HostedFile.objects.filter(pk=stored.pk).exists()
    assert events("hosted_file_deleted") == 0 and events(DELETE) == 0


def test_a_busy_library_is_unavailable(admin, media):  # noqa: F811
    """The page's storage lock held elsewhere: retry, nothing deleted."""
    browser, secret, _ = paired(admin.service)
    stored = uploaded(browser, "a.pdf", samples.pdf())
    with storage.storage_lock(media):
        code, document, _ = delete(admin, secret, stored.pk, yes=True)
    assert code == 3 and document["error"]["code"] == "unavailable", document
    assert HostedFile.objects.filter(pk=stored.pk).exists()
    assert events(DELETE) == 0


def test_an_unused_file_is_renamed(admin, media):  # noqa: F811
    """The page's event and the command's; the link is unchanged; the current
    name changes and records nothing."""
    browser, secret, row = paired(admin.service)
    stored = uploaded(browser, "guide.pdf", samples.pdf(), slug="guide")
    code, document, errors = rename(admin, secret, stored.pk, " parish-guide ")
    assert code == 0, document
    assert "Type yes" not in errors
    result = document["result"]
    assert result["changed"] is True
    assert result["file"]["slug"] == "parish-guide"
    assert result["file"]["placeholder"] == "{{ file.parish-guide }}"
    assert result["file"]["link"] == f"{ORIGIN}/files/{stored.token}"
    assert HostedFile.objects.get(pk=stored.pk).slug == "parish-guide"
    [changed] = AuditEvent.objects.filter(event_type="hosted_file_slug_changed")
    assert changed.actor_id == row.principal_id
    assert changed.auditcontext.context["previous_file_slug"] == "guide"
    [event] = AuditEvent.objects.filter(event_type=RENAME)
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    assert not AutomationNotice.objects.exclude(kind="approved").exists()

    code, document, _ = rename(admin, secret, stored.pk, "parish-guide")
    assert code == 0, document
    assert document["result"]["changed"] is False
    assert events(RENAME) == 1 and events("hosted_file_slug_changed") == 1


def test_renaming_refusals(admin, media, monkeypatch):  # noqa: F811
    """In use, an invalid or taken name, a missing file, a read-only session
    and Staff are refused, and nothing changes."""
    browser, secret, _ = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    store = admin.service.store
    campaign, _ = draft(store)
    used = uploaded(browser, "used.pdf", samples.pdf(), slug="used")
    free = uploaded(browser, "free.pdf", samples.pdf(), slug="free")
    pending(
        store,
        content(str(campaign.pk), slot="login_help", html="<p>{{ file.used }}</p>"),
    )

    code, document, _ = rename(admin, secret, used.pk, "renamed")
    assert code == 1 and document["error"]["code"] == "invalid", document
    [field] = document["error"]["fields"]
    assert field["field"] == str(used.pk)
    assert "A change that is still being applied" in field["message"]
    for slug, words in (
        ("Bad Name", "lowercase letters"),
        ("used", "already uses this placeholder name"),
    ):
        code, document, _ = rename(admin, secret, free.pk, slug)
        assert code == 1 and document["error"]["code"] == "invalid", document
        [field] = document["error"]["fields"]
        assert field["field"] == "slug" and words in field["message"]
    code, document, _ = rename(admin, secret, uuid4(), "renamed")
    assert code == 1 and document["error"]["code"] == "not_available", document
    code, document, _ = rename(admin, reader, free.pk, "renamed")
    assert code == 1 and document["error"]["code"] == "denied", document
    as_staff(monkeypatch)
    code, document, _ = rename(admin, secret, free.pk, "renamed")
    assert code == 1 and document["error"]["code"] == "denied", document
    assert sorted(HostedFile.objects.values_list("slug", flat=True)) == [
        "free",
        "used",
    ]
    assert events("hosted_file_slug_changed") == 0 and events(RENAME) == 0
