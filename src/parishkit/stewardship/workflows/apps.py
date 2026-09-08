"""Django registration for the Stewardship workflows boundary."""

from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.workflows"
    label = "stewardship_workflows"
