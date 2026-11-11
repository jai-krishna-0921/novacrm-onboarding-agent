"""Settings and the CS-owned tier rules. Tier mapping lives here, never in prompts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import re

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Tier(StrEnum):
    ENTERPRISE = "enterprise"
    GROWTH = "growth"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/app"
    # Migration-validation source/destination (plan §12). Default: databases mig_source / mig_dest on the
    # same server as DATABASE_URL.
    mig_source_url: str = ""
    mig_dest_url: str = ""
    public_base_url: str = ""
    provider_mode: Literal["fake", "real"] = "fake"

    call_max_attempts: int = 3
    call_retry_delay_seconds: int = 30

    anthropic_api_key: str = ""
    llm_model: str = "claude-haiku-5-5"
    llm_judge_model: str = "claude-haiku-5-5"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    gmail_pubsub_topic: str = ""
    gmail_push_token: str = ""
    cs_inbox_address: str = ""

    retell_api_key: str = ""
    retell_agent_id: str = ""
    retell_from_number: str = ""

    rocketlane_api_key: str = ""
    rocketlane_base_url: str = "https://api.rocketlane.com/api/1.0"
    rocketlane_template_enterprise: str = ""
    rocketlane_template_growth: str = ""
    rocketlane_owner_email: str = ""
    rocketlane_pm_email: str = ""
    rocketlane_csm_enterprise: str = ""
    rocketlane_csm_growth: str = ""

    demo_ae_name: str = ""
    demo_ae_email: str = ""
    demo_ae_phone: str = ""

    slack_bot_token: str = ""
    slack_internal_member_ids: str = ""

    @field_validator("retell_from_number", "demo_ae_phone")
    @classmethod
    def _e164(cls, v: str) -> str:
        """Accept dashboard formatting ('+1 (510) 822-8650') but store strict E.164; Retell 404s otherwise."""
        v = v.strip()
        if not v:
            return v
        if not re.fullmatch(r"\+[\d\s().-]+", v):
            raise ValueError("phone number must be E.164, e.g. +15108228650")
        digits = re.sub(r"\D", "", v)
        if not 8 <= len(digits) <= 15:
            raise ValueError("phone number must have 8-15 digits")
        return "+" + digits

    @model_validator(mode="after")
    def _migration_urls(self) -> "Settings":
        def sibling(db: str) -> str:
            return make_url(self.database_url).set(database=db).render_as_string(hide_password=False)

        self.mig_source_url = _psycopg(self.mig_source_url or sibling("mig_source"))
        self.mig_dest_url = _psycopg(self.mig_dest_url or sibling("mig_dest"))
        return self


def _psycopg(url: str) -> str:
    """`postgresql://` would pick psycopg2, which isn't installed; use psycopg 3."""
    return "postgresql+psycopg://" + url.removeprefix("postgresql://") if url.startswith("postgresql://") else url


@dataclass(frozen=True)
class TierRule:
    tier: Tier
    label: str
    template_id: str
    duration_days: int
    csm_model: Literal["dedicated", "pooled"]
    csm_email: str


def tier_rules(s: Settings) -> dict[Tier, TierRule]:
    return {
        Tier.ENTERPRISE: TierRule(Tier.ENTERPRISE, "Enterprise", s.rocketlane_template_enterprise,
                                  30, "dedicated", s.rocketlane_csm_enterprise),
        Tier.GROWTH: TierRule(Tier.GROWTH, "Growth", s.rocketlane_template_growth,
                              14, "pooled", s.rocketlane_csm_growth),
    }


# Template tasks are assigned to these Rocketlane role placeholders (docs/ROCKETLANE_SETUP.md). The 1-day overdue
# automation notifies "Project Manager", so it must be filled on every project.
PLACEHOLDER_PM, PLACEHOLDER_CSM, PLACEHOLDER_CUSTOMER = "Project Manager", "Customer success", "Customer"


def role_placeholders(pm_email: str, csm_email: str, customer_email: str) -> dict[str, str]:
    m = {PLACEHOLDER_PM: pm_email, PLACEHOLDER_CSM: csm_email, PLACEHOLDER_CUSTOMER: customer_email}
    return {k: v for k, v in m.items() if v}
