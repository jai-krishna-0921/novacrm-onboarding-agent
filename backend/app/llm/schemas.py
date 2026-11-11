"""Structured outputs the LLM must return. The LLM fills these; code decides what to do with them."""

from typing import Literal

from pydantic import BaseModel, Field


class DealFields(BaseModel):
    customer_name: str | None = Field(None, description="Customer company name, exactly as written")
    customer_contact_name: str | None = Field(None, description="Customer contact person's name")
    customer_email: str | None = Field(None, description="Customer contact's email address, verbatim")
    ae_name: str | None = Field(None, description="Account Executive who closed the deal")
    ae_email: str | None = Field(None, description="AE's email address, verbatim")
    opportunity_id: str | None = Field(None, description="Salesforce Opportunity ID if present")
    deal_count: int = Field(1, description="Number of distinct customer deals announced in the email")
    notes: str | None = Field(None, description="Other onboarding-relevant details, one short sentence")


class ExtractedDeal(DealFields):
    """DealFields plus what code (never the LLM) pulls from the email: the LLM schema stays unchanged."""
    opportunity_url: str | None = None


class TranscriptReading(BaseModel):
    plan_tier: Literal["enterprise", "growth", "unclear"]
    ae_confirmed: bool = Field(description="True only if the AE explicitly confirmed the tier after read-back")
    reason: str = Field(description="One sentence citing the AE's words")


class WelcomeDraft(BaseModel):
    message: str
