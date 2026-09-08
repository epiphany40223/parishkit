"""Django registration for the Stewardship audit boundary."""

from django.apps import AppConfig


class AuditConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.audit"
    label = "stewardship_audit"
