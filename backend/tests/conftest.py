import pytest

from app.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        provider_mode="fake",
        rocketlane_template_enterprise="tmpl-ent",
        rocketlane_template_growth="tmpl-growth",
        rocketlane_owner_email="owner@novacrm.example",
        rocketlane_pm_email="pm@novacrm.example",
        rocketlane_csm_enterprise="csm-dedicated@novacrm.example",
        rocketlane_csm_growth="csm-pool@novacrm.example",
        call_max_attempts=3,
        call_retry_delay_seconds=0,
    )
