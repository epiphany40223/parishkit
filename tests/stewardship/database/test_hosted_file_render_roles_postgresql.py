"""Every runtime role that renders content can expand hosted files, and no more.

Hosted files (#346) are expanded wherever content is rendered: Family pages and
Admin previews in web, Family mail and chosen-Family tests prepared by the
general worker, and Family mail and receipts re-rendered by mail dispatch. #348
granted the worker and web but not mail dispatch, so every invitation, reminder
or receipt naming a file failed with "permission denied". Each test here runs
under the real runtime login, with content holding both a file link and an
inline hosted image.
"""

from uuid import uuid4

import pytest
from django.db import ProgrammingError, transaction

from parishkit.stewardship.accounts.hosted_file_content import hosted_links, links_for
from parishkit.stewardship.accounts.hosted_file_models import HostedFile
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.responses.page_content import render_pages
from parishkit.stewardship.web.content import render_template

from ..content_factory import content
from .campaign_builders import add_draft, campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim, prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_receipt_dispatch_postgresql import begin as begin_receipt
from .test_receipt_dispatch_postgresql import receipt

pytestmark = pytest.mark.django_db(transaction=True)
ORIGIN = "http://localhost:8000"
# A link to a hosted document and an inline hosted image, as authored.
HTML = (
    '<a href="{{ file.guide }}" rel="noopener noreferrer">Guide</a>'
    '<img src="{{ file.picnic }}" alt="Picnic">'
)


def library():
    """One hosted document and one hosted image, inserted as the owner."""
    return {
        slug: HostedFile.objects.create(
            slug=slug,
            original_name=name,
            kind=kind,
            size=1,
            sha256="0" * 64,
            width=width,
            height=width,
            token=(slug[0].upper() * 43),
            uploaded_by_id=uuid4(),
        )
        for slug, name, kind, width in (
            ("guide", "guide.pdf", "pdf", None),
            ("picnic", "picnic.png", "png", 100),
        )
    }


def expanded(html, files, origin=ORIGIN):
    """Assert both the document link and the inline image were expanded."""
    assert f'href="{origin}/files/{files["guide"].token}"' in html
    assert f'<img src="{origin}/files/{files["picnic"].token}" alt="Picnic">' in html


@pytest.mark.parametrize(
    "role", [ServiceRole.WEB, ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH]
)
def test_each_rendering_role_expands_files_with_only_their_links(role):
    """Web, worker and mail dispatch resolve slugs; background roles see no more."""
    files = library()
    with task_login(role):
        links = links_for(ORIGIN, HTML)
        expanded(render_template(f"<p>{HTML}</p>", {}, html=True, files=links), files)
        if role is not ServiceRole.WEB:
            # Only the slug and token: not the name, uploader or digest.
            with pytest.raises(ProgrammingError), transaction.atomic():
                list(HostedFile.objects.values_list("original_name"))


def test_the_scheduler_cannot_read_hosted_files():
    """The scheduler renders nothing, so it has no hosted-file read."""
    library()
    with (
        task_login(ServiceRole.SCHEDULER),
        pytest.raises(ProgrammingError),
        transaction.atomic(),
    ):
        hosted_links(ORIGIN)


def test_family_pages_render_files_as_web(auth_service):
    """A Family page naming files renders under the web login."""
    files = library()
    store = auth_service.store
    result, _, _ = add_draft(store, store.active(), uuid4())
    assert result.state == "applied"
    campaign = Campaign.objects.get()
    page = content(str(campaign.pk), slot="login_help", html=f"<p>{HTML}</p>")
    page["values"]["text"] = "Guide"
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **page}],
        ).state
        == "applied"
    )
    campaign.refresh_from_db()
    with task_login(ServiceRole.WEB):
        pages = render_pages(
            campaign.active_configuration.configuration_id,
            campaign.active_configuration,
            {"login_help"},
            {},
        )
    expanded(pages["login_help"], files, origin="")


def template_with_files(harness, slot):
    """Select an email template for ``slot`` whose body names hosted files."""
    access = " {{ family_code }} {{ family_url }}" if slot == "initial" else ""
    template = content(
        str(harness.campaign.pk),
        kind="email",
        slot=slot,
        html=f"<p>{HTML}{access}</p>",
        text="Guide: {{ file.guide }}" + access,
    )
    patch = [{"operation": "add", "section": "content", **template}]
    if slot == "initial":
        patch.append(
            {
                "operation": "update",
                "section": "schedules",
                "id": str(ScheduleDefinition.objects.get().pk),
                "values": {
                    "template_version": template["id"],
                    "subject": template["values"]["subject"],
                },
            }
        )
    store = harness.service.store
    assert change(store, store.active(), uuid4(), patch).state == "applied"


def test_family_mail_prepared_by_worker_and_sent_by_mail_dispatch(
    family_mail,  # noqa: F811
):
    """An invitation naming files prepares as worker and dispatches as mail."""
    files = library()
    template_with_files(family_mail, "initial")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        # prepare() runs the real preparation handler as the worker login.
        message = prepare(family_mail)
        expanded(message.render.html, files)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            mail, *_ = begin_submission(
                message.pk,
                execution.claim,
                private=family_mail.rings.private,
                public_origin=ORIGIN,
            )
            expanded(mail.html, files)
            assert f"Guide: {ORIGIN}/files/{files['guide'].token}" in mail.text
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
    message.refresh_from_db()
    assert message.state == "delivered"


def test_receipt_naming_files_is_sent_by_mail_dispatch(live_response_service):
    """A confirmation naming files renders and sends under the mail login."""
    files = library()
    template_with_files(live_response_service, "confirmation")
    message = receipt(live_response_service, production=True)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        mail, *_ = begin_receipt(message, execution)
        expanded(mail.html, files, origin="https://parish.example.org")
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.state == "delivered"
