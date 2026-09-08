"""Django registration for the Stewardship responses boundary."""

from django.apps import AppConfig


class ResponsesConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.responses"
    label = "stewardship_responses"
