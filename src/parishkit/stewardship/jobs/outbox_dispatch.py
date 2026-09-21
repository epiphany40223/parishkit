"""Route one closed outbox Task type to independently admitted purpose owners."""

from parishkit.stewardship.campaigns.work_locks import work_transaction

from .dispatch import Handler
from .family_mail_delivery_tasks import delivery_handler as campaign_handler
from .operational_mail_tasks import delivery_handler as alert_handler
from .operational_owner import OPERATIONAL
from .outbox_models import OutboxMessage
from .ownership import lock_task_claim
from .queues import WorkQueue
from .security_owner import SECURITY
from .storage import _status


def delivery_handler(
    store, *, private=None, public_origin=None, credential_path=None, scheduler=False
):
    """Stored purpose selects a verifier; it never replaces that verifier's proof."""
    campaign = campaign_handler(
        store,
        private=private,
        public_origin=public_origin,
        credential_path=credential_path,
        scheduler=scheduler,
    )
    # Each Administrator-routed owner keeps its own cohort, content and SQL
    # admission; the purpose only names which compiled owner must prove it.
    alerts = {
        owner.purpose: alert_handler(
            store, credential_path=credential_path, scheduler=scheduler, owner=owner
        )
        for owner in (OPERATIONAL, SECURITY)
    }

    def owner(status):
        """Do not accept a purpose or recipient from a broker message or caller."""
        purpose = (
            OutboxMessage.objects.only("purpose")
            .get(pk=status.domain_request_id, task_id=status.root_id)
            .purpose
        )
        return alerts.get(purpose, campaign)

    def admit(action, status):
        """Recheck purpose ownership on every hint, claim, effect and transition."""
        return owner(status).admit(action, status)

    def recover(status):
        """The compiled owner retains its own uncertainty and deadline semantics."""
        return owner(status).recover(status)

    def execute(execution):
        """Choose under the live claim, then release SQL locks before provider IO."""
        with work_transaction():
            selected = owner(_status(lock_task_claim(execution.claim)))
        return selected.execute(execution)

    return Handler(
        WorkQueue.MAIL, admit, execute, recover=recover, scope=work_transaction
    )
