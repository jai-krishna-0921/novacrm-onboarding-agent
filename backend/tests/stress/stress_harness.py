"""Shared machinery for the stress / chaos suite. PROVIDER_MODE=fake only: no real provider is ever touched.

- deal-email generator with a labelled mix (valid, missing fields, non-deal, spoofed, unknown sender, duplicate
  opportunity, Gmail redelivery) and a per-run "persona" that decides how the AE answers the phone
- StressLLM: FakeLLM that extracts per-email fields, with optional latency and LLMError injection
- RetellSim: plays Retell (webhook payloads + get-call for reconciliation of lost webhooks)
- Faulty: provider proxy injecting 5xx / 429 / timeout-before-send / timeout-after-send at a given rate
- UdsServer: the real FastAPI app under uvicorn on a unix socket (pytest-socket allows AF_UNIX), so webhook
  latency is measured with background work really running in the background (TestClient runs it inline)
- check_invariants(): the system-wide invariants every stress test asserts
"""

import hashlib
import hmac
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

import httpx
import uvicorn

from app.guardrails.output import run_suffix
from app.llm.client import LLMError
from app.llm.schemas import DealFields
from app.services.keys import customer_key, opportunity_key
from app.sim import FakeLLM
from app.tools.definitions import AeLookupOut, InboundEmail
from app.tools.registry import AmbiguousToolError, TransientToolError

KEY, TOKEN = "stress-retell-key", "stress-push-token"
AES = {f"ae{i}@novacrm.example": (f"Ae{i} Person", f"+1415555{i:04d}") for i in range(8)}
PERSONAS = ("enterprise", "growth", "late", "never", "hedge")
CONFIRMS = {"enterprise": "enterprise", "growth": "growth", "late": "enterprise"}
OK_RESTING = {"COMPLETED", "HANDED_OFF", "NOT_A_DEAL", "AWAITING_CLARIFICATION", "CANCELLED"}


def directory() -> dict[str, AeLookupOut]:
    return {e: AeLookupOut(found=True, name=n, email=e, phone=p, active=True) for e, (n, p) in AES.items()}


def seed_store_aes(store) -> None:
    for e, (n, p) in AES.items():
        store.upsert_ae(e, n, p)


# ---------------------------------------------------------------- deal emails
@dataclass
class Deal:
    idx: int
    email: InboundEmail
    fields: dict
    kind: str      # valid | missing | not_deal | spoofed | unknown_sender | dup_opp | redelivery
    persona: str   # how the AE answers (see PERSONAS)
    opp: str


def opp_url(opp: str) -> str:
    return f"https://novacrm.lightning.force.com/lightning/r/Opportunity/{opp}/view"


def make_deals(n: int, seed: int) -> list[Deal]:
    rng = random.Random(seed)
    deals: list[Deal] = []
    originals: list[Deal] = []
    for i in range(n):
        r = rng.random()
        if originals and r < .10:
            kind = "dup_opp"
        elif deals and r < .15:
            kind = "redelivery"
        elif r < .25:
            kind = "missing"
        elif r < .32:
            kind = "not_deal"
        elif r < .39:
            kind = "spoofed"
        elif r < .44:
            kind = "unknown_sender"
        else:
            kind = "valid"
        if kind == "redelivery":  # the same Gmail message delivered again (history overlap, push + poller)
            src = rng.choice(deals)
            deals.append(Deal(i, src.email, src.fields, "redelivery", src.persona, src.opp))
            continue
        persona = rng.choice(PERSONAS)
        if kind == "dup_opp":  # the AE re-sends the deal email for an opportunity already in flight
            src = rng.choice(originals)
            fields, opp = dict(src.fields), src.opp
        else:
            ae = rng.choice(list(AES))
            opp = f"006{i:012d}AAA"
            fields = dict(customer_name=f"Nimbus{i} Systems", customer_contact_name="Dana Lee",
                          customer_email=f"dana{i}@nimbus{i}.example", ae_name=AES[ae][0], ae_email=ae,
                          opportunity_id=opp, deal_count=1)
        if kind == "missing":
            fields[rng.choice(["customer_name", "customer_email", "ae_email"])] = None
        if kind == "not_deal":
            fields["deal_count"] = 0
        sender = "stranger@evil.example" if kind == "unknown_sender" else (fields["ae_email"] or rng.choice(list(AES)))
        email = InboundEmail(
            message_id=f"gm-{i}", thread_id=f"th-{i}", rfc_message_id=f"<m{i}@mail.example>", sender=sender,
            subject=f"Closed Won: {fields['customer_name'] or 'a new customer'}",
            body=f"REF-{i}\nWe closed {fields['customer_name']}. Opp: {opp_url(opp)}",
            sender_authenticated=kind != "spoofed",
            auth_detail="dmarc=fail" if kind == "spoofed" else "dmarc=pass header.from=novacrm.example")
        d = Deal(i, email, fields, kind, persona, opp)
        deals.append(d)
        if kind in ("valid", "dup_opp"):
            originals.append(d)
    return deals


class StressInbox:
    """Gmail history stand-in. Thread safe. Each read also re-returns the last `overlap` messages (history overlap
    / push+poller racing on the same cursor), so the runner's message-id dedupe is exercised constantly."""

    def __init__(self, overlap: int = 3):
        self._lock = threading.Lock()
        self.pending: list[InboundEmail] = []
        self.recent: list[InboundEmail] = []
        self.overlap, self.reads = overlap, 0

    def add(self, *emails: InboundEmail) -> None:
        with self._lock:
            self.pending.extend(emails)

    def current_history_id(self) -> str:
        return "1"

    def new_deal_emails(self, cursor, relevant=None):
        with self._lock:
            self.reads += 1
            out = self.recent[-self.overlap:] + self.pending
            self.recent += self.pending
            self.pending = []
        return [e for e in out if relevant is None or relevant(e)], str(self.reads + 1)


# ---------------------------------------------------------------- LLM
class StressLLM(FakeLLM):
    """FakeLLM with per-email extraction (REF-<n> in the body), optional latency and outage injection."""

    def __init__(self, deals: list[Deal], fault_rate: float = 0.0, seed: int = 0, latency: float = 0.0):
        super().__init__(fields=DealFields())
        self.by_ref = {f"REF-{d.idx}": d.fields for d in deals}
        self.fault_rate, self.latency = fault_rate, latency
        self._rng, self._lock = random.Random(seed), threading.Lock()
        self.faults = 0

    def parse(self, *, run_id, agent, node, prompt, user, schema):
        if self.latency:
            time.sleep(self.latency)
        with self._lock:
            fault = self._rng.random() < self.fault_rate
        if fault:
            self.faults += 1
            raise LLMError(f"{node}: injected outage/refusal")
        out = super().parse(run_id=run_id, agent=agent, node=node, prompt=prompt, user=user, schema=schema)
        if schema is DealFields:
            ref = re.search(r"REF-\d+", user)
            return DealFields(**self.by_ref[ref.group(0)]) if ref else DealFields(deal_count=0)
        return out


# ---------------------------------------------------------------- Retell
class RetellSim:
    """Decides each placed call's outcome from the run's persona and serves it as a webhook or via get-call."""

    def __init__(self, world, persona_of_run):
        self.world, self.persona_of_run = world, persona_of_run
        self.get_call_hits = 0

    def _call(self, call_id: str) -> dict | None:
        return next((c for c in self.world.voice.calls if c["provider_call_id"] == call_id), None)

    def call_object(self, call_id: str) -> dict | None:
        c = self._call(call_id)
        if c is None:
            return None
        rid, attempt = c["run_id"], c["attempt"]
        persona = self.persona_of_run(rid)
        base = {"call_id": call_id, "metadata": {"run_id": rid, "attempt": attempt}}
        if persona == "never" or (persona == "late" and attempt == 1):
            return {**base, "call_status": "not_connected", "disconnection_reason": "dial_no_answer"}
        if persona == "hedge":
            return {**base, "call_status": "ended", "disconnection_reason": "user_hangup",
                    "transcript": "Agent: Enterprise or Growth?\nUser: Hmm, I think maybe the bigger one? Not sure.",
                    "call_analysis": {"custom_analysis_data": {"plan_tier": "unclear", "tier_confirmed": False}}}
        tier = CONFIRMS[persona]
        return {**base, "call_status": "ended", "disconnection_reason": "user_hangup",
                "transcript": f"Agent: Enterprise or Growth?\nUser: {tier.title()}.\nAgent: Confirm?\nUser: Yes.",
                "call_analysis": {"custom_analysis_data": {"plan_tier": tier, "tier_confirmed": True}}}

    def get_call(self, call_id: str) -> dict | None:  # the reconcile path (tick) calls this outside the lock
        self.get_call_hits += 1
        return self.call_object(call_id)

    def calls_of(self, run_id: str) -> list[str]:
        return [c["provider_call_id"] for c in self.world.voice.calls if c["run_id"] == run_id]


def signed(payload: dict, key: str = KEY) -> tuple[str, dict]:
    body = json.dumps(payload)
    ts = str(int(time.time() * 1000))
    sig = f"v={ts},d={hmac.new(key.encode(), (body + ts).encode(), hashlib.sha256).hexdigest()}"
    return body, {"X-Retell-Signature": sig, "Content-Type": "application/json"}


# ---------------------------------------------------------------- provider faults
WRITES = {"reply", "start_call", "create_project", "append_task_note", "set_task_status", "create_channel",
          "set_topic", "invite", "invite_shared", "post"}
READS = {"lookup", "find_project", "find_task", "list_tasks", "find_channel", "find_post", "find_sent"}


class Faulty:
    """Proxy around one fake provider. On each listed call, with probability `rate`, injects one of:
    5xx and 429 (TransientToolError, nothing happened), timeout before send (AmbiguousToolError, nothing happened)
    or timeout AFTER send (the write happened, then AmbiguousToolError). Reads only get 5xx/429."""

    def __init__(self, inner, rate: float, rng: random.Random, lock: threading.Lock, log: Counter):
        self._inner, self._rate, self._rng, self._lock, self._log = inner, rate, rng, lock, log
        self.enabled = True

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr) or name not in WRITES | READS:
            return attr

        def wrapped(*a, **kw):
            with self._lock:
                r = self._rng.random() / self._rate if self.enabled and self._rate else 1.0
            if r < .3:
                self._log[f"{name}:503"] += 1
                raise TransientToolError(f"injected 503 on {name}")
            if r < .5:
                self._log[f"{name}:429"] += 1
                raise TransientToolError(f"injected 429 on {name}")
            if name in WRITES and r < .75:
                self._log[f"{name}:timeout_before_send"] += 1
                raise AmbiguousToolError(f"injected timeout before {name} was sent")
            if name in WRITES and r < 1.0:
                attr(*a, **kw)  # the side effect happens; then the response is "lost"
                self._log[f"{name}:timeout_after_send"] += 1
                raise AmbiguousToolError(f"injected timeout after {name} was sent")
            return attr(*a, **kw)
        return wrapped


class ChaosProviders:
    """What build_registry sees: every provider wrapped in Faulty. `raw` keeps the un-wrapped world for asserts."""

    def __init__(self, world, rate: float, seed: int):
        self.raw, self.faults = world, Counter()
        rng, lock = random.Random(seed), threading.Lock()
        self.proxies = [Faulty(getattr(world, n), rate, rng, lock, self.faults)
                        for n in ("directory", "gmail", "voice", "rocketlane", "slack")]
        self.directory, self.gmail, self.voice, self.rocketlane, self.slack = self.proxies

    def calm(self) -> None:
        for p in self.proxies:
            p.enabled = False


# ---------------------------------------------------------------- server
class UdsServer:
    """The real ASGI app under uvicorn on a unix socket in a background thread."""

    def __init__(self, app):
        self.dir = tempfile.mkdtemp(prefix="stress-")
        self.path = os.path.join(self.dir, "api.sock")
        self.server = uvicorn.Server(uvicorn.Config(app, uds=self.path, log_level="warning", lifespan="on",
                                                    access_log=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        t0 = time.time()
        while not self.server.started:
            assert time.time() - t0 < 10, "uvicorn did not start"
            time.sleep(0.01)
        return self

    def client(self, **kw) -> httpx.Client:
        return httpx.Client(transport=httpx.HTTPTransport(uds=self.path), base_url="http://stress.local",
                            timeout=60, **kw)

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


class ErrorLog(logging.Handler):
    """Collects ERROR+ records (unhandled exceptions in background tasks, scheduler failures)."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        logging.getLogger().addHandler(self)
        return self

    def __exit__(self, *exc):
        logging.getLogger().removeHandler(self)

    def summary(self) -> list[str]:
        return [f"{r.name}: {r.getMessage()} {r.exc_info[1]!r}" if r.exc_info else f"{r.name}: {r.getMessage()}"
                for r in self.records]


def settle(signature, quiet: float = 0.5, timeout: float = 120) -> None:
    """Wait until background work stops changing `signature()` for `quiet` seconds."""
    end, last, since = time.time() + timeout, None, time.time()
    while time.time() < end:
        s = signature()
        if s != last:
            last, since = s, time.time()
        elif time.time() - since >= quiet:
            return
        time.sleep(0.05)
    raise AssertionError("system did not settle")


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))] if s else 0.0


# ---------------------------------------------------------------- invariants
def check_invariants(*, runs: list[dict], escalations: list[dict], audit_rows: list[dict], world, deals=None,
                     human_retries: dict | None = None) -> list[str]:
    """System-wide invariants. Returns human-readable violations (empty = all held)."""
    v: list[str] = []
    human_retries = human_retries or {}
    rl = world.rocketlane.projects
    for opp, n in Counter(opportunity_key(p["externalReferenceId"]) for p in rl).items():
        if n > 1:
            v.append(f"DUPLICATE PROJECT: {n} Rocketlane projects for opportunity {opp}")
    for k, n in Counter(customer_key(p["customer"]["companyName"]) for p in rl).items():
        if n > 1:
            v.append(f"DUPLICATE PROJECT: {n} Rocketlane projects for customer key {k!r}")
    dials = Counter((c["run_id"], c["attempt"], c["idempotency_key"]) for c in world.voice.calls)
    v += [f"DUPLICATE CALL: {n} dials for run/attempt {k}" for k, n in dials.items() if n > 1]
    per_run = Counter(c["run_id"] for c in world.voice.calls)
    v += [f"TOO MANY CALLS: run {r} dialled {n}x (retries={human_retries.get(r, 0)})"
          for r, n in per_run.items() if n > 3 * (1 + human_retries.get(r, 0))]

    open_by_run = defaultdict(list)
    for e in escalations:
        if e.get("status") == "open":
            open_by_run[e["run_id"]].append(e)
    audit_by_run = defaultdict(list)
    for a in audit_rows:
        audit_by_run[a["run_id"]].append(a)
        if not (a.get("ts") and a.get("agent") and a.get("rationale")):
            v.append(f"INCOMPLETE AUDIT ROW: {a}")
        if "+1415555" in json.dumps(a, default=str):
            v.append(f"UNMASKED PHONE in audit row {a['run_id']}:{a['step']}")
    project_runs = Counter(r["rocketlane_project_id"] for r in runs if r.get("rocketlane_project_id"))
    v += [f"PROJECT SHARED: project {p} recorded on {n} runs" for p, n in project_runs.items() if n > 1]
    chan_runs = Counter(r["slack_channel_id"] for r in runs if r.get("slack_channel_id"))
    v += [f"CHANNEL SHARED: channel {c} recorded on {n} runs" for c, n in chan_runs.items() if n > 1]
    for r in runs:
        rid, st = r["id"], r.get("status")
        steps = {(a["step"], a["status"]) for a in audit_by_run[rid]}
        if st not in OK_RESTING and not (st == "ESCALATED" and open_by_run[rid]):
            v.append(f"STUCK/UNESCALATED: run {rid} in {st} (open escalations: {len(open_by_run[rid])}, "
                     f"error={r.get('error')})")
        need = {"COMPLETED": ("complete", "ok"), "ESCALATED": ("escalate", "escalated"),
                "NOT_A_DEAL": ("not_a_deal", "ok"), "AWAITING_CLARIFICATION": ("tool:gmail_reply", "ok")}.get(st)
        if not any(s == "start" for s, _ in steps):
            v.append(f"AUDIT GAP: run {rid} has no 'start' row")
        if need and need not in steps:
            v.append(f"AUDIT GAP: run {rid} in {st} without a {need} audit row")
        if st in ("COMPLETED", "HANDED_OFF"):
            ch = world.slack.channels.get(r.get("slack_channel_id"))
            if ch is None:
                v.append(f"NO CHANNEL: completed run {rid}")
            else:
                welcomes = [m for m in ch["messages"] if not m.startswith(":white_check_mark:")]
                if len(welcomes) != 1:
                    v.append(f"WELCOME COUNT: channel {r['slack_channel_id']} has {len(welcomes)} welcome posts")
    # Ownership from the audit trail (an escalated run created its channel but only COMPLETED records it on the
    # run). A channel/project no run's audit claims is an orphan of a lost create that a retry re-created; one
    # claimed by two runs is shared across customers.
    claims: dict[tuple[str, str], set] = defaultdict(set)
    for a in audit_rows:
        out = a.get("output") or {}
        if a["status"] in ("ok", "replayed") and isinstance(out, dict):
            if a["step"] == "tool:slack_create_channel" and out.get("channel_id"):
                claims[("channel", out["channel_id"])].add(a["run_id"])
            if a["step"] == "tool:rocketlane_create_project" and out.get("project_id"):
                claims[("project", str(out["project_id"]))].add(a["run_id"])
    v += [f"{k[0].upper()} CLAIMED BY {len(r)} RUNS: {k[1]}" for k, r in claims.items() if len(r) > 1]
    # A create whose response AND follow-up lookup were both lost is escalated as "outcome unknown"; the channel
    # exists but nothing can claim it until a person verifies. That is the designed outcome, not an orphan, as
    # long as the owning run (name ends in its run hash) is still escalated with that reason.
    unknown = {run_suffix(e["run_id"]) for e in escalations
               if e.get("status") == "open" and "outcome unknown" in (e.get("reason") or "")}
    v += [f"ORPHAN CHANNEL: {cid} {c['name']}" for cid, c in world.slack.channels.items()
          if ("channel", cid) not in claims and cid not in set(chan_runs) and not cid.startswith("CHANDOFF")
          and c["name"].rsplit("-", 1)[-1] not in unknown]
    unknown_customers = {r.get("customer_name") for r in runs
                         if run_suffix(r["id"]) in unknown and r.get("customer_name")}
    v += [f"ORPHAN PROJECT: {p['projectId']} {p['projectName']}" for p in rl
          if ("project", str(p["projectId"])) not in claims and str(p["projectId"]) not in set(project_runs)
          and not any(p["projectName"].startswith(f"{c} Onboarding") for c in unknown_customers)]

    if deals is not None:
        v += _check_outcomes(runs, deals, world, audit_by_run, escalations)
    return v


def _check_outcomes(runs, deals, world, audit_by_run, escalations) -> list[str]:
    """Each labelled email ended where its label says (no deal is ever onboarded without a confirmed tier)."""
    v = []
    by_msg = {r.get("gmail_message_id"): r for r in runs}
    esc_kind = defaultdict(set)
    for e in escalations:
        esc_kind[e["run_id"]].add(e.get("kind"))
    projects = {str(p["projectId"]): p for p in world.rocketlane.projects}
    for d in deals:
        if d.kind == "redelivery":
            continue
        r = by_msg.get(d.email.message_id)
        if r is None:
            v.append(f"LOST EMAIL: {d.email.message_id} ({d.kind}) never became a run")
            continue
        rid, st = r["id"], r.get("status")
        dialled = any(c["run_id"] == rid for c in world.voice.calls)
        if d.kind in ("spoofed", "unknown_sender"):
            if st != "ESCALATED" or "input" not in esc_kind[rid] or dialled or r.get("rocketlane_project_id"):
                v.append(f"GUARDRAIL: {d.kind} {d.email.message_id} -> {st}, dialled={dialled}")
        elif d.kind == "missing" and (dialled or r.get("rocketlane_project_id")):
            v.append(f"GUARDRAIL: missing-field email {d.email.message_id} dialled/created ({st})")
        elif d.kind == "not_deal" and (dialled or st not in ("NOT_A_DEAL", "ESCALATED")):
            v.append(f"GUARDRAIL: non-deal email {d.email.message_id} -> {st}, dialled={dialled}")
        pid = r.get("rocketlane_project_id")
        if pid:
            p = projects.get(str(pid))
            want = CONFIRMS.get(d.persona)
            if want is None and not r.get("plan_tier", "").startswith("human"):
                # project for an AE who never confirmed: the costliest failure
                if not any(a["step"] == "resolve" for a in audit_by_run[rid]):
                    v.append(f"PROJECT WITHOUT CONFIRMED TIER: run {rid} persona {d.persona}")
            elif p is not None and want and f"({want.title()}," not in p["projectName"]:
                v.append(f"WRONG TIER: run {rid} persona {d.persona} -> {p['projectName']}")
    return v
