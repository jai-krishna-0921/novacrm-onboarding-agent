"""LLM evals against real Claude (costs tokens). `make eval ARGS="--suite transcripts --model claude-sonnet-5-5"`.

Scoring rules: evals/datasets/README.md. Results: evals/results/<ts>-<model>.json. Exit 1 under threshold.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import Settings, Tier, tier_rules
from app.guardrails.output import check_welcome
from app.llm.client import LangChainLLM
from app.llm.schemas import DealFields, TranscriptReading, WelcomeDraft
from app.services.store import MemoryStore

HERE = Path(__file__).parent
FIELDS = ["customer_name", "customer_contact_name", "customer_email", "ae_name", "ae_email", "opportunity_id",
          "deal_count"]
PHASES = ["kickoff", "data migration", "configuration", "go-live"]


class JudgeScores(BaseModel):
    personalization: int = Field(ge=1, le=4)
    tone: int = Field(ge=1, le=4)
    clarity: int = Field(ge=1, le=4)
    no_invented_commitments: int = Field(ge=1, le=4)
    reason: str


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (HERE / "datasets" / name).read_text().splitlines() if line.strip()]


# ---------------- scoring (pure, unit-tested) ----------------

def _norm(field: str, v):
    if v is None:
        return None
    if field == "deal_count":
        return int(v)
    s = str(v).strip()
    return s if field == "opportunity_id" else s.casefold()


def score_extraction(case: dict, output: dict) -> dict:
    fields = case.get("scored_fields") or FIELDS
    accept = case.get("accept", {})
    results = {}
    for f in fields:
        allowed = accept.get(f, [case["expected"].get(f)])
        if case["expected"].get(f) not in allowed:
            allowed = [case["expected"].get(f), *allowed]
        got = output.get(f)
        if got == "":
            got = "<empty>"  # "" is never a valid answer (null means absent)
        results[f] = _norm(f, got) in {_norm(f, a) for a in allowed}
    blob = json.dumps(output, ensure_ascii=False).casefold()
    leaked = [s for s in case.get("forbidden_substrings", []) if s.casefold() in blob]
    return {"passed": all(results.values()) and not leaked, "fields": results, "leaked": leaked}


def score_transcript(case: dict, output: dict) -> dict:
    exp = case["expected"]
    passed = output["plan_tier"] == exp["plan_tier"] and output["ae_confirmed"] == exp["ae_confirmed"]
    claims = output["plan_tier"] != "unclear" and output["ae_confirmed"] is True
    truly = exp["plan_tier"] == output["plan_tier"] and exp["ae_confirmed"] is True
    return {"passed": passed, "false_confirmed": claims and not truly}


# ---------------- suites ----------------

def run_extraction(llm, cases):
    def one(c):
        e = c["email"]
        user = f"<email>\nFrom: {e['sender']}\nSubject: {e['subject']}\n\n{e['body']}\n</email>"
        out = llm.parse(run_id=f"eval:{c['id']}", agent="eval", node="extract_fields", prompt="extract_v1",
                        user=user, schema=DealFields).model_dump()
        return {**score_extraction(c, out), "id": c["id"], "tags": c["tags"], "output": out}
    return _pool(one, cases)


def run_transcripts(llm, cases):
    def one(c):
        out = llm.parse(run_id=f"eval:{c['id']}", agent="eval", node="interpret_call", prompt="interpret_v1",
                        user=f"<transcript>\n{c['transcript']}\n</transcript>", schema=TranscriptReading).model_dump()
        return {**score_transcript(c, out), "id": c["id"], "tags": c["tags"], "output": out,
                "expected": c["expected"]}
    return _pool(one, cases)


def run_welcome(llm, judge, settings, cases):
    rules = tier_rules(settings)

    def one(c):
        f = c["facts"]
        msg = llm.parse(run_id=f"eval:{c['id']}", agent="eval", node="draft_welcome", prompt="welcome_v1",
                        user=json.dumps(f), schema=WelcomeDraft).message
        rule = rules[Tier.ENTERPRISE if f["plan"].lower() == "enterprise" else Tier.GROWTH]
        problems = check_welcome(msg, f["customer_name"], rule)
        low = msg.lower()
        idx = [low.find(p) for p in PHASES]
        if -1 in idx or idx != sorted(idx):
            problems.append("phases missing or out of order")
        if len(msg.split()) > 120:
            problems.append(f"{len(msg.split())} words > 120")
        scores = judge.parse(run_id=f"eval:{c['id']}", agent="eval", node="judge_welcome", prompt="judge_v1",
                             user=f"FACTS:\n{json.dumps(f)}\n\nMESSAGE:\n{msg}", schema=JudgeScores)
        avg = (scores.personalization + scores.tone + scores.clarity + scores.no_invented_commitments) / 4
        return {"id": c["id"], "tags": c["tags"], "passed": not problems and avg >= 3, "problems": problems,
                "judge": scores.model_dump(), "judge_avg": avg, "message": msg}
    return _pool(one, cases)


def _pool(fn, cases):
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(fn, cases))


def by_tag(results) -> dict[str, str]:
    agg = defaultdict(lambda: [0, 0])
    for r in results:
        for t in r["tags"]:
            agg[t][0] += r["passed"]
            agg[t][1] += 1
    return {t: f"{p}/{n}" for t, (p, n) in sorted(agg.items())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all", choices=["all", "extraction", "transcripts", "welcome"])
    ap.add_argument("--model")
    ap.add_argument("--judge-model")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    s = Settings()
    model = a.model or s.llm_model
    store = MemoryStore()
    llm = LangChainLLM(store, model, s.anthropic_api_key)
    judge = LangChainLLM(store, a.judge_model or s.llm_judge_model, s.anthropic_api_key)

    report, ok, started = {"model": model}, True, time.time()
    if a.suite in ("all", "extraction"):
        res = run_extraction(llm, load("extraction.jsonl")[: a.limit])
        clean = [r for r in res if "clean" in r["tags"]]
        field_acc = {f: sum(r["fields"].get(f, True) for r in res) / len(res) for f in FIELDS}
        report["extraction"] = {"passed": f"{sum(r['passed'] for r in res)}/{len(res)}", "by_tag": by_tag(res),
                                "field_accuracy": field_acc, "misses": [r for r in res if not r["passed"]]}
        ok &= all(r["passed"] for r in clean) and not any(r["leaked"] for r in res)
    if a.suite in ("all", "transcripts"):
        res = run_transcripts(llm, load("transcripts.jsonl")[: a.limit])
        fc = [r["id"] for r in res if r["false_confirmed"]]
        report["transcripts"] = {"passed": f"{sum(r['passed'] for r in res)}/{len(res)}", "false_confirmed": fc,
                                 "by_tag": by_tag(res), "misses": [r for r in res if not r["passed"]]}
        ok &= not fc
    if a.suite in ("all", "welcome"):
        res = run_welcome(llm, judge, s, load("welcome.jsonl")[: a.limit])
        report["welcome"] = {"passed": f"{sum(r['passed'] for r in res)}/{len(res)}",
                             "judge_avg": round(sum(r["judge_avg"] for r in res) / len(res), 2),
                             "by_tag": by_tag(res), "misses": [r for r in res if not r["passed"]]}
        ok &= all(not r["problems"] for r in res) and report["welcome"]["judge_avg"] >= 3

    calls = store.llm_calls
    report["cost"] = {"calls": len(calls), "usd": round(sum(c["cost_usd"] for c in calls), 4),
                      "seconds": round(time.time() - started, 1)}
    out = HERE / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-{model}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))

    for suite in ("extraction", "transcripts", "welcome"):
        if suite in report:
            r = report[suite]
            print(f"\n== {suite}: {r['passed']} passed" + (f" | FALSE-CONFIRMED: {r['false_confirmed'] or 0}"
                                                         if suite == "transcripts" else "")
                  + (f" | judge avg {r['judge_avg']}" if suite == "welcome" else ""))
            print("   by tag: " + ", ".join(f"{t} {v}" for t, v in r["by_tag"].items()))
            for m in r["misses"]:
                detail = m.get("problems") or {k: v for k, v in m.get("fields", {}).items() if not v} or \
                    {"got": m.get("output"), "expected": m.get("expected")}
                print(f"   MISS {m['id']}: {detail}")
    print(f"\nmodel={model} cost=${report['cost']['usd']} calls={report['cost']['calls']} "
          f"time={report['cost']['seconds']}s -> {out.name}\n{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
