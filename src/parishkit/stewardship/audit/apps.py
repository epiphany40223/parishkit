"""Django registration for the Stewardship audit boundary."""

from django.apps import AppConfig


class AuditConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.audit"
    label = "stewardship_audit"

    def ready(self):
        """Let an index expression carry an operator class (#765).

        The System logs search's trigram indexes are declared as
        ``OpClass(expression, name="gin_trgm_ops")`` (``models``). Django
        renders that inside an index only when ``OpClass`` is registered as
        an index wrapper, which ``django.contrib.postgres``'s own app does;
        that app is not installed (it would also add lookups and a type
        query on every new connection), so this registers the wrapper alone,
        exactly as that app does.
        """
        from django.contrib.postgres.indexes import OpClass
        from django.db.models import OrderBy
        from django.db.models.functions import Collate
        from django.db.models.indexes import IndexExpression

        IndexExpression.register_wrappers(OrderBy, OpClass, Collate)
