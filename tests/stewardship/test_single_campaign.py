"""The single-campaign interim (ADM-12.05; admin-portal spec, navigation rule 10).

Multi-campaign controls are greyed out with one tip, and their actions are
refused in campaign service code. The views' refusals are covered by
``database/test_clone_views_postgresql.py``,
``database/test_campaign_views_postgresql.py`` and the report suites; the
browser behavior of the tip by ``browser/test_single_campaign.py``.
"""

from pathlib import Path

import pytest
from django.template import Context, Template

from parishkit.stewardship.campaigns import single_campaign
from parishkit.stewardship.web.refusals import UserFacingGone, gone_response

TIP = "Disabled; will be removed with the single-campaign change (#145)"
TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)


def test_tip_is_the_specified_text():
    """The spec's exact wording, defined once."""
    assert str(single_campaign.TIP) == TIP


def test_multi_campaign_control_is_an_unavailable_described_control():
    """No href; keyboard-reachable; the tip is its accessible description."""
    html = Template(
        '{% load stewardship %}{% multi_campaign_control "copy-campaign" '
        '"Copy campaign" %}'
    ).render(Context())
    assert html.strip() == (
        '<span class="disabled-control"><a class="disabled-control-link" '
        'role="link" aria-disabled="true" tabindex="0" '
        'aria-describedby="copy-campaign-tip" data-menu-tip>Copy campaign</a>'
        '<span class="admin-tip" id="copy-campaign-tip" role="tooltip">'
        f"{TIP}</span></span>"
    )


@pytest.mark.parametrize(
    ("template", "control", "retired"),
    [
        (
            "campaign-settings.html",
            '{% multi_campaign_control "copy-campaign" _("Copy campaign") %}',
            ("admin:campaign_clone", "clone_sources", "Create campaign draft"),
        ),
        (
            "participation.html",
            '{% multi_campaign_control "choose-campaign" '
            '_("Choose a retained campaign") %}',
            ("picker_url", "admin:report_campaigns"),
        ),
        (
            "ministry-report.html",
            '{% multi_campaign_control "choose-campaign" '
            '_("Choose a retained campaign") %}',
            ("admin:ministry_report_campaigns",),
        ),
    ],
)
def test_templates_grey_out_their_multi_campaign_control(template, control, retired):
    """Each named control is the greyed one; its old link or action is gone."""
    source = (TEMPLATES / template).read_text()
    assert control in source
    assert not [text for text in retired if text in source]


def test_no_template_links_a_retired_multi_campaign_page():
    """Nothing links New campaign or the two choosers any more.

    The clone templates are checked too: Copy campaign refuses before
    rendering them, so a page render would never catch a stale link there.
    """
    for path in TEMPLATES.rglob("*.html"):
        source = path.read_text()
        for name in ("campaign_new", "report_campaigns", "ministry_report_campaigns"):
            for quote in "'\"":
                assert f"admin:{name}{quote}" not in source, path.name


@pytest.mark.parametrize(
    "patch",
    [
        [{"operation": "add", "section": "campaigns", "id": "x", "values": {}}],
        [
            {"operation": "add", "section": "content", "id": "y", "values": {}},
            {"operation": "add", "section": "campaigns", "id": "x", "values": {}},
        ],
    ],
)
def test_adding_a_campaign_is_refused(patch):
    """Creating or copying a campaign adds a record; that is refused (410)."""
    with pytest.raises(UserFacingGone) as caught:
        single_campaign.refuse_campaign_creation(patch)
    assert str(caught.value.refusal.message) == "Creating another campaign is disabled."
    response = gone_response(caught.value)
    assert response.status_code == 410
    assert response["Cache-Control"] == "no-store"


def test_editing_the_current_campaign_is_not_refused():
    """Updates and other sections pass; only a new campaign record is refused."""
    single_campaign.refuse_campaign_creation(
        [
            {"operation": "update", "section": "campaigns", "id": "x", "values": {}},
            {"operation": "add", "section": "content", "id": "y", "values": {}},
        ]
    )


def test_gone_refusal_is_still_a_permission_error():
    """A handler that does not know the 410 still refuses, never serves."""
    assert isinstance(single_campaign.not_current_refused(), PermissionError)
    assert isinstance(single_campaign.copy_refused(), PermissionError)
