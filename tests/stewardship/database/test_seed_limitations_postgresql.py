"""The seeder's Limitations answers go through the real submission path (#2178).

``build_answers`` maps a timeline's Cannot attend and Cannot serve answers
onto the form's own projection; the Family form owner, and the database
guard behind it, must accept them as a browser submission. The payload shape
itself is tested without a database in ``test_seed_web.py``.
"""

import pytest

from parishkit.stewardship.responses.models import Submission

from .response_builders import activate_response_service
from .test_ministry_responses_postgresql import start
from .test_seed_proposed_member_postgresql import seed_submit

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_seeders_limitations_answers_are_accepted(response_service):
    """Cannot attend and one Member's Cannot serve are stored as answered."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    payload = seed_submit(harness, {"cannot_attend": True, "cannot_serve": True})
    ((member, entry),) = payload["service"]["members"].items()
    assert entry == {"cannot_serve": True}
    choices = payload["ministries"]["members"][member]
    assert choices["join"] == [] and choices["leave"]
    (submission,) = Submission.objects.all()
    assert submission.answers["cannot_attend"] is True
    assert submission.answers["service"]["members"][member]["cannot_serve"] is True
