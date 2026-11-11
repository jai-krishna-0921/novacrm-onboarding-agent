"""Agent 2 - Communication: personalized Slack channel, topic, invites and welcome message."""

import json

from langgraph.graph import END, START, StateGraph

from app.config import Tier, TierRule
from app.graph.state import Deps, OnboardingState
from app.guardrails.output import channel_name, check_welcome
from app.llm import versions as prompts
from app.llm.client import LLMError
from app.llm.schemas import WelcomeDraft
from app.tools.registry import ToolFailed

AGENT = "comms"


def welcome_facts(s: OnboardingState, rule: TierRule) -> dict:
    x = s["extracted"]
    return {"customer_name": x["customer_name"], "contact_name": x.get("customer_contact_name"),
            "plan": rule.label, "duration_days": rule.duration_days, "csm_model": rule.csm_model}


def static_welcome(f: dict) -> str:
    hello = f"Hi {f['contact_name'].split()[0]}, welcome" if f.get("contact_name") else "Welcome"
    return (f"{hello} to NovaCRM, {f['customer_name']}! :wave:\n"
            f"This channel is home for your {f['duration_days']}-day onboarding on the {f['plan']} plan. "
            f"Your {f['csm_model']} Customer Success Manager will guide you through four phases: "
            f"Kickoff, Data Migration, Configuration and Go-Live.\n"
            f"Next step: we'll reach out here to schedule your kickoff call.")


def topic(s: OnboardingState, rule: TierRule) -> str:
    return (f"{s['extracted']['customer_name']} | {rule.label} | {rule.duration_days}-day onboarding | "
            f"{rule.csm_model} CSM")[:250]


def build_comms(d: Deps):
    def entry(s: OnboardingState) -> str:
        return "setup_channel" if s.get("welcome_message") else "draft_welcome"

    def draft_welcome(s: OnboardingState) -> dict:
        rule = d.rules[Tier(s["plan_tier"])]
        facts = welcome_facts(s, rule)
        problems: list[str] = []
        for attempt in (1, 2):
            try:
                draft = d.llm.parse(run_id=s["run_id"], agent=AGENT, node="draft_welcome", prompt=prompts.WELCOME,
                                    user=json.dumps(facts), schema=WelcomeDraft).message
            except LLMError as e:
                problems = [f"LLM unavailable: {e}"]
                break
            problems = check_welcome(draft, facts["customer_name"], rule)
            if not problems:
                d.audit(s, AGENT, "draft_welcome", f"LLM draft passed fact checks (attempt {attempt})",
                        output={"message": draft})
                return {"welcome_message": draft}
            d.audit(s, AGENT, "check_welcome", f"draft rejected: {problems}", status="blocked",
                    output={"message": draft})
        fallback = static_welcome(facts)
        d.audit(s, AGENT, "draft_welcome", f"no acceptable LLM draft ({problems}); using static template",
                output={"message": fallback}, status="fallback")
        return {"welcome_message": fallback}

    def setup_channel(s: OnboardingState) -> dict:
        tier, rid = Tier(s["plan_tier"]), s["run_id"]
        rule = d.rules[tier]
        call = d.registry.call
        try:
            ch = call(AGENT, "slack_create_channel", {"name": channel_name(s["extracted"]["customer_name"], tier)},
                      run_id=rid, idempotency_key=f"channel:{rid}", rationale="tier-personalized channel name")
            cid = ch.channel_id
            call(AGENT, "slack_set_topic", {"channel_id": cid, "topic": topic(s, rule)}, run_id=rid,
                 idempotency_key=f"topic:{rid}")
            internal = [u for u in d.settings.slack_internal_member_ids.split(",") if u.strip()]
            if internal:
                call(AGENT, "slack_invite_internal", {"channel_id": cid, "user_ids": internal}, run_id=rid,
                     idempotency_key=f"invite:{rid}")
            shared = call(AGENT, "slack_invite_shared", {"channel_id": cid, "email": s["extracted"]["customer_email"]},
                          run_id=rid, idempotency_key=f"shared:{rid}")
            if shared.status != "sent":
                d.audit(s, AGENT, "slack_connect", f"Slack Connect unavailable ({shared.detail}); "
                        "channel kept internal - CSM must invite the customer manually", status="degraded")
            call(AGENT, "slack_post_message", {"channel_id": cid, "text": s["welcome_message"]}, run_id=rid,
                 idempotency_key=f"welcome:{rid}")
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"Slack setup failed ({e.tool}: {e.reason}); Rocketlane project kept", "system")
        d.audit(s, AGENT, "setup_channel", "channel ready", output={"channel_id": cid})
        return {"channel_id": cid}

    g = StateGraph(OnboardingState)
    g.add_node("draft_welcome", draft_welcome)
    g.add_node("setup_channel", setup_channel)
    g.add_conditional_edges(START, entry, ["draft_welcome", "setup_channel"])
    g.add_edge("draft_welcome", "setup_channel")
    g.add_edge("setup_channel", END)
    return g.compile()
