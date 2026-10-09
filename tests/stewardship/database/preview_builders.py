"""Age signed previews past their lifetime and check the out-of-date refusal."""

from contextlib import contextmanager
from time import time
from types import SimpleNamespace

from django.core import signing

OUT_OF_DATE = "This preview is out of date."


@contextmanager
def previews_aged(monkeypatch, seconds=301):
    """Make every signature look ``seconds`` old to Django's signing clock.

    Only ``signing``'s own clock moves, so sessions, sign-in freshness and the
    database clock stay current and only the preview lifetime can refuse.
    """
    with monkeypatch.context() as patch:
        patch.setattr(signing, "time", SimpleNamespace(time=lambda: time() + seconds))
        yield


def assert_out_of_date(response, link):
    """A 409 "This preview is out of date" refusal linking back to ``link``."""
    assert response.status_code == 409, response.content
    refusal = response.json()["refusal"]
    assert refusal["message"] == OUT_OF_DATE, refusal
    assert refusal["link"] == {"url": link, "label": "Review the changes again"}
