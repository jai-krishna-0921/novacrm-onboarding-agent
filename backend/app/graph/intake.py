"""Agent 1 - Intake & Routing: extract -> validate -> look up AE -> call -> confirm tier -> project."""

import re

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.config import Tier
from app.graph.state import Deps, OnboardingState, customer_key
from app.guardrails.decision import CallOutcome, CallResult, decide_tier
from app.guardrails.input import validate_deal
from app.llm.schemas import DealFields, TranscriptReading
from app.tools.registry import ToolFailed

AGENT = "intake"
OPP_RE = re.compile(r"/Opportunity/([A-Za-z0-9]{15,18})")


def build_intake(d: Deps):
    def entry(s: OnboardingState) -> str:
        if s.get("plan_tier") and not s.get("project_id"):
            return "check_duplicate"
        if s.get("ae") and not s.get("plan_tier"):
            return "place_call"
        return "extract_fields"

    def extract_fields(s: OnboardingState) -> dict:
        email = s["email"]
        user = (f"<email>\nFrom: {email['sender']}\nSubject: {email['subject']}\n\n"
                f"{email['body']}\n</email>")
        fields = d.llm.parse(run_id=s["run_id"], agent=AGENT, node="extract_fields",
                             prompt="extract_v1", user=user, schema=DealFields)
        rationale = "LLM structured extraction (email treated as untrusted data)"
        if m := OPP_RE.search(email["body"]):
            if fields.opportunity_id != m.group(1):
                rationale += f"; opportunity id overridden by regex ({fields.opportunity_id!r} -> {m.group(1)})"
            fields.opportunity_id = m.group(1)
        d.audit(s, AGENT, "extract_fields", rationale, input={"subject": email["subject"]},
                output=fields.model_dump())
        return {"extracted": fields.model_dump()}

    def validate(s: OnboardingState) -> dict:
        fields = DealFields(**s["extracted"])
        issues = validate_deal(fields)
        if fields.deal_count != 1:
            d.audit(s, AGENT, "validate", f"email announces {fields.deal_count} deals; one deal per email",
                    status="escalated")
            return d.escalate(s, AGENT, f"Email announces {fields.deal_count} deals; needs a human to split")
        d.audit(s, AGENT, "validate", "all required fields present and valid" if not issues
                else f"cannot proceed: {issues}", output={"issues": issues},
                status="ok" if not issues else "blocked")
        return {"issues": issues}

    def request_clarification(s: OnboardingState) -> dict:
        email, issues = s["email"], s["issues"]
        body = ("Hi,\n\nThanks for the deal notification. Before we can start onboarding, "
                "please reply with the following:\n\n" + "\n".join(f"- {i}" for i in issues) +
                "\n\nWe have not created anything yet and will not guess these values.\n\n"
                "NovaCRM Onboarding")
        try:
            d.registry.call(AGENT, "gmail_reply", dict(
                thread_id=email["thread_id"], rfc_message_id=email["rfc_message_id"], to=email["sender"],
                subject=f"Re: {email['subject']}", body=body),
                run_id=s["run_id"], idempotency_key=f"clarify:{s['run_id']}",
                rationale=f"missing/invalid fields: {issues}")
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"Could not email the AE for clarification: {e.reason}")
        return d.set_status(s, "AWAITING_CLARIFICATION")

    def lookup_ae(s: OnboardingState) -> dict:
        sender = s["email"]["sender"]
        who = d.registry.call(AGENT, "ae_lookup", {"email": sender}, run_id=s["run_id"])
        if not (who.found and who.active):
            return d.escalate(s, AGENT, f"Email sender {sender} is not an active AE in the directory")
        ae_email = s["extracted"]["ae_email"]
        ae = who if ae_email.casefold() == sender.casefold() else \
            d.registry.call(AGENT, "ae_lookup", {"email": ae_email}, run_id=s["run_id"])
        if not (ae.found and ae.active and ae.phone):
            return d.escalate(s, AGENT, f"AE {ae_email} not found/active or has no phone number")
        d.audit(s, AGENT, "lookup_ae", f"AE {ae.name} found; will call to confirm tier")
        return {"ae": ae.model_dump()}

    def place_call(s: OnboardingState) -> dict:
        attempt = s.get("call_attempts", 0) + 1
        if attempt > 1:
            d.sleep(d.settings.call_retry_delay_seconds)
        try:
            out = d.registry.call(AGENT, "voice_start_call", dict(
                to_number=s["ae"]["phone"], ae_name=s["ae"]["name"],
                customer_name=s["extracted"]["customer_name"], run_id=s["run_id"], attempt=attempt),
                run_id=s["run_id"], idempotency_key=f"call:{s['run_id']}:{attempt}:{s.get('escalation_count', 0)}",
                rationale=f"tier confirmation call, attempt {attempt}")
        except ToolFailed as e:
            return {**d.escalate(s, AGENT, f"Could not place call to AE: {e.reason}"), "call_attempts": attempt}
        return {"call_attempts": attempt, "provider_call_id": out.provider_call_id,
                **d.set_status(s, "AWAITING_CALL")}

    def await_call(s: OnboardingState) -> dict:
        # Only the interrupt lives here: this node re-runs on resume, so it must have no side effects.
        result = interrupt({"type": "call", "provider_call_id": s["provider_call_id"],
                            "attempt": s["call_attempts"]})
        return {"call_result": CallResult.model_validate(result).model_dump()}

    def interpret_call(s: OnboardingState) -> dict:
        result = CallResult.model_validate(s["call_result"])
        cross = None
        if result.status == "connected" and result.transcript.strip():
            reading = d.llm.parse(run_id=s["run_id"], agent=AGENT, node="interpret_call",
                                  prompt="interpret_v1", user=f"<transcript>\n{result.transcript}\n</transcript>",
                                  schema=TranscriptReading)
            cross = reading.plan_tier if reading.ae_confirmed else "unclear"
        decision = decide_tier(result, cross)
        d.audit(s, AGENT, "decide_tier", decision.rationale, status=decision.outcome.value,
                input={"attempt": s["call_attempts"], "status": result.status,
                       "provider_tier": result.plan_tier, "confirmed": result.confirmed},
                output={"outcome": decision.outcome.value, "tier": decision.tier, "cross_check": cross})
        if decision.outcome == CallOutcome.CONFIRMED:
            return {"plan_tier": decision.tier.value, "tier_source": "ae_call",
                    **d.set_status(s, "TIER_CONFIRMED", plan_tier=decision.tier.value)}
        if s["call_attempts"] >= d.settings.call_max_attempts:
            return d.escalate(s, AGENT, f"No clear plan tier after {s['call_attempts']} call attempts "
                                        f"(last: {decision.rationale})")
        return {"last_call_rationale": decision.rationale}

    def check_duplicate(s: OnboardingState) -> dict:
        x = s["extracted"]
        key = customer_key(x["customer_name"])
        d.store.upsert_run(s["run_id"], opportunity_id=x.get("opportunity_id"), customer_key=key,
                           customer_name=x["customer_name"])
        if other := d.store.find_active_run(opportunity_id=x.get("opportunity_id"), customer_key=key,
                                            exclude_run_id=s["run_id"]):
            return d.escalate(s, AGENT, f"Existing onboarding run {other['id']} already has a project "
                                        f"for this customer/opportunity")
        try:
            found = d.registry.call(AGENT, "rocketlane_find_project", dict(
                customer_name=x["customer_name"], external_reference_id=_ext_ref(s)), run_id=s["run_id"])
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"Rocketlane API unavailable during duplicate check: {e.reason}")
        if found.project_id:
            return d.escalate(s, AGENT, f"Existing Rocketlane project {found.project_id} for this customer")
        d.audit(s, AGENT, "check_duplicate", "no existing run or Rocketlane project")
        return {}

    def create_project(s: OnboardingState) -> dict:
        x, rule = s["extracted"], d.rules[Tier(s["plan_tier"])]
        start = d.today().isoformat()
        args = dict(
            project_name=f"{x['customer_name']} Onboarding ({rule.label}, {rule.duration_days}-day)",
            customer_name=x["customer_name"], owner_email=d.settings.rocketlane_owner_email,
            start_date=start, template_id=rule.template_id,
            member_emails=[e for e in (d.settings.rocketlane_pm_email, rule.csm_email) if e],
            customer_contact_email=x["customer_email"], external_reference_id=_ext_ref(s))
        try:
            out = d.registry.call(AGENT, "rocketlane_create_project", args, run_id=s["run_id"],
                                  idempotency_key=f"project:{s['run_id']}",
                                  rationale=f"tier {rule.tier} ({s['tier_source']}) -> template "
                                            f"{rule.template_id}, {rule.duration_days} days, {rule.csm_model} CSM")
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"Rocketlane API unavailable, project not created: {e.reason}")
        d.audit(s, AGENT, "create_project", f"created from {rule.label} template",
                output={"project_id": out.project_id})
        return {"project_id": out.project_id,
                **d.set_status(s, "PROJECT_CREATED", rocketlane_project_id=out.project_id)}

    def escalated(s):
        return END if s.get("escalation_reason") else None

    g = StateGraph(OnboardingState)
    for fn in (extract_fields, validate, request_clarification, lookup_ae, place_call, await_call,
               interpret_call, check_duplicate, create_project):
        g.add_node(fn.__name__, fn)
    g.add_conditional_edges(START, entry, ["extract_fields", "place_call", "check_duplicate"])
    g.add_edge("extract_fields", "validate")
    g.add_conditional_edges("validate", lambda s: escalated(s) or (
        "request_clarification" if s["issues"] else "lookup_ae"),
        [END, "request_clarification", "lookup_ae"])
    g.add_edge("request_clarification", END)
    g.add_conditional_edges("lookup_ae", lambda s: escalated(s) or "place_call", [END, "place_call"])
    g.add_conditional_edges("place_call", lambda s: escalated(s) or "await_call", [END, "await_call"])
    g.add_edge("await_call", "interpret_call")
    g.add_conditional_edges("interpret_call", lambda s: escalated(s) or (
        "check_duplicate" if s.get("plan_tier") else "place_call"),
        [END, "check_duplicate", "place_call"])
    g.add_conditional_edges("check_duplicate", lambda s: escalated(s) or "create_project",
                            [END, "create_project"])
    g.add_edge("create_project", END)
    return g.compile()


def _ext_ref(s: OnboardingState) -> str:
    return s["extracted"].get("opportunity_id") or f"run:{s['run_id']}"
