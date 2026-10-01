from __future__ import annotations

from dataclasses import replace

import pytest

from quayt_service.provision_demo import provision_demo, provision_demo_identity
from quayt_service.settings import Environment, SettingsError


def test_demo_provisioning_refuses_before_database_access(settings) -> None:
    assert settings.environment is Environment.TEST
    with pytest.raises(SettingsError, match="only in development"):
        provision_demo(settings, "demo-subject", None)  # type: ignore[arg-type]
    with pytest.raises(SettingsError, match="only in development"):
        provision_demo_identity(settings, "demo-subject", None)  # type: ignore[arg-type]


@pytest.mark.parametrize("subject", ["", " ", "x" * 513])
def test_demo_provisioning_validates_subject_before_database_access(settings, subject: str) -> None:
    development = replace(settings, environment=Environment.DEVELOPMENT)
    with pytest.raises(ValueError, match="subject"):
        provision_demo_identity(development, subject, None)  # type: ignore[arg-type]
