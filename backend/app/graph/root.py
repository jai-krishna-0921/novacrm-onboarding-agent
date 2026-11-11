"""Root router: plain conditional edges on state, no LLM. Owns escalation and human resolution."""

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.config import Tier
from app.graph.comms import build_comms
from app.graph.intake import build_intake
from app.graph.state import Deps, OnboardingState

# Which human resolutions each escalation kind accepts. Only an unconfirmed-tier escalation may be
# resolved by a human stating the tier; every other kind must go back through the normal checks.
ALLOWED = {
    "tier": {"set_tier", "retry", "cancel"},
    "input": {"retry", "cancel"},
    "duplicate": {"retry", "cancel"},
    "system": {"retry", "cancel"},
    # Raised outside the graph by the migration checker (services/migration_check.py) after the project exists.
    # The CSM gets the data fixed and re-runs the check; a passing re-run resolves it. Never a tier change.
    "migration": {"retry"},
}


def validate_resolution(resolution: dict, state: dict) -> dict:
    action = resolution.get("action") if isinstance(resolution, dict) else None
    kind = state.get("escalation_kind", "system")
    if action not in ALLOWED[kind]:
        raise ValueError(f"'{action}' is not allowed for a {kind} escalation; allowed: {sorted(ALLOWED[kind])}")
    if action == "set_tier" and state.get("project_id"):
        raise ValueError("project already exists; its tier can't be changed here")
    if action == "set_tier" and resolution.get("tier") not in {t.value for t in Tier}:
        raise ValueError("tier must be 'enterprise' or 'growth'")
    if not resolution.get("by"):
        raise ValueError("resolution must name who made it ('by')")
    return resolution


def build_graph(d: Deps, checkpointer):
    def start(s: OnboardingState) -> dict:
        d.audit(s, "root", "start", "deal email received; starting intake",
                input={k: s["email"][k] for k in ("message_id", "sender", "subject")})
        return d.set_status(s, "RECEIVED", gmail_message_id=s["email"]["message_id"])

    def after_intake(s: OnboardingState) -> str:
        if s.get("escalation_reason"):
            return "escalate"
        if s.get("status") == "AWAITING_CLARIFICATION":
            return END
        return "comms"

    def after_comms(s: OnboardingState) -> str:
        return "escalate" if s.get("escalation_reason") else "complete"

    def complete(s: OnboardingState) -> dict:
        d.audit(s, "root", "complete", "project created and channel set up")
        return d.set_status(s, "COMPLETED", slack_channel_id=s["channel_id"])

    def escalate(s: OnboardingState) -> dict:
        n = s.get("escalation_count", 0) + 1
        d.store.upsert_escalation(f"{s['run_id']}:{n}", dict(
            run_id=s["run_id"], reason=s["escalation_reason"], kind=s.get("escalation_kind"),
            agent=s.get("escalated_by"), status="open"))
        d.audit(s, "root", "escalate", s["escalation_reason"], status="escalated")
        return {"escalation_count": n, **d.set_status(s, "ESCALATED")}

    def await_human(s: OnboardingState) -> dict:
        # Invalid resolutions are rejected by re-asking (defence in depth behind the API's own check).
        while True:
            resolution = interrupt({"type": "escalation", "kind": s.get("escalation_kind"),
                                    "reason": s["escalation_reason"]})
            try:
                return {"resolution": validate_resolution(resolution, s)}
            except ValueError:
                continue

    def apply_resolution(s: OnboardingState) -> dict:
        r = s["resolution"]
        d.store.upsert_escalation(f"{s['run_id']}:{s['escalation_count']}",
                                  dict(status="resolved", resolved_by=r["by"], resolution=r))
        d.audit(s, "root", "resolve", f"human resolution by {r['by']}: {r['action']}", input=r)
        if r["action"] == "cancel":
            return d.set_status(s, "CANCELLED")
        update: dict = {"escalation_reason": None, "escalation_kind": None}
        if r["action"] == "set_tier":
            update |= {"plan_tier": r["tier"], "tier_source": f"human:{r['by']}"}
        else:
            update |= {"call_attempts": 0}
        return update

    def after_resolution(s: OnboardingState) -> str:
        if s.get("status") == "CANCELLED":
            return END
        return "comms" if s.get("project_id") else "intake"

    g = StateGraph(OnboardingState)
    g.add_node("start", start)
    g.add_node("intake", build_intake(d))
    g.add_node("comms", build_comms(d))
    g.add_node("complete", complete)
    g.add_node("escalate", escalate)
    g.add_node("await_human", await_human)
    g.add_node("apply_resolution", apply_resolution)
    g.add_edge(START, "start")
    g.add_edge("start", "intake")
    g.add_conditional_edges("intake", after_intake, ["escalate", "comms", END])
    g.add_conditional_edges("comms", after_comms, ["escalate", "complete"])
    g.add_edge("complete", END)
    g.add_edge("escalate", "await_human")
    g.add_edge("await_human", "apply_resolution")
    g.add_conditional_edges("apply_resolution", after_resolution, ["intake", "comms", END])
    return g.compile(checkpointer=checkpointer)
