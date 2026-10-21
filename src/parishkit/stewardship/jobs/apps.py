"""Django registration for the Stewardship jobs boundary."""

from django.apps import AppConfig


class JobsConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.jobs"
    label = "stewardship_jobs"

    def ready(self):
        """Read System health's debug-off switch on every new connection (#530).

        Also make OpClass an index-expression wrapper, as
        django.contrib.postgres does when installed (this project does not
        install it, for its per-connection type lookups). Without it Django
        wraps "expression opclass" in parentheses, which PostgreSQL refuses;
        Sent mail's address index names jsonb_path_ops on an expression
        (outbox_models.OutboxRender, #1562).
        """
        from django.contrib.postgres.indexes import OpClass
        from django.db.models import OrderBy
        from django.db.models.functions import Collate
        from django.db.models.indexes import IndexExpression

        from parishkit.stewardship import debug_switch

        debug_switch.install()
        IndexExpression.register_wrappers(OrderBy, OpClass, Collate)
