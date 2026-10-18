"""Specific refusal reasons reach the durable entry on real pages (#1403, PR 5).

The Administrator's 2026-10-15 cases (a refused logo upload, a logo form
posted with fields it does not have, the Off-site backups page's Test
access) and the refusals Automation access draws in place with 200. Real
sessions and the real middleware stack, transactional as in
``test_admin_refusal_log_postgresql.py``.
"""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from parishkit.stewardship.web.content import MAX_IMAGE_BYTES

from .auth_builders import signed_in, stale_sign_in
from .automation_builders import post
from .test_admin_refusal_log_postgresql import entries, user_id
from .test_artwork_views_postgresql import draft, staged
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_branding_views_postgresql import URL as LOGOS
from .test_branding_views_postgresql import (
    media,  # noqa: F401
    upload,
)
from .test_branding_views_postgresql import post as post_files
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_views_postgresql import post as setup_post
from .test_setup_views_postgresql import setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
APPROVAL = "/admin/users/automation/approval/"
BACKUP = "/admin/system/integrations/backup/"
TEST_ACCESS = {
    "drive_not_in_local",
    "workspace_mail_missing",
    "drive_folder_missing",
    "drive_folder_link_invalid",
}


def test_a_logo_over_the_limit_records_both_sizes(auth_service, google, media):  # noqa: F811
    """Parish logos: the limit and the file's size, never its name."""
    browser, _ = signed_in()
    size = MAX_IMAGE_BYTES + 1
    refused = post_files(
        browser,
        LOGOS,
        {
            "base_digest": auth_service.store.active().digest,
            "logo": SimpleUploadedFile("private-name.png", b"x" * size, "image/png"),
        },
    )
    assert refused.status_code == 400
    (row,) = entries()
    assert row.level == "INFO" and row.actor_id == user_id()
    assert row.context["reason"] == "image_too_large"
    assert row.context["codes"] == ["too_large"]
    assert row.context["fields"] == ["logo"]
    assert (row.context["expected"], row.context["actual"]) == (
        str(MAX_IMAGE_BYTES),
        str(size),
    )
    assert "private-name" not in json.dumps(row.context)


def test_a_logo_form_with_extra_fields_counts_them(auth_service, google, media):  # noqa: F811
    """The unexpected names are counted, never named."""
    browser, _ = signed_in()
    refused = post_files(
        browser,
        LOGOS,
        {
            "base_digest": auth_service.store.active().digest,
            "zz_private_extra": "hunter2",
            "zz_other_extra": "1",
        },
    )
    assert refused.status_code == 400
    (row,) = entries()
    assert row.context["reason"] == "unexpected_fields"
    assert row.context["unexpected_count"] == 2
    text = json.dumps(row.context)
    assert "zz_private_extra" not in text and "hunter2" not in text


def test_test_access_with_extra_fields_counts_them(auth_service, google):
    """The Test access body: an extra field is counted."""
    browser, _ = signed_in()
    refused = post(browser, BACKUP, action="test", target="", zz_extra="x")
    assert refused.status_code == 400
    (row,) = entries()
    assert row.context["route"] == "admin:integration_settings"
    assert row.context["reason"] == "unexpected_fields"
    assert row.context["unexpected_count"] == 1


def test_a_refused_test_access_says_which_refusal(auth_service, google):
    """Each of Test access's refusals has its own closed reason."""
    browser, _ = signed_in()
    refused = post(browser, BACKUP, action="test", target="not-a-drive-link")
    assert refused.status_code == 400
    (row,) = entries()
    assert row.context["reason"] in TEST_ACCESS
    assert "not-a-drive-link" not in json.dumps(row.context)


def test_a_code_posted_from_an_old_sign_in_reaches_the_lookup(auth_service, google):
    """A sign-in older than five minutes looks the code up (#1547).

    The unknown code is refused by the lookup itself, at INFO, not by any
    sign-in age.
    """
    browser, _ = signed_in()
    stale_sign_in()
    assert browser.get(APPROVAL).status_code == 200
    assert entries() == []
    page = post(browser, APPROVAL, action="lookup", code="ZZZZZZZZ")
    assert page.status_code == 200
    (row,) = entries()
    assert row.level == "INFO"
    assert row.context["reason"] == "pairing_code_refused"
    assert "ZZZZZZZZ" not in json.dumps(row.context)


def test_an_unknown_pairing_code_is_refused_in_place(auth_service, google):
    """An unknown code (or another's pairing): the input's own, at INFO."""
    browser, _ = signed_in()
    page = post(browser, APPROVAL, action="lookup", code="ZZZZZZZZ")
    assert page.status_code == 200
    (row,) = entries()
    assert row.level == "INFO" and row.actor_id == user_id()
    assert row.context["reason"] == "pairing_code_refused"
    assert "ZZZZZZZZ" not in json.dumps(row.context)


def test_the_setup_logo_step_records_its_reasons(request):
    """The setup logo step: a logo over the limit, an unreadable one, and
    a form with a field the step does not have (#1396's case)."""
    request.getfixturevalue("setup_http")
    request.getfixturevalue("google")
    request.getfixturevalue("media")
    url = "/admin/setup/branding"
    size = MAX_IMAGE_BYTES + 1
    with web_login():
        browser = started()
        for values in (
            {"version": "1", "logo": SimpleUploadedFile("big.png", b"\0" * size)},
            {"version": "1", "logo": SimpleUploadedFile("bad.png", b"not-a-png")},
            {"version": "1", "logo": upload(), "zz_private_extra": "hunter2"},
        ):
            assert setup_post(browser, url, values).status_code == 400
    large, unsupported, extra = entries()
    assert large.context["route"] == "admin:setup_branding"
    assert large.context["reason"] == "image_too_large"
    assert (large.context["expected"], large.context["actual"]) == (
        str(MAX_IMAGE_BYTES),
        str(size),
    )
    assert unsupported.context["reason"] == "image_unsupported"
    assert unsupported.context["codes"] == ["unsupported_image"]
    assert extra.context["reason"] == "unexpected_fields"
    assert extra.context["unexpected_count"] == 1
    text = json.dumps([row.context for row in entries()])
    assert "zz_private_extra" not in text and "bad.png" not in text


def test_a_campaign_image_records_its_reasons(auth_service, google, media):  # noqa: F811
    """Campaign images: a file over the limit and one that is no image."""
    store = auth_service.store
    _, path = draft(store)
    browser, _ = signed_in()
    size = MAX_IMAGE_BYTES + 1
    for upload_file in (
        SimpleUploadedFile("big.png", b"\0" * size, "image/png"),
        SimpleUploadedFile("bad.png", b"not-a-png", "image/png"),
    ):
        refused = staged(browser, auth_service, path, "member", upload_file)
        assert refused.status_code == 400
    large, unsupported = entries()
    assert large.context["reason"] == "image_too_large"
    assert large.context["actual"] == str(size)
    assert unsupported.context["reason"] == "image_unsupported"
