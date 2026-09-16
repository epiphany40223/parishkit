"""Independent review regressions for Web intent versus provider authority."""

# ruff: noqa: F811 -- pytest injects the imported fixture by name.

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import delivery_resolution
from parishkit.stewardship.jobs.family_mail_credentials import seal_current_credentials
from parishkit.stewardship.jobs.family_mail_dispatch import begin_submission
from parishkit.stewardship.jobs.outbox_validation import RenderInput

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery, resolve
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_policy_postgresql import user

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "field", ["subject", "html", "text", "payload_digest", "sealed_substitutions"]
)
def test_web_preparation_is_never_provider_content_authority(
    family_mail, monkeypatch, field
):
    """Even correctly sealed forged content is rerendered by the isolated owner.

    Simulate a compromised Web producer, not merely a broken render checksum.
    Web can seal with the public key, but cannot choose provider content or
    transplant a ciphertext context across deliveries/Families.
    """
    principal = user("admin@example.org")
    original = delivery_resolution._prepare

    def forged(message, **keys):
        """Alter the actual Web INSERT payload after legitimate preparation."""
        values = original(message, **keys)
        if field == "payload_digest":
            values["render"][field] = "a" * 64
        elif field == "sealed_substitutions":
            values["sealed"][field] = keys["public"].encrypt(
                b"{}",
                context=b"a-different-family-and-delivery",
            )
        else:
            public = {
                key: value
                for key, value in values["render"].items()
                if key != "payload_digest"
            }
            for key in ("configuration_id", "template_id"):
                public[key] = UUID(public[key])
            render = replace(RenderInput(**public), **{field: "forged-mail-marker"})
            sealed = seal_current_credentials(
                identity=delivery_resolution._identity(message),
                render=render,
                campaign=Campaign.objects.get(pk=message.campaign_id),
                family=FamilyCampaign.objects.get(pk=message.family_id),
                general=keys["general"],
                public=keys["public"],
            )
            values = {
                "render": render.fields(),
                "sealed": sealed.fields(),
            }
            values = {
                kind: {
                    key: str(value) if isinstance(value, UUID) else value
                    for key, value in record.items()
                }
                for kind, record in values.items()
            }
        return values

    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        monkeypatch.setattr(delivery_resolution, "_prepare", forged)
        receipt = resolve(family_mail, principal, message, "resend")
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(SimpleNamespace(task_id=receipt.retry_task_id))
            if field == "sealed_substitutions":
                with pytest.raises(CryptographicError):
                    begin_submission(
                        message.pk,
                        execution.claim,
                        private=family_mail.rings.private,
                        public_origin="http://localhost:8000",
                    )
            else:
                mail, *_ = begin_submission(
                    message.pk,
                    execution.claim,
                    private=family_mail.rings.private,
                    public_origin="http://localhost:8000",
                )
                assert "forged-mail-marker" not in mail.subject + mail.html + mail.text
                assert family_mail.code in mail.text
        message.refresh_from_db()
        if field == "sealed_substitutions":
            assert message.state == "pending" and message.attempt == 1
        else:
            assert message.render.payload_digest != "a" * 64
