"""Agent 1 - Intake & Routing: extract -> validate -> look up AE -> call -> confirm tier -> project."""

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.config import PLACEHOLDER_CSM, PLACEHOLDER_CUSTOMER, PLACEHOLDER_PM, Tier, role_placeholders
from app.graph.state import Deps, OnboardingState
from app.guardrails.decision import CallOutcome, CallResult, decide_tier
from app.guardrails.input import find_opportunity_url, opportunity_id_from_url, validate_deal
from app.llm import versions as prompts
from app.llm.client import LLMError
from app.llm.schemas import DealFields, ExtractedDeal, TranscriptReading
from app.services.keys import customer_key, opportunity_key
from app.tools.registry import ToolFailed

AGENT = "intake"


def build_intake(d: Deps):
    def entry(s: OnboardingState) -> str:
        if s.get("plan_tier") and not s.get("project_id"):
            return "check_duplicate"
        if s.get("ae") and not s.get("plan_tier"):
            return "place_call"
        return "extract_fields" if s.get("sender_ok") else "check_sender"

    def check_sender(s: OnboardingState) -> dict:
        # Before any LLM call or reply: only active AEs can trigger onboarding (no auto-replies to outsiders).
        # A From header is trivially forged, so Gmail must first have authenticated it (DMARC / aligned DKIM).
        sender = s["email"]["sender"]
        if not s["email"].get("sender_authenticated"):
            return d.escalate(s, AGENT, f"Sender authentication failed for {sender} (possible spoofing): "
                                        f"{s['email'].get('auth_detail') or 'no verdict'}", "input")
        try:
            who = d.registry.call(AGENT, "ae_lookup", {"email": sender}, run_id=s["run_id"])
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"AE directory unavailable: {e.reason}", "system")
        if not (who.found and who.active):
            return d.escalate(s, AGENT, f"Email sender {sender} is not an active AE in the directory", "input")
        d.audit(s, AGENT, "check_sender", f"sender {sender} is an active AE")
        return {"sender_ok": True}

    def extract_fields(s: OnboardingState) -> dict:
        email = s["email"]
        user = (f"<email>\nFrom: {email['sender']}\nSubject: {email['subject']}\n\n"
                f"{email['body']}\n</email>")
        try:
            fields = d.llm.parse(run_id=s["run_id"], agent=AGENT, node="extract_fields",
                                 prompt=prompts.EXTRACT, user=user, schema=DealFields)
        except LLMError as e:
            return d.escalate(s, AGENT, f"Could not read the deal email (LLM unavailable): {e}", "system")
        rationale = "LLM structured extraction (email treated as untrusted data)"
        deal = ExtractedDeal(**fields.model_dump(), opportunity_url=find_opportunity_url(email["body"]))
        if opp := opportunity_id_from_url(deal.opportunity_url):
            if deal.opportunity_id != opp:
                rationale += f"; opportunity id taken from the Salesforce link ({deal.opportunity_id!r} -> {opp})"
            deal.opportunity_id = opp
        deal.opportunity_id = opportunity_key(deal.opportunity_id)
        d.audit(s, AGENT, "extract_fields", rationale, input={"subject": email["subject"]},
                output=deal.model_dump())
        return {"extracted": deal.model_dump()}

    def validate(s: OnboardingState) -> dict:
        fields = ExtractedDeal(**s["extracted"])
        issues = validate_deal(fields)
        if fields.deal_count != 1:
            d.audit(s, AGENT, "validate", f"email announces {fields.deal_count} deals; one deal per email",
                    status="escalated")
            return d.escalate(s, AGENT, f"Email announces {fields.deal_count} deals; needs a human to split", "input")
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
                subject=f"Re: {email['subject']}", body=body,
                message_id_header=f"<clarify-{s['run_id']}@{_mail_domain(d)}>"),
                run_id=s["run_id"], idempotency_key=f"clarify:{s['run_id']}",
                rationale=f"missing/invalid fields: {issues}")
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"Could not email the AE for clarification: {e.reason}", "system")
        return d.set_status(s, "AWAITING_CLARIFICATION")

    def lookup_ae(s: OnboardingState) -> dict:
        ae_email = s["extracted"]["ae_email"]
        try:
            ae = d.registry.call(AGENT, "ae_lookup", {"email": ae_email}, run_id=s["run_id"])
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"AE directory unavailable: {e.reason}", "system")
        if not (ae.found and ae.active and ae.phone):
            return d.escalate(s, AGENT, f"AE {ae_email} not found/active or has no phone number", "input")
        d.audit(s, AGENT, "lookup_ae", f"AE {ae.name} found; will call to confirm tier")
        return {"ae": ae.model_dump()}

    def precheck_duplicate(s: OnboardingState) -> dict:
        # The opportunity is known from the email: never phone the AE about a deal that is already onboarded.
        # check_duplicate runs again after the call (another run may have created a project meanwhile).
        return _duplicate(s, "precheck_duplicate") or {}

    def place_call(s: OnboardingState) -> dict:
        attempt = s.get("call_attempts", 0) + 1
        key = f"call:{s['run_id']}:{attempt}:{s.get('escalation_count', 0)}"
        try:
            out = d.registry.call(AGENT, "voice_start_call", dict(
                to_number=s["ae"]["phone"], ae_name=s["ae"]["name"],
                customer_name=s["extracted"]["customer_name"], run_id=s["run_id"], attempt=attempt,
                idempotency_key=key.replace(":", "-")),
                run_id=s["run_id"], idempotency_key=key,
                rationale=f"tier confirmation call, attempt {attempt}")
        except ToolFailed as e:
            return {**d.escalate(s, AGENT, f"Could not place call to AE: {e.reason}", "tier"), "call_attempts": attempt}
        return {"call_attempts": attempt, "provider_call_id": out.provider_call_id,
                **d.set_status(s, "AWAITING_CALL")}

    def await_call(s: OnboardingState) -> dict:
        # Only interrupts live here: this node re-runs on resume, so it must have no side effects.
        # A result for any other call (late webhook from an earlier attempt, misrouting) is ignored
        # and we keep waiting for the pending call.
        expected = s["provider_call_id"]
        while True:
            raw = interrupt({"type": "call", "provider_call_id": expected, "attempt": s["call_attempts"]})
            if isinstance(raw, dict) and raw.get("provider_call_id") == expected:
                return {"call_result": CallResult.model_validate(raw).model_dump()}

    def interpret_call(s: OnboardingState) -> dict:
        result = CallResult.model_validate(s["call_result"])
        cross = None
        if result.status == "connected" and result.transcript.strip():
            try:
                reading = d.llm.parse(run_id=s["run_id"], agent=AGENT, node="interpret_call",
                                      prompt=prompts.INTERPRET, user=f"<transcript>\n{result.transcript}\n</transcript>",
                                      schema=TranscriptReading)
            except LLMError as e:
                # The AE may well have confirmed; a human reviews the transcript and can set the tier.
                return d.escalate(s, AGENT, f"Transcript cross-check unavailable ({e}); provider says "
                                            f"tier={result.plan_tier}, confirmed={result.confirmed}. Review the "
                                            f"call and set the tier.", "tier")
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
                                        f"(last: {decision.rationale})", "tier")
        delay = d.settings.call_retry_delay_seconds
        if delay <= 0:
            return {"last_call_rationale": decision.rationale}
        now = d.clock()
        return {"last_call_rationale": decision.rationale, "retry_at": now + delay, "retry_since": now,
                **d.set_status(s, "AWAITING_RETRY")}

    def wait_retry(s: OnboardingState) -> dict:
        # Durable timer: pause until retry_at (a scheduler resumes us). Interrupt only, no side effects.
        if s.get("retry_at"):
            interrupt({"type": "wait", "since": s.get("retry_since"), "until": s["retry_at"],
                       "attempt": s["call_attempts"]})
        return {"retry_at": None}

    def _duplicate(s: OnboardingState, step: str) -> dict | None:
        x = s["extracted"]
        key = customer_key(x["customer_name"])
        d.store.upsert_run(s["run_id"], opportunity_id=x.get("opportunity_id"), customer_key=key,
                           customer_name=x["customer_name"])
        if other := d.store.find_active_run(opportunity_id=x.get("opportunity_id"), customer_key=key,
                                            exclude_run_id=s["run_id"]):
            return d.escalate(s, AGENT, f"Existing onboarding run {other['id']} already has a project "
                                        f"for this customer/opportunity", "duplicate")
        if d.store.peek_idem(f"project:{s['run_id']}"):
            # This run's own create was interrupted: the project Rocketlane may show is ours, and
            # create_project's reconcile lookup adopts it instead of creating a second one.
            d.audit(s, AGENT, step, "this run's earlier project create was interrupted; create_project reconciles")
            return None
        try:
            found = d.registry.call(AGENT, "rocketlane_find_project", dict(
                customer_name=x["customer_name"], external_reference_id=_ext_ref(s)), run_id=s["run_id"])
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"Rocketlane API unavailable during duplicate check: {e.reason}", "system")
        if found.project_id:
            return d.escalate(s, AGENT, f"Existing Rocketlane project {found.project_id} for this customer", "duplicate")
        d.audit(s, AGENT, step, "no existing run or Rocketlane project")
        return None

    def check_duplicate(s: OnboardingState) -> dict:
        return _duplicate(s, "check_duplicate") or {}

    def create_project(s: OnboardingState) -> dict:
        x, rule = s["extracted"], d.rules[Tier(s["plan_tier"])]
        start = d.today().isoformat()
        args = dict(
            project_name=f"{x['customer_name']} Onboarding ({rule.label}, {rule.duration_days}-day)",
            customer_name=x["customer_name"], owner_email=d.settings.rocketlane_owner_email,
            start_date=start, template_id=rule.template_id,
            member_emails=[e for e in (d.settings.rocketlane_pm_email, rule.csm_email) if e],
            customer_contact_email=x["customer_email"], external_reference_id=_ext_ref(s),
            placeholders=role_placeholders(d.settings.rocketlane_pm_email, rule.csm_email, x["customer_email"]))
        try:
            out = d.registry.call(AGENT, "rocketlane_create_project", args, run_id=s["run_id"],
                                  idempotency_key=f"project:{s['run_id']}",
                                  rationale=f"tier {rule.tier} ({s['tier_source']}) -> template "
                                            f"{rule.template_id}, {rule.duration_days} days, {rule.csm_model} CSM")
        except ToolFailed as e:
            return d.escalate(s, AGENT, f"Rocketlane project not created: {e.reason}", "system")
        unfilled = sorted({PLACEHOLDER_PM, PLACEHOLDER_CSM, PLACEHOLDER_CUSTOMER} - set(args["placeholders"]))
        d.audit(s, AGENT, "create_project", f"created from {rule.label} template"
                + (f"; WARNING unfilled placeholders {unfilled} (overdue alerts/tasks have no owner)" if unfilled else ""),
                output={"project_id": out.project_id, "unfilled_placeholders": unfilled})
        return {"project_id": out.project_id,
                **d.set_status(s, "PROJECT_CREATED", rocketlane_project_id=out.project_id)}

    def escalated(s):
        return END if s.get("escalation_reason") else None

    g = StateGraph(OnboardingState)
    for fn in (check_sender, extract_fields, validate, request_clarification, lookup_ae, precheck_duplicate,
               place_call, await_call, interpret_call, wait_retry, check_duplicate, create_project):
        g.add_node(fn.__name__, fn)
    g.add_conditional_edges(START, entry, ["check_sender", "extract_fields", "place_call", "check_duplicate"])
    g.add_conditional_edges("check_sender", lambda s: escalated(s) or "extract_fields", [END, "extract_fields"])
    g.add_conditional_edges("extract_fields", lambda s: escalated(s) or "validate", [END, "validate"])
    g.add_conditional_edges("validate", lambda s: escalated(s) or (
        "request_clarification" if s["issues"] else "lookup_ae"),
        [END, "request_clarification", "lookup_ae"])
    g.add_edge("request_clarification", END)
    g.add_conditional_edges("lookup_ae", lambda s: escalated(s) or "precheck_duplicate", [END, "precheck_duplicate"])
    g.add_conditional_edges("precheck_duplicate", lambda s: escalated(s) or "place_call", [END, "place_call"])
    g.add_conditional_edges("place_call", lambda s: escalated(s) or "await_call", [END, "await_call"])
    g.add_edge("await_call", "interpret_call")
    g.add_conditional_edges("interpret_call", lambda s: escalated(s) or (
        "check_duplicate" if s.get("plan_tier") else "wait_retry"),
        [END, "check_duplicate", "wait_retry"])
    g.add_edge("wait_retry", "place_call")
    g.add_conditional_edges("check_duplicate", lambda s: escalated(s) or "create_project",
                            [END, "create_project"])
    g.add_edge("create_project", END)
    return g.compile()


def _mail_domain(d: Deps) -> str:
    return (d.settings.cs_inbox_address.rpartition("@")[2] or "onboarding.invalid").strip()


def _ext_ref(s: OnboardingState) -> str:
    return s["extracted"].get("opportunity_id") or f"run:{s['run_id']}"
