"""Carry the exact maintained execution's claimed identity into SQL effects.

The transaction-local setting is evidence, not authority. Every guarded write
compares it with the durable task's current run, worker, fence and unexpired
lease. A caller cannot revive stale evidence merely because a replacement run
now owns the same preparation. The setting grants no role or table privileges.
"""

import json
from contextlib import contextmanager

from django.db import connection, transaction

from parishkit.stewardship.jobs.ownership import lock_task_claim

from .work_locks import require_work_order

SETTING = "parishkit.production_token_claim"


@contextmanager
def token_claim(claim):
    """Fence and scope SQL evidence to this effect, restoring any enclosing value."""
    require_work_order()
    lock_task_claim(claim)
    value = json.dumps(
        {"run": str(claim.run_id), "fence": claim.fence, "worker": str(claim.worker_id)}
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s,true)", [SETTING])
        previous = cursor.fetchone()[0] or ""
        cursor.execute("SELECT set_config(%s,%s,true)", [SETTING, value])
        yield
        # On an exception, atomic rolls back the savepoint and its local setting.
        # Do not issue cleanup SQL in that possibly broken transaction. On the
        # successful path restore any enclosing owner before returning control.
        if not connection.needs_rollback:
            cursor.execute("SELECT set_config(%s,%s,true)", [SETTING, previous])
