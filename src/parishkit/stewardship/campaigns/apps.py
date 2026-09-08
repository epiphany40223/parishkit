"""Django registration for the Stewardship campaigns boundary."""

from django.apps import AppConfig


class CampaignsConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.campaigns"
    label = "stewardship_campaigns"
