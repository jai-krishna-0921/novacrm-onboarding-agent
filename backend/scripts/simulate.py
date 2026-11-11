"""Mock end-to-end runs: `make simulate SCENARIO=<name>` (or `all`).

Runs the real graph with fake providers and a scripted LLM, prints the audit timeline and
checks the expected end state from IMPLEMENTATION_PLAN §15.
"""

import sys

from app.config import Settings
from app.db.seed import demo_contacts
from app.llm.schemas import DealFields
from app.services.tracking import PHASES, Tracker
from app.sim import FakeLLM, Harness, InboundEmail

FIELDS = dict(customer_name="Acme Corp", customer_contact_name="Dana Lee", customer_email="dana@acme.example",
              ae_name="Jai Krishna", ae_email="jai@novacrm.example", opportunity_id="006Hs00001AbCdEIAV")
EMAIL = InboundEmail(message_id="gm-1", thread_id="th-1", rfc_message_id="<m1@mail>", sender="jai@novacrm.example",
                     subject="Closed Won: Acme Corp", sender_authenticated=True,
                     body="We just closed Acme Corp ...\n"
                          "Opp: https://novacrm.lightning.force.com/lightning/r/Opportunity/006Hs00001AbCdEIAV/view")
ENT = dict(status="connected", plan_tier="enterprise", confirmed=True, transcript="AE: Enterprise.\nAE: Yes.")
GRO = dict(status="connected", plan_tier="growth", confirmed=True, transcript="AE: Growth.\nAE: Yes.")
NOPE = dict(status="no_answer", transcript="")
HEDGE = dict(status="connected", plan_tier="unclear", confirmed=False, transcript="AE: maybe the big one?")



def _track(h: Harness, rid: str, contacts, phases) -> Tracker:
    """Agent 3: a person ticks whole phases in Rocketlane; the tracker syncs after each one."""
    t = Tracker(h.settings, h.store, h.deps.registry, h.llm, contacts)
    pid = h.store.get_run(rid)["rocketlane_project_id"]
    t.sync(rid)
    for phase in phases:
        h.world.rocketlane.mark(pid, phase=phase)
        t.sync(rid)
    return t


def tracking_handoff(h: Harness, rid: str) -> str:
    source, _ = demo_contacts()
    _track(h, rid, lambda: (source, source), PHASES)
    return h.status(rid)


def tracking_unverified_reopen(h: Harness, rid: str) -> str:
    _track(h, rid, demo_contacts, PHASES[:2])  # the seeded destination has a duplicate and a blank row
    [mv] = [t for t in h.store.get_tracking(rid)["tasks"] if t["name"].startswith("Migration verified")]
    open_esc = [e for e in h.store.list_escalations("open") if e["run_id"] == rid and e["kind"] == "migration"]
    return "REOPENED" if not mv["completed"] and open_esc else "NOT_REOPENED"


# name: (calls, field overrides, world kwargs, expected status, send email twice[, after-run check -> outcome])
SCENARIOS = {
    "happy_enterprise": ([ENT], {}, {}, "COMPLETED", False),
    "happy_growth": ([GRO], {}, {}, "COMPLETED", False),
    "missing_contact_email": ([], {"customer_email": None}, {}, "AWAITING_CLARIFICATION", False),
    "unknown_ae": ([], {"ae_email": "ghost@novacrm.example"}, {}, "ESCALATED", False),
    "no_answer_then_escalate": ([NOPE] * 3, {}, {}, "ESCALATED", False),
    "ambiguous_then_clear": ([HEDGE, ENT], {}, {}, "COMPLETED", False),
    "rocketlane_down_recovers": ([ENT], {}, {"rocketlane_failures": 2}, "COMPLETED", False),
    "rocketlane_down": ([ENT], {}, {"rocketlane_failures": 99}, "ESCALATED", False),
    "duplicate_project": ([ENT], {}, {"existing_projects": [
        {"projectId": 7, "customer": {"companyName": "Acme Corp"}, "externalReferenceId": "x"}]}, "ESCALATED", False),
    "duplicate_webhook": ([ENT], {}, {}, "COMPLETED", True),
    "tracking_handoff": ([ENT], {}, {}, "HANDED_OFF", False, tracking_handoff),
    "tracking_unverified_reopen": ([GRO], {}, {}, "REOPENED", False, tracking_unverified_reopen),
}


def run(name: str, settings: Settings) -> bool:
    calls, fields, world, expected, twice, *after = SCENARIOS[name]
    h = Harness(settings, llm=FakeLLM(fields=DealFields(**{**FIELDS, **fields})), **world)
    h.world.voice.script = list(calls)
    rid = h.receive(EMAIL)
    if twice:
        assert h.receive(EMAIL) == rid
    status = after[0](h, rid) if after else h.status(rid)
    ok = status == expected
    print(f"\n=== {name}: {status} (expected {expected}) {'PASS' if ok else 'FAIL'} ===")
    for r in h.store.audit_rows:
        print(f"  {r['ts'][11:23]} [{r['agent']:<6}] {r['step']:<28} {r['status']:<10} {r['rationale'][:90]}")
    print(f"  calls={len(h.world.voice.calls)} projects={len(h.world.rocketlane.projects)} "
          f"channels={len(h.world.slack.channels)} emails_sent={len(h.world.gmail.sent)} "
          f"slack_posts={sum(len(c['messages']) for c in h.world.slack.channels.values())}")
    return ok


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    settings = Settings(_env_file=None, provider_mode="fake", call_retry_delay_seconds=0,
                        rocketlane_template_enterprise="TEMPLATE-ENT", rocketlane_template_growth="TEMPLATE-GROWTH",
                        rocketlane_owner_email="owner@novacrm.example", rocketlane_pm_email="pm@novacrm.example",
                        rocketlane_csm_enterprise="csm-dedicated@novacrm.example",
                        rocketlane_csm_growth="csm-pool@novacrm.example")
    names = list(SCENARIOS) if target == "all" else [target]
    results = {n: run(n, settings) for n in names}
    print(f"\n{sum(results.values())}/{len(results)} scenarios matched expected end state")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
