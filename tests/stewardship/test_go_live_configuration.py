"""A structurally valid incomplete draft is not Production readiness."""

from uuid import uuid4

from parishkit.stewardship.accounts.go_live_configuration import configuration_readiness

from .campaign_factory import campaign, financial, schedule
from .configuration_factory import configuration_document
from .content_factory import content
from .policy_factory import address


def configured():
    """A complete synthetic census campaign with one selected invitation revision."""
    document = configuration_document()
    sections = document["sections"]
    owner = campaign()
    template = content(owner["id"], kind="email", slot="initial")
    invitation = schedule(
        owner["id"],
        template_version=template["id"],
        subject=template["values"]["subject"],
    )
    sections.update(
        campaigns=[owner],
        schedules=[invitation],
        content=[template],
        login_rules=[address()],
    )
    sections["integrations"] += [
        {
            "id": str(uuid4()),
            "values": {
                "kind": kind,
                "settings": values,
                "credential_fingerprint": fingerprint,
            },
        }
        for kind, values, fingerprint in (
            ("google_oauth", {"client_id": "example"}, "b" * 64),
            ("google_workspace", {"delegated_email": "test@example.org"}, "c" * 64),
            (
                "email",
                {"sender": "test@example.org", "reply_to": "test@example.org"},
                None,
            ),
        )
    ]
    return document, owner["id"]


def check(document, owner, **catalogs):
    """The test's source catalogs contain no private source payload."""
    return configuration_readiness(
        document, owner, **({"ministries": set(), "funds": set()} | catalogs)
    )


def test_complete_configuration_resolves_selected_family_template():
    document, owner = configured()
    result = check(document, owner)
    assert result.ready and result.admin_recipients
    assert result.family_templates == (document["sections"]["content"][0]["id"],)


def test_deployment_owned_google_login_need_not_be_duplicated_in_campaign():
    """Real first setup installs provider integrations, not another OAuth setting."""
    document, owner = configured()
    document["sections"]["integrations"] = [
        row
        for row in document["sections"]["integrations"]
        if row["values"]["kind"] != "google_oauth"
    ]
    assert check(document, owner).ready


def test_opaque_template_and_page_ids_remain_blockers_not_empty_content():
    document, owner = configured()
    sections = document["sections"]
    sections["content"] = []
    sections["campaigns"][0]["values"]["content_versions"] = {"welcome": str(uuid4())}
    result = check(document, owner)
    assert result.problems == (
        "page_reference_unavailable",
        "mail_template_unavailable",
    )
    assert not result.family_templates


def test_other_campaign_content_cannot_satisfy_an_unresolved_revision():
    document, owner = configured()
    document["sections"]["content"][0]["values"]["campaign_id"] = str(uuid4())
    assert check(document, owner).problems == ("mail_template_unavailable",)


def test_configuration_without_initial_invitation_is_not_ready():
    document, owner = configured()
    document["sections"]["schedules"] = []
    assert check(document, owner).problems == ("initial_schedule_required",)


def test_financial_requires_both_selected_period_funds_and_share_options():
    document, owner = configured()
    values = document["sections"]["campaigns"][0]["values"]
    values.update(modules=["financial"], financial=None)
    assert check(document, owner).problems == ("financial_mapping_incomplete",)
    values.update(
        financial=financial(),
        share_options=[{"id": str(uuid4()), "label": "Check", "free_text": False}],
    )
    assert check(document, owner, funds={1}).problems == (
        "financial_mapping_incomplete",
    )
    assert check(document, owner, funds={1, 2}).ready


def test_missing_ministry_recipient_or_integration_is_explicitly_reported():
    document, owner = configured()
    sections = document["sections"]
    sections["campaigns"][0]["values"].update(modules=["ministry"], ministry_duids=[1])
    sections["login_rules"] = []
    sections["integrations"] = []
    assert check(document, owner).problems == (
        "ministry_mapping_unavailable",
        "integration_configuration_incomplete",
        "admin_recipient_required",
    )
