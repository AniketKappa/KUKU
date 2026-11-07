"""Run the scenarios against the real model and save complete raw output.

Needs an API key in .env (DEEPSEEK_API_KEY, GEMINI_API_KEY, XAI_API_KEY or ANTHROPIC_API_KEY). Run
scripts/smoke_test.py first. This can spend real money, except on Gemini's free tier.

  .venv/bin/python tests/run_scenarios.py              run all, skipping finished ones
  .venv/bin/python tests/run_scenarios.py 01 18        run only ids starting with these
  .venv/bin/python tests/run_scenarios.py --force      run again from scratch
  .venv/bin/python tests/run_scenarios.py --report     rebuild the summary from saved runs
  .venv/bin/python tests/run_scenarios.py --free       no-cost run on Gemini's free tier: most important
                                                       scenarios first, moving to the next model when one
                                                       runs out. Run it again on later days to continue.

Writes outputs/raw/<id>.json (the full record) and outputs/SUMMARY.md.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import datetime as dt
import json
import mimetypes
import pathlib
import re
import sys
import threading
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import agent, config, triage  # noqa: E402
from app.corpus import CITE_RE, library  # noqa: E402
from app.verifier import language_mismatch  # noqa: E402
from tests.scenarios import SCENARIOS  # noqa: E402

RAW = ROOT / "outputs" / "raw"
# Gemini's free tier allows about 20 requests per model per day. Stronger models come first.
# Lite models have the most free quota, so they write. The stronger models have about 20 requests
# a day, which is enough to audit: a model checking its own work misses far more.
FREE_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
FREE_AUDIT_MODELS = ["gemini-3-flash-preview", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.8-flash"]
# Traps and safety first, because those are where a mistake does the most harm.
FREE_PRIORITY = ["18", "19", "09", "16", "11", "23", "24", "01", "21", "04", "08", "12", "03", "10", "20", "06", "22", "15", "27", "02", "05", "07", "13", "14", "17", "25", "26"]
OUT_OF_CREDIT = threading.Event()
FIXTURES = ROOT / "tests" / "fixtures"
SCRIPT_RANGES = {"devanagari": ("ऀ", "ॿ"), "tamil": ("஀", "௿"), "bengali": ("ঀ", "৿"), "latin": ("a", "z")}
PRICE = config.PRICES.get(config.MODEL, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})


def main_script_is(text: str, script: str) -> bool:
    letters = [c.lower() for c in CITE_RE.sub("", text) if c.isalpha()]
    if not letters:
        return False
    low, high = SCRIPT_RANGES[script]
    return sum(low <= c <= high for c in letters) / len(letters) >= 0.5


def file_payload(name: str) -> dict:
    path = FIXTURES / name
    media = mimetypes.guess_type(name)[0] or "text/plain"
    return {"name": name, "media_type": media, "data": base64.b64encode(path.read_bytes()).decode()}


def run_scenario(scenario: dict) -> dict:
    client = agent.make_client()
    session = agent.new_session()
    record = {"id": scenario["id"], "group": scenario["group"], "why": scenario["why"], "started": dt.datetime.now().isoformat(),
              "provider": config.PROVIDER, "model": config.MODEL, "audit_model": config.AUDIT_MODEL, "effort": config.EFFORT, "audit_effort": config.AUDIT_EFFORT, "turns": []}
    started = time.time()
    finals = []
    for turn in scenario["turns"]:
        text = turn if isinstance(turn, str) else turn["text"]
        files = [file_payload(turn["file"])] if isinstance(turn, dict) and turn.get("file") else None
        events = list(agent.run_turn(client, session, text, files=files))
        final = next((e for e in events if e["type"] == "final"), None)
        record["turns"].append({"user": text, "file": turn.get("file") if isinstance(turn, dict) else None,
                                "reply": final["text"] if final else None,
                                "verification": final["verification"] if final else None,
                                "citations": [c["id"] for c in final["citations"]] if final else [],
                                "errors": [e for e in events if e["type"] == "error"]})
        if final:
            finals.append(final)
    if scenario.get("practice"):
        cfg = scenario["practice"]
        agent.practice_start(session, cfg["role"], cfg["style"])
        lines = []
        for line in cfg["lines"]:
            lines.append({"you": line, "them": agent.practice_turn(client, session, line)})
        record["practice"] = lines
        events = list(agent.practice_debrief(client, session))
        final = next((e for e in events if e["type"] == "final"), None)
        record["turns"].append({"user": "[practice debrief]", "reply": final["text"] if final else None,
                                "verification": final["verification"] if final else None,
                                "citations": [c["id"] for c in final["citations"]] if final else [],
                                "errors": [e for e in events if e["type"] == "error"]})
        if final:
            finals.append(final)
    record.update({
        "seconds": round(time.time() - started, 1), "usage": session["usage"], "case_file": session["case_file"],
        "assessment": session["assessment"], "triage": session["triage"], "documents": session["documents"],
        "doc_rejections": session.get("doc_rejections", 0),
        "trail": session["log"],  # Every tool call, every rejected draft, every audit verdict.
    })
    record["checks"] = evaluate(scenario, record)
    return record


def evaluate(scenario: dict, record: dict) -> list[dict]:
    lib = library()
    checks = scenario.get("checks", {})
    results = []

    def add(name, ok, detail=""):
        results.append({"check": name, "pass": bool(ok), "detail": detail})

    replies = [t["reply"] or "" for t in record["turns"]]
    joined = "\n\n".join(replies)
    bare = CITE_RE.sub(" ", joined)
    cited = set(CITE_RE.findall(joined))
    doc_cited = {c for d in record["documents"] for c in d["citations"]}
    every_cited = cited | doc_cited

    # These apply to every scenario.
    add("got_an_answer", all(t["reply"] for t in record["turns"]), f"{sum(1 for t in record['turns'] if t['errors'])} turn(s) with errors")
    fake = [c for c in every_cited if not lib.get(c)]
    add("all_citations_exist", not fake, f"not in library: {fake}" if fake else f"{len(every_cited)} citation(s) all resolve")
    typed = [t["user"] for t in record["turns"] if t["user"] != "[practice debrief]"]
    wrong = [language_mismatch(u, t["reply"] or "") for u, t in zip(typed, record["turns"])]
    add("same_language_as_user", not any(wrong), next((w for w in wrong if w), ""))
    asked_for_draft = re.search(r"(?i)draft|notice bana|legal notice for|write a notice|please write|bana do", " ".join(typed))
    add("no_unrequested_document", asked_for_draft or not record["documents"], f"saved {[d['type'] for d in record['documents']]} without being asked")
    add("substantive_answer", all(len(CITE_RE.sub("", r)) >= 150 for r in replies), f"shortest reply is {min((len(r) for r in replies), default=0)} characters")
    add("no_case_law_cited", not re.search(r"\bAIR\s*\d{4}|\(\d{4}\)\s*\d+\s*SCC|\bSCC OnLine\b", joined))

    if "cite_any" in checks:
        add("cite_any", every_cited & set(checks["cite_any"]), f"wanted any of {checks['cite_any']}; cited {sorted(every_cited)}")
    if "cite_none" in checks:
        bad = every_cited & set(checks["cite_none"])
        add("cite_none", not bad, f"should not cite {sorted(bad)}" if bad else "")
    for pattern in checks.get("say_any", []):
        add(f"say_any /{pattern[:40]}/", re.search(pattern, bare, re.S))
    for pattern in checks.get("say_none", []):
        match = re.search(pattern, bare)
        add(f"say_none /{pattern[:40]}/", not match, f"found {match.group(0)!r}" if match else "")
    if "triage" in checks:
        got = record["triage"]["level"]
        add("triage", triage.LEVELS.index(got) >= triage.LEVELS.index(checks["triage"]), f"got {got}, wanted at least {checks['triage']}")
    if "lawyer" in checks:
        got = (record["assessment"] or {}).get("lawyer_needed", "none")
        add("lawyer", triage.LEVELS.index(got) >= triage.LEVELS.index(checks["lawyer"]), f"got {got}, wanted at least {checks['lawyer']}")
    if "status" in checks:
        got = [t["verification"]["status"] for t in record["turns"] if t["verification"]]
        add("status", all(s in checks["status"] for s in got), f"got {got}")
    if "document" in checks:
        types = [d["type"] for d in record["documents"]]
        add("document", checks["document"] in types, f"saved {types}")
    if "script" in checks:
        add("script", all(main_script_is(r, checks["script"]) for r in replies if r), f"wanted {checks['script']}")
    return results


def cost(usage: dict, model: str | None = None) -> float:
    """At paid list price. Gemini's free tier charges nothing."""
    price = config.PRICES.get(model, PRICE) if model else PRICE
    return sum(usage.get(k, 0) * price[k] for k in price) / 1_000_000


def report() -> None:
    rows, total_cost = [], 0.0
    records = [json.loads(p.read_text()) for p in sorted(RAW.glob("*.json"))]
    passed_all = status_counts = 0
    status_counts, repairs_total, checks_total, checks_pass = {}, 0, 0, 0
    blocked_codes: dict[str, int] = {}
    for r in records:
        checks = r.get("checks", [])
        failed = [c for c in checks if not c["pass"]]
        passed_all += not failed
        checks_total += len(checks)
        checks_pass += len(checks) - len(failed)
        total_cost += cost(r.get("usage", {}), r.get("model"))
        statuses = [t["verification"]["status"] for t in r.get("turns", []) if t.get("verification")]
        repairs = sum(t["verification"]["repairs"] for t in r.get("turns", []) if t.get("verification"))
        repairs_total += repairs
        for s in statuses:
            status_counts[s] = status_counts.get(s, 0) + 1
        for entry in r.get("trail", []):
            for draft in entry.get("drafts", []):
                for issue in draft.get("issues", []):
                    if issue.get("blocking"):
                        blocked_codes[issue["code"]] = blocked_codes.get(issue["code"], 0) + 1
        fail_text = "; ".join(f"{c['check']} ({c['detail'][:70]})" for c in failed) or "all passed"
        rows.append(f"| {r['id']} | {r.get('model', '?')} / {r.get('audit_model', '?')} | {'PASS' if not failed else 'FAIL'} | {', '.join(sorted(set(statuses)))} | {repairs} | {r.get('seconds', 0):.0f}s | {fail_text} |")
    lines = [
        "# Scenario results", "",
        f"Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} from outputs/raw/. Models used: {sorted({r.get('model', '?') for r in records})}, effort {config.EFFORT}.", "",
        f"- Scenarios with every automatic check passing: **{passed_all} of {len(records)}**",
        f"- Individual checks passed: {checks_pass} of {checks_total}",
        f"- Final answer status, per turn: {status_counts}",
        f"- Drafts sent back for repair before the user saw them: {repairs_total}",
        f"- Problems the gate caught in drafts: {dict(sorted(blocked_codes.items(), key=lambda kv: -kv[1]))}",
        f"- Scenarios run so far: {len(records)} of {len(SCENARIOS)}",
        f"- Approximate model cost at paid list price: ${total_cost:.2f} (nothing is charged on Gemini's free tier)", "",
        "Automatic checks catch hard failures only. They can't tell whether advice is good, so read the transcripts in outputs/raw/.", "",
        "| Scenario | Writer / auditor | Checks | Final status | Repairs | Time | Details |", "|---|---|---|---|---|---|---|", *rows,
    ]
    (ROOT / "outputs" / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))


def provider_problems(record: dict) -> list[str]:
    """Failures caused by the provider, which say nothing about how good the app's answers are."""
    problems = [e["error"] for e in record["trail"] if e.get("error")]
    problems += [e["audit_error"] for e in record["trail"] if e.get("audit_error")]
    return problems


def free_run(run=None) -> list[str]:
    """Work through the scenarios on free quota, one model at a time. Returns the ids saved."""
    run = run or run_scenario
    if config.PROVIDER != "gemini":
        sys.exit("--free needs LLM_PROVIDER=gemini in .env and a GEMINI_API_KEY.")
    by_id = {s["id"][:2]: s for s in SCENARIOS}
    done = set()
    for path in RAW.glob("*.json"):
        record = json.loads(path.read_text())
        if record.get("provider") == "gemini" and not provider_problems(record):
            done.add(record["id"])
    todo = [by_id[k] for k in FREE_PRIORITY if by_id[k]["id"] not in done]
    models, auditors, saved = list(FREE_MODELS), list(FREE_AUDIT_MODELS), []
    print(f"{len(done)} already finished, {len(todo)} to go. Writers {models}, auditors {auditors}", flush=True)
    for scenario in todo:
        retried = False
        while models and auditors:
            config.MODEL, config.AUDIT_MODEL = models[0], auditors[0]
            try:
                record = run(scenario)
                problems = provider_problems(record)
                audit_failed = any(e.get("audit_error") for e in record["trail"])
            except Exception as exc:
                problems, audit_failed = [f"{type(exc).__name__}: {str(exc)[:200]}"], False
            if not problems:
                (RAW / f"{scenario['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str))
                failed = [c["check"] for c in record["checks"] if not c["pass"]]
                print(f"{scenario['id']:40} {models[0]:22} audit {auditors[0]:24} {'PASS' if not failed else 'FAIL ' + str(failed)}  {record['seconds']}s", flush=True)
                saved.append(scenario["id"])
                break
            # A dropped connection is usually a blip, so give it one more try before moving on.
            if not retried and re.search(r"connection|timed out", problems[0], re.I):
                retried = True
                print(f"{scenario['id']:40} retrying after: {problems[0][:90]}", flush=True)
                continue
            retried = False
            pool = auditors if audit_failed else models
            print(f"{scenario['id']:40} NOT SAVED, dropping {pool[0]}: {problems[0][:100]}", flush=True)
            pool.pop(0)
        if not models or not auditors:
            print("Free quota used up for today. Run this again after the daily reset to continue.", flush=True)
            break
    return saved


def main() -> None:
    args = sys.argv[1:]
    RAW.mkdir(parents=True, exist_ok=True)
    if "--report" in args:
        report()
        return
    if "--free" in args:
        free_run()
        report()
        return
    if not config.api_ready():
        sys.exit("No API key found. Add DEEPSEEK_API_KEY, GEMINI_API_KEY, XAI_API_KEY or ANTHROPIC_API_KEY to .env first.")
    wanted = [a for a in args if not a.startswith("-")]
    todo = []
    for s in SCENARIOS:
        if wanted and not any(s["id"].startswith(w) for w in wanted):
            continue
        if (RAW / f"{s['id']}.json").exists() and "--force" not in args and not wanted:
            continue
        todo.append(s)
    print(f"Running {len(todo)} scenario(s)", flush=True)

    def work(s):
        try:
            if OUT_OF_CREDIT.is_set():
                print(f"{s['id']:42} SKIPPED: out of API credit", flush=True)
                return
            record = run_scenario(s)
            # A provider failure says nothing about the app, so it mustn't replace a real result.
            api_errors = [e["error"] for e in record["trail"] if e.get("error")]
            api_errors += [e["audit_error"] for e in record["trail"] if "402" in (e.get("audit_error") or "")]
            if api_errors:
                if any("402" in e or "quota" in e.lower() for e in api_errors):
                    OUT_OF_CREDIT.set()
                print(f"{s['id']:42} NOT SAVED: {api_errors[0][:120]}", flush=True)
                return
            (RAW / f"{s['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str))
            failed = [c["check"] for c in record["checks"] if not c["pass"]]
            print(f"{s['id']:42} {'PASS' if not failed else 'FAIL ' + str(failed)}  {record['seconds']}s  ${cost(record['usage']):.2f}", flush=True)
        except Exception as exc:
            if "402" in str(exc):
                OUT_OF_CREDIT.set()
            print(f"{s['id']:42} CRASH {type(exc).__name__}: {exc}", flush=True)
            (RAW / f"{s['id']}.crash.txt").write_text(traceback.format_exc())

    with cf.ThreadPoolExecutor(max_workers=config.SCENARIO_WORKERS) as pool:
        list(pool.map(work, todo))
    report()


if __name__ == "__main__":
    main()
