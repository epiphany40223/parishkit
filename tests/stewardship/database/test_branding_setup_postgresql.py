"""Initial setup logos retain the original attempt/session and expire with it."""

from io import BytesIO

import pytest
from PIL import Image

from parishkit.stewardship.accounts.branding_models import BrandingBundle
from parishkit.stewardship.accounts.branding_staging import (
    stage_branding,
    staged_bundle,
)
from parishkit.stewardship.accounts.setup_staging import begin_setup, cancel_setup
from parishkit.stewardship.web.content import prepare_graphics

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def graphics():
    """Use the production normalizer, with no original file on disk."""
    image = BytesIO()
    Image.new("RGB", (50, 30), "blue").save(image, format="PNG")
    image.seek(0)
    return prepare_graphics(image)


def test_setup_logo_cannot_be_adopted_and_cancelled_attempt_cannot_use_it(
    request, tmp_path
):
    """No configuration marker or Parish is created just by staging a logo."""
    service = request.getfixturevalue("setup_service")
    owner = login(service)
    attempt = begin_setup(owner, service)
    media = tmp_path / "media"
    media.mkdir(mode=0o700)
    identifier = stage_branding(
        owner,
        service,
        media,
        graphics(),
        base_digest=service.store.active().digest,
        setup_attempt_id=attempt.attempt_id,
    )
    row, assets = staged_bundle(
        owner, service, identifier, setup_attempt_id=attempt.attempt_id
    )
    assert row.setup_attempt_id == attempt.attempt_id and len(assets) == 4
    assert not service.configured()
    newcomer = login(service)
    with pytest.raises(PermissionError):
        staged_bundle(
            newcomer, service, identifier, setup_attempt_id=attempt.attempt_id
        )
    cancel_setup(owner, service, attempt.attempt_id)
    with pytest.raises(PermissionError):
        staged_bundle(owner, service, identifier, setup_attempt_id=attempt.attempt_id)
    from parishkit.stewardship.accounts.branding_cleanup import (
        TASK_TYPE,
        cleanup_handler,
    )
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.jobs.dispatch import WorkQueue, execute_hint

    from .test_background_grants_postgresql import task_login
    from .test_branding_cleanup_postgresql import produce

    tasks = produce()
    assert len(tasks) == 1 and tasks[0].domain_request_id == identifier
    with task_login(ServiceRole.WORKER, reconnect=True):
        execute_hint(
            tasks[0].run_id,
            queue=WorkQueue.GENERAL,
            worker_id=identifier,
            handlers={TASK_TYPE: cleanup_handler(media)},
        )
    assert BrandingBundle.objects.get(pk=identifier).state == "scrubbed"
    assert not (media / "branding" / identifier.hex).exists()


def test_setup_identifier_and_ready_state_do_not_bypass_admission(request, tmp_path):
    """An arbitrary attempt string is never interpreted as an owner or filepath."""
    service = request.getfixturevalue("setup_service")
    owner = login(service)
    media = tmp_path / "media"
    media.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        stage_branding(
            owner,
            service,
            media,
            graphics(),
            base_digest=service.store.active().digest,
            setup_attempt_id="forged",
        )
    assert not BrandingBundle.objects.exists()
