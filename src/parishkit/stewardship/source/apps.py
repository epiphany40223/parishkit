"""Django registration for the Stewardship source boundary."""

from django.apps import AppConfig


class SourceConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.source"
    label = "stewardship_source"
