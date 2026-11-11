"""Settings and the CS-owned tier rules. Tier mapping lives here, never in prompts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Tier(StrEnum):
    ENTERPRISE = "enterprise"
    GROWTH = "growth"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/app"
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
