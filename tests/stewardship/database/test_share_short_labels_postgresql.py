"""Frozen migration 0166: a live campaign's short labels stay editable (#1569).

0166 replaces ``stewardship_campaign_pointer_v1`` with 0103's body, changing
only the locked-structure check so that a structurally locked campaign's
share options are compared without their ``short_label`` keys. This tests
the file against the OLD (0103) definition, that its DO block refuses each
regression, and, end to end, that on a live campaign the Share options page
changes the short labels and nothing else, without making the Family form
stale.
"""

import re
from copy import deepcopy
from difflib import ndiff
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns import admission
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.responses.financial_inputs import financial_definition
from parishkit.stewardship.responses.inputs import definition_digest

from .auth_builders import signed_in
from .campaign_builders import change, command
from .test_campaign_views_postgresql import apply, post, requested
from .test_parish_views_postgresql import token
from .test_share_views_postgresql import setup as financial_draft

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = (MIGRATIONS / "0166_share_short_labels.sql").read_text(encoding="utf-8")
OLD = (MIGRATIONS / "0103_any_creation_timezone.sql").read_text(encoding="utf-8")
POINTER = "public.stewardship_campaign_pointer_v1()"
MARK = "e.option - 'short_label'"


def statement(text):
    """The pointer function's ``CREATE OR REPLACE`` statement in ``text``."""
    start = text.index(f"CREATE OR REPLACE FUNCTION {POINTER}")
    return text[start : text.index("END $$;", start) + len("END $$;")]


def installed(cursor):
    """The installed pointer function's body."""
    cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [POINTER])
    return cursor.fetchone()[0]


def test_the_body_is_0103s_with_only_the_share_option_check_changed():
    """One line changes (share_options joins the exempt keys) and the new
    check is added; nothing else differs."""
    diff = list(ndiff(statement(OLD).splitlines(), statement(FROZEN).splitlines()))
    removed = [line[2:] for line in diff if line.startswith("- ")]
    added = [line[2:] for line in diff if line.startswith("+ ")]
    assert len(removed) == 1 and "'ministry_leader_roles'])" in removed[0]
    assert added[0] == removed[0].replace(
        "'ministry_leader_roles']", "'ministry_leader_roles','share_options']"
    )
    # The new check compares both sides' options with short labels removed.
    assert sum(MARK in line for line in added) == 2
    assert sum("IS DISTINCT FROM" in line for line in added) == 2


def test_the_file_replaces_the_old_definition():
    """Over 0103's body the file installs the new one and its check passes."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(statement(OLD))
        assert MARK not in installed(cursor)
        cursor.execute(FROZEN)
        assert MARK in installed(cursor)
        transaction.set_rollback(True)


def test_migration_check_refuses_the_old_body_or_a_changed_function():
    """0166's DO block passes as installed and refuses each regression."""
    capture = FROZEN[FROZEN.index("SELECT set_config(") :]
    capture = capture[: capture.index("true);") + len("true);")]
    check = FROZEN[FROZEN.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(capture)
        cursor.execute(check)
        for change_sql, message in (
            (statement(OLD), "not installed as declared"),
            (f"ALTER FUNCTION {POINTER} SECURITY DEFINER", "not installed"),
            (f"ALTER FUNCTION {POINTER} RESET search_path", "not installed"),
            (
                "CREATE ROLE pk_s1569_probe NOLOGIN; "
                f"GRANT EXECUTE ON FUNCTION {POINTER} TO pk_s1569_probe",
                "grants changed",
            ),
        ):
            with (
                pytest.raises(DatabaseError, match=f"Migration 0166.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change_sql)
                cursor.execute(check)
        transaction.set_rollback(True)


def live(store):
    """A live (structurally locked) financial campaign with the default options."""
    row, previous, path = financial_draft(store)
    command(row, uuid4(), Action.ACTIVATE)
    row.refresh_from_db()
    assert row.structural_locked
    return row, previous, path


def update(store, row, options):
    """Install a change of the campaign's share options; return the receipt,
    or the error that refused it (Python admission's or SQL's)."""
    try:
        return change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(row.pk),
                    "values": {"share_options": options},
                }
            ],
        )
    except (ConfigError, DatabaseError) as error:
        return error


def applied(result):
    """Whether ``update`` installed its change."""
    return getattr(result, "state", None) == "applied"


@pytest.mark.parametrize("other", ["label", "free_text", "order", "removal"])
@pytest.mark.parametrize("bypass", [False, True])
def test_a_live_campaign_changes_only_its_short_labels(
    auth_service, monkeypatch, bypass, other
):
    """A short-label-only change installs; a label, free-text, order or
    removal change is refused. With Python's admission bypassed, SQL's own
    lock refuses them too, so the exemption covers only short labels."""
    store = auth_service.store
    row, previous, _ = live(store)
    if bypass:
        monkeypatch.setattr(admission, "validate_installation", lambda *a, **k: None)
    shortened = deepcopy(previous)
    shortened[0]["short_label"] = "Check"
    del shortened[1]["short_label"]
    assert applied(update(store, row, shortened))
    row.refresh_from_db()
    assert row.active_configuration.values["share_options"] == shortened
    # One refused change per test: a refusal by SQL leaves the installer
    # needing recovery before the next change.
    refused = deepcopy(shortened)
    if other == "label":
        refused[0]["label"] = "Changed"
    elif other == "free_text":
        refused[0]["free_text"] = not refused[0]["free_text"]
    elif other == "order":
        refused.reverse()
    else:
        refused.pop()
    result = update(store, row, refused)
    assert not applied(result)
    if bypass:
        assert "Live structural settings are locked" in str(result)
    row.refresh_from_db()
    assert row.active_configuration.values["share_options"] == shortened


def short_post(store, previous, shorts):
    """A live campaign's Review post, as the browser sends it: each row's id
    and short label only, its other fields being disabled."""
    data = {
        "action": "preview",
        "base_digest": store.active().digest,
        "options-TOTAL_FORMS": str(len(previous)),
        "options-INITIAL_FORMS": str(len(previous)),
    }
    for index, (row, short) in enumerate(zip(previous, shorts, strict=True)):
        data[f"options-{index}-id"] = row["id"]
        data[f"options-{index}-short_label"] = short
    return data


def test_the_live_page_edits_only_short_labels(auth_service, google):
    """On a live campaign Share options opens with every field but the
    short label disabled; a short-label change is reviewed, recorded,
    audited and installed without changing the Family form's definition or
    digest; a post of any other field is refused in the dialog."""
    store = auth_service.store
    row, previous, path = live(store)
    browser, _ = signed_in()
    page = browser.get(path)
    assert page.status_code == 200
    text = page.content.decode()
    assert "Admin tables and charts show them, and Families always see" in text
    assert "placeholders inside double braces" not in text
    assert re.search(r'<input type="text" name="options-0-label"[^>]* disabled', text)
    short = re.search(r'<input type="text" name="options-0-short_label"[^>]*>', text)
    assert short and "disabled" not in short.group(0)
    # No blank row to add an option in.
    assert 'name="options-TOTAL_FORMS" value="7"' in text
    family_before = definition_digest(row.active_configuration.values)
    definition_before = financial_definition(
        row.active_configuration.values, campaign_id=row.pk
    )
    shorts = [option.get("short_label", "") for option in previous]
    shorts[0] = "Check"
    requests = ConfigurationChangeRequest.objects.count()
    preview = post(browser, path, short_post(store, previous, shorts))
    lines = re.findall(r"<li>(.*?)</li>", preview.content.decode(), flags=re.S)
    assert len(lines) == 1 and "Short label for" in lines[0], lines
    response = post(browser, path, {"action": "confirm", "preview": token(preview)})
    apply(store, response)
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # Audited as every Admin settings change is, under its request.
    assert AuditEvent.objects.filter(subject_id=requested(response)).exists()
    row.refresh_from_db()
    values = row.active_configuration.values
    assert values["share_options"][0]["short_label"] == "Check"
    assert [option["label"] for option in values["share_options"]] == [
        option["label"] for option in previous
    ]
    # Nothing a Family sees changed, so no open Family form is stale.
    assert definition_digest(values) == family_before
    assert financial_definition(values, campaign_id=row.pk) == definition_before
    # A form drawn before go-live (or built by hand) sends a label: refused.
    stale = short_post(store, previous, shorts) | {"options-0-label": "Changed"}
    refused = post(browser, path, stale)
    assert refused.status_code == 409
    assert refused.json()["refusal"]["message"] == (
        "This campaign is locked, so only the short labels can change."
    )
    # A blank row, which the live form does not have, is refused too.
    extra = short_post(store, previous, shorts) | {
        "options-TOTAL_FORMS": str(len(previous) + 1),
        f"options-{len(previous)}-short_label": "New",
    }
    assert post(browser, path, extra).status_code == 400
    assert ConfigurationChangeRequest.objects.count() == requests + 1
