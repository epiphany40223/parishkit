"""A page with the real chart component and engine, from synthetic metrics (#477).

The response dashboard (``response_dashboard_components``) is the first Admin
page with charts; this fixture isolates the engine, wrapping the shared
``components/chart.html`` include and the ``components/chart-scripts.html``
head include in the real base template. The charts are the funnel and
activity documents built from the response-metrics test rows, so what the
browser draws is what the unit tests pin.
"""

from dataclasses import replace

from django.template import engines

from parishkit.stewardship.reports.chart_specs import activity_chart, funnel_chart
from parishkit.stewardship.web.dates import using

from ..test_chart_specs import metrics

# A send name an Administrator could type, made of markup: the tooltip, the
# chart's text marks, the summary and the notes must all show it as text.
HOSTILE_NAME = '<img src=x onerror="window.__chartInjected=1">'

PAGE = """{% extends 'stewardship/admin-base.html' %}{% load i18n %}
{% block title %}Charts{% endblock %}
{% block head %}{% include 'stewardship/components/chart-scripts.html' %}{% endblock %}
{% block content %}
<h1>Response charts</h1>
{% for chart in charts %}<section class="panel">
{% include 'stewardship/components/chart.html' %}
</section>{% endfor %}
{% endblock %}"""


def hostile_metrics():
    """The test metrics with every send renamed to HOSTILE_NAME."""
    value = metrics()
    return replace(
        value, sends=tuple(replace(send, name=HOSTILE_NAME) for send in value.sends)
    )


def components(context, admin):
    """The pages, rendered with the Admin chrome like every report page.

    ``/charts`` holds the funnel and activity charts; ``/charts/hostile`` the
    activity chart with markup for its send names.
    """
    with using("us_long"):
        charts = [funnel_chart(metrics()), activity_chart(metrics())]
        hostile = [activity_chart(hostile_metrics())]
    page = engines["django"].from_string(PAGE)
    return {
        path: (
            "text/html",
            page.render(context | {"admin_chrome": admin, "charts": documents}),
        )
        for path, documents in (("/charts", charts), ("/charts/hostile", hostile))
    }
