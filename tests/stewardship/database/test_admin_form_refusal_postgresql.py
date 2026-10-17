"""Admin forms drawn again with their errors leave a refusal entry (#1403).

Real pages, real sessions and the real middleware stack: each view below
answers a refused form by drawing it again (400) and makes no logging call
of its own. The shared form base (``web.admin_forms``) lets the refusal log
name the form's own fields and Django error codes, never what was typed.
Transactional, as in ``test_admin_refusal_log_postgresql.py``.
"""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from ..test_source_corpus import source
from .auth_builders import signed_in
from .test_admin_refusal_log_postgresql import entries, user_id
from .test_branding_views_postgresql import URL as LOGOS
from .test_branding_views_postgresql import media  # noqa: F401
from .test_branding_views_postgresql import post as post_files
from .test_campaign_create_postgresql import CREATE, new_fields
from .test_campaign_views_postgresql import post
from .test_schedule_entry_postgresql import NEW, entry, setup
from .test_source_families_postgresql import (  # noqa: F401
    publish,
    source_singletons,
)

pytestmark = pytest.mark.django_db(transaction=True)
# A value no entry may hold.
TYPED = "Mars/Base"


def only_entry():
    """The one durable refusal entry, checked to hold no typed value."""
    (entry_row,) = entries()
    assert TYPED not in json.dumps(entry_row.context)
    return entry_row


def test_a_refused_logo_upload_names_the_field_and_code(auth_service, google, media):  # noqa: F811
    """Parish logos: an empty file is drawn again at its field."""
    browser, _ = signed_in()
    refused = post_files(
        browser,
        LOGOS,
        {
            "base_digest": auth_service.store.active().digest,
            "logo": SimpleUploadedFile("private-name.png", b"", "image/png"),
        },
    )
    assert refused.status_code == 400
    row = only_entry()
    assert row.level == "INFO" and row.actor_id == user_id()
    assert row.context == {
        "route": "admin:branding_settings",
        "method": "post",
        "status": 400,
        "form": "logo_upload_form",
        "fields": ["logo"],
        "codes": ["empty"],
        "reason": "unclassified",
        "mode": "testing",
    }
    assert "private-name" not in json.dumps(row.context)


def test_a_refused_campaign_zone_names_the_field_and_code(auth_service, google):
    """Create the campaign: a zone that is not in the catalog."""
    publish(source())
    browser, _ = signed_in()
    refused = post(
        browser, CREATE, new_fields(auth_service.store) | {"timezone": TYPED}
    )
    assert refused.status_code == 400
    row = only_entry()
    assert row.context["route"] == "admin:campaign_create"
    # Step 1 is Campaign settings' own form since #1404 PR 3.
    assert row.context["form"] == "campaign_settings_form"
    assert row.context["fields"] == ["timezone"]
    assert row.context["codes"] == ["invalid_choice"]


def test_a_refused_schedule_keeps_the_forms_prefix(auth_service, google):
    """New scheduled email: a date outside the campaign, at its prefixed field."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    refused = post(
        browser,
        NEW,
        entry(
            store,
            kind="reminder",
            date="2054-11-15",
            time="09:00",
            template_version=emails["reminder"]["id"],
        ),
    )
    assert refused.status_code == 400
    row = only_entry()
    assert row.context["route"] == "admin:schedule_new"
    assert row.context["form"] == "schedule_form"
    # The page's own form first; the view also checks the campaign's whole
    # set of schedules, a formset whose new row is refused at its date too.
    assert row.context["fields"] == ["schedule-date", "schedules-2-date"]
    assert row.context["codes"] == ["outside_campaign"]
    assert "2054" not in json.dumps(row.context)
