"""Django registration for the Stewardship reports boundary."""

from django.apps import AppConfig


class ReportsConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.reports"
    label = "stewardship_reports"
