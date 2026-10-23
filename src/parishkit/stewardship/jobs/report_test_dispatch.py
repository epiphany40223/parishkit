"""Mail dispatch of one report email test (#2036), from its kept email.

Send a test to me (``reports.report_email_test``) keeps the email it drew
on its ``ReportEmailTest``; the worker made one ``admin_report_test``
message from it. Dispatch rechecks the test's scope before each submission
and sends the kept email again on a retry, with its images, routed to the
Administrator (the Testing recipient in Testing). The database's write gate
repeats every check (``stewardship_report_email_test_dispatch_write_v1``,
frozen file 0173). A test has no occurrence and no pause hold: while
Production mail is paused it waits, and a lost scope cancels it.
"""

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.reports.report_email_models import ReportEmailTest

from .ownership import database_now

PURPOSE = "admin_report_test"
# The test's columns every login that dispatches or recovers it may read.
BINDING = ("id", "campaign_id", "mode", "testing_recipient", "state")


def bound_test_message(message):
    """The message's test, from opaque identity columns only.

    Scheduler recovery reads this too, so it never reads the kept email or
    the address.
    """
    require_work_order()
    if (
        message.purpose != PURPOSE
        or message.family_id is not None
        or message.credential_namespace != "none"
    ):
        raise PermissionError("Report email test delivery identity differs.")
    return ReportEmailTest.objects.only(*BINDING).get(
        pk=message.semantic_key, campaign_id=message.campaign_id, mode=message.mode
    )


def test_disposition(message, *, check_recipient=False):
    """Cancel a test that can never be sent; hold one only temporarily blocked.

    A changed campaign, mode or Testing recipient, a campaign that no
    longer sends, or (``check_recipient``: mail dispatch, which may read
    the address) an address no longer an Administrator's is
    "scope_replaced". A restore, go-live or work gate, paused Production
    mail and a retry not yet due hold it; a test is in no pause hold.
    """
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.reports.report_email_test import held, scope_lost

    from .family_mail_dispatch import FamilyDeliveryHeld

    bound_test_message(message)
    fields = BINDING + (("address",) if check_recipient else ())
    row = ReportEmailTest.objects.only(*fields).get(pk=message.semantic_key)
    if scope_lost(row, address=check_recipient):
        return "scope_replaced"
    if held(row):
        raise FamilyDeliveryHeld("Report email test awaits current campaign authority.")
    if message.mode == "production" and Campaign.objects.values_list(
        "delivery_paused", flat=True
    ).get(pk=row.campaign_id):
        raise FamilyDeliveryHeld("Mail is paused.")
    if message.not_before > database_now():
        raise FamilyDeliveryHeld("Report email test retry is not due.")
    return None


def current_test_content(message, scope):
    """The test's kept email, under the current sender, as its mode routes it."""
    from parishkit.stewardship.reports.report_email_test import (
        images_of,
        label_render,
    )

    from .report_dispatch import delivery_mail

    bound_test_message(message)
    row = ReportEmailTest.objects.get(pk=message.semantic_key)
    version = scope.runtime.active_configuration
    render = label_render(
        row,
        configuration_id=version.pk,
        email=AppliedIntegration.objects.get(configuration=version, kind="email"),
    )
    return render, None, delivery_mail(message.semantic_key, render, images_of(row))
