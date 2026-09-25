#!/usr/bin/env python3
"""Evaluate the REAL extraction pipeline (real LLM + validation + deterministic deadline resolver) on labelled emails.

    source scripts/dev-env.sh
    apps/api/.venv/bin/python -u scripts/eval_extraction.py [--provider gemini] [--model M] [--rpm 8] [--only id1,id2] [--out docs/eval/x.json]

Set GEMINI_THINKING_BUDGET= (blank) to send no thinkingConfig to a model that rejects it.

Each email in apps/api/tests/data/eval_emails.jsonl is synthetic and labelled by hand (expected values are NOT produced by the
code under test). Every check is graded independently and failures are printed with expected vs actual. Nothing is hidden: a
failing item is a finding, not something to tune away.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.config import get_settings  # noqa: E402
from app.models import User  # noqa: E402
from app.services.extraction.llm import build_llm_client  # noqa: E402
from app.services.extraction.schema import MessageEnvelope  # noqa: E402
from app.services.extraction.service import ExtractionStatus, extract_message  # noqa: E402

# When one of these comes back, every remaining item would fail the same way: stop instead of burning provider calls.
FATAL = {ExtractionStatus.LLM_QUOTA_EXHAUSTED, ExtractionStatus.LLM_REJECTED, ExtractionStatus.NOT_CONFIGURED}
# Failures of the provider rather than of the pipeline; they say nothing about detection quality and are reported separately.
INFRA = {ExtractionStatus.LLM_QUOTA_EXHAUSTED, ExtractionStatus.LLM_REJECTED, ExtractionStatus.NOT_CONFIGURED,
         ExtractionStatus.LLM_UNAVAILABLE, ExtractionStatus.LLM_TIMEOUT}
MAX_CONSECUTIVE_INFRA_FAILURES = 3
MODEL_SETTING = {"gemini": "gemini_model", "openai_compat": "llm_model"}

DATA_DIR = ROOT / "apps" / "api" / "tests" / "data"
DATASETS = {"dev": DATA_DIR / "eval_emails.jsonl", "heldout": DATA_DIR / "eval_emails_heldout.jsonl"}


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def parse_sender(raw: str) -> tuple[str | None, str | None]:
    if "<" in raw and raw.endswith(">"):
        name, email = raw[:-1].split("<", 1)
        return email.strip().lower(), name.strip() or None
    return raw.strip().lower(), None


def grade(item: dict, outcome) -> tuple[dict[str, bool], dict]:
    exp = item["expect"]
    checks: dict[str, bool] = {}
    got: dict = {"status": outcome.status.value}
    ok = outcome.status == ExtractionStatus.OK
    if not ok:
        got["error"] = outcome.error
        checks["pipeline_ok"] = False
        return checks, got
    a = outcome.analysis
    ext, res, dec = a.extraction, a.resolution, a.decision
    predicted_obligation = dec.action != "IGNORE"
    got.update(
        decision=dec.action, is_obligation=ext.is_obligation, confidence=ext.confidence, confidence_raw=a.validated.confidence_raw,
        type=ext.obligation_type.value, owner=ext.owner, priority=ext.priority.value, requires_confirmation=ext.requires_confirmation,
        deadline_text=ext.deadline_text, due_at=res.due_at.isoformat() if res.due_at else None,
        precision=res.precision.value if res.precision else None, ambiguous=res.ambiguous, ambiguity=res.ambiguity,
        recurrence=ext.recurrence.value if ext.recurrence else None, warnings=a.validated.warnings + res.warnings,
        reasons=dec.reasons, context_grounded=a.validated.context_grounded, deadline_grounded=a.validated.deadline_grounded,
        unknown_fields=a.validated.unknown_fields, counterparty_email=ext.counterparty_email,
    )
    checks["pipeline_ok"] = True
    checks["contract"] = not a.validated.unknown_fields
    if "is_obligation" in exp:
        checks["is_obligation"] = predicted_obligation == exp["is_obligation"]
    if "decision_in" in exp:
        checks["decision"] = dec.action in exp["decision_in"]
    if exp.get("is_obligation") is False:
        return checks, got
    if "due_at" in exp:
        if exp["due_at"] is None:
            checks["due"] = res.due_at is None
        else:
            checks["due"] = res.due_at == parse_utc(exp["due_at"])
    if "due_any_of" in exp:
        checks["due"] = res.due_at in [parse_utc(x) for x in exp["due_any_of"]]
    if "precision" in exp:
        checks["precision"] = (res.precision.value if res.precision else None) == exp["precision"]
    if "ambiguous" in exp:
        checks["ambiguity"] = res.ambiguous == exp["ambiguous"]
    if "type_any" in exp:
        checks["type"] = ext.obligation_type.value in exp["type_any"]
    if "owner" in exp:
        checks["owner"] = ext.owner == exp["owner"]
    if "confirm" in exp:
        checks["confirmation"] = ext.requires_confirmation == exp["confirm"]
    if "recurrence" in exp:
        checks["recurrence"] = (ext.recurrence.value if ext.recurrence else None) == exp["recurrence"]
    if predicted_obligation:
        checks["grounded"] = a.validated.context_grounded and (ext.deadline_text is None or a.validated.deadline_grounded)
    if exp.get("no_attacker_email"):
        checks["ignores_injection"] = (ext.counterparty_email or "") != "attacker@evil.example.net"
    return checks, got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["gemini", "openai_compat"])
    ap.add_argument("--dataset", choices=sorted(DATASETS), default="dev",
                    help="dev = the set the prompt was developed against; heldout = unseen emails that must not be tuned on")
    ap.add_argument("--model", help="override the provider's model name")
    ap.add_argument("--rpm", type=float, default=8.0, help="max requests per minute (free tiers are strict)")
    ap.add_argument("--only", help="comma-separated item ids")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args()

    settings = get_settings()
    if args.provider:
        settings = settings.model_copy(update={"llm_provider": args.provider})
    if args.model and settings.llm_provider in MODEL_SETTING:
        settings = settings.model_copy(update={MODEL_SETTING[settings.llm_provider]: args.model})
    llm = build_llm_client(settings)
    print(f"provider={llm.provider} model={llm.model} dataset={args.dataset} rpm<={args.rpm}")

    items = [json.loads(line) for line in DATASETS[args.dataset].read_text().splitlines() if line.strip()]
    dataset_size = len(items)
    if args.only:  # run in the order given, so the most valuable items go first when a daily quota might run out
        by_id = {i["id"]: i for i in items}
        unknown = [x for x in args.only.split(",") if x not in by_id]
        if unknown:
            raise SystemExit(f"unknown item ids: {', '.join(unknown)}")
        items = [by_id[x] for x in args.only.split(",")]
    if args.limit:
        items = items[: args.limit]

    user = User(id=uuid.uuid4(), email="eval@example.com", password_hash="x", timezone="America/New_York", preferences={})
    min_gap = 60.0 / args.rpm
    results = []
    last_call = 0.0
    aborted: str | None = None
    infra_streak = 0
    for n, item in enumerate(items, 1):
        wait = min_gap - (time.monotonic() - last_call)
        if last_call and wait > 0:
            time.sleep(wait)
        email, name = parse_sender(item["from"])
        received = parse_utc(item["received_at"])
        message = MessageEnvelope(
            source_type="WEBHOOK", external_id=item["id"], sender_email=email, sender_name=name, subject=item["subject"], body=item["body"],
            received_at=received, direction=item.get("direction", "INBOUND"),
        )
        now = parse_utc(item["now"]) if item.get("now") else received + timedelta(minutes=5)
        last_call = time.monotonic()
        started = time.perf_counter()
        outcome = extract_message(message, user, settings, llm, now)
        elapsed = time.perf_counter() - started
        checks, got = grade(item, outcome)
        passed = all(checks.values())
        results.append({"id": item["id"], "passed": passed, "checks": checks, "got": got, "expected": item["expect"], "seconds": round(elapsed, 2),
                        "attempts": outcome.attempts, "tokens": [outcome.input_tokens, outcome.output_tokens]})
        failed = [k for k, v in checks.items() if not v]
        print(f"[{n:2}/{len(items)}] {'PASS' if passed else 'FAIL'} {item['id']:24} {got.get('decision', got['status']):9} {elapsed:5.1f}s"
              + (f"   failed: {', '.join(failed)}" if failed else ""))
        infra_streak = infra_streak + 1 if outcome.status in INFRA else 0
        if outcome.status in FATAL or infra_streak >= MAX_CONSECUTIVE_INFRA_FAILURES:
            aborted = f"{outcome.status.value} after {len(results)} items: {outcome.error}"
            print(f"\nABORTING: {aborted}")
            break

    # ---------------------------------------------------------------- summary
    total = len(results)
    per_check: dict[str, list[bool]] = {}
    for r in results:
        for k, v in r["checks"].items():
            per_check.setdefault(k, []).append(v)
    answered = [r for r in results if r["got"]["status"] == ExtractionStatus.OK.value]  # provider failures say nothing about detection
    tp = sum(1 for r in answered if r["expected"].get("is_obligation") is True and r["got"]["decision"] != "IGNORE")
    fn = sum(1 for r in answered if r["expected"].get("is_obligation") is True and r["got"]["decision"] == "IGNORE")
    fp = sum(1 for r in answered if r["expected"].get("is_obligation") is False and r["got"]["decision"] != "IGNORE")
    tn = sum(1 for r in answered if r["expected"].get("is_obligation") is False and r["got"]["decision"] == "IGNORE")
    latencies = [r["seconds"] for r in answered] or [0.0]
    summary = {
        "provider": llm.provider, "model": llm.model, "dataset": args.dataset, "dataset_items": dataset_size, "items_run": total, "items_answered": len(answered),
        "aborted": aborted, "passed": sum(r["passed"] for r in results),
        "per_check": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(per_check.items())},
        "obligation_detection": {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                                 "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None},
        "latency_seconds": {"mean": round(statistics.mean(latencies), 2), "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2),
                            "max": max(latencies)},
        "repair_retries": sum(1 for r in results if r["attempts"] > 1),
        "tokens": {"input": sum(r["tokens"][0] or 0 for r in results), "output": sum(r["tokens"][1] or 0 for r in results)},
        "auto_created_wrongly": [r["id"] for r in results if r["got"].get("decision") == "CREATE" and "CREATE" not in r["expected"].get("decision_in", ["CREATE"])],
    }
    print("\n" + "=" * 100 + f"\nRESULT: {summary['passed']}/{total} items fully correct   (answered by the model: {len(answered)}, dataset: {dataset_size})")
    for k, v in summary["per_check"].items():
        print(f"  {k:16} {v}")
    print(f"  obligation detection: {summary['obligation_detection']}")
    print(f"  latency: {summary['latency_seconds']}   repair retries: {summary['repair_retries']}   tokens: {summary['tokens']}")
    print(f"  wrongly AUTO-CREATED (the costly error): {summary['auto_created_wrongly'] or 'none'}")

    failures = [r for r in results if not r["passed"]]
    if failures:
        print("\n" + "-" * 100 + "\nFAILURES (expected vs actual)")
        for r in failures:
            print(f"\n* {r['id']}   failed checks: {[k for k, v in r['checks'].items() if not v]}")
            e, g = r["expected"], r["got"]
            print(f"    expected: decision_in={e.get('decision_in')} due={e.get('due_at', e.get('due_any_of'))} ambiguous={e.get('ambiguous')} "
                  f"type_any={e.get('type_any')} owner={e.get('owner')}")
            print(f"    actual:   decision={g.get('decision')} due={g.get('due_at')} ({g.get('precision')}) ambiguous={g.get('ambiguous')} "
                  f"type={g.get('type')} owner={g.get('owner')} conf={g.get('confidence')} deadline_text={g.get('deadline_text')!r}")
            if g.get("ambiguity") or g.get("reasons"):
                print(f"    notes:    {g.get('ambiguity')} | {g.get('reasons')}")
            if g.get("error"):
                print(f"    error:    {g['error']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"summary": summary, "results": results, "generated_at": datetime.now(UTC).isoformat()}, indent=2, default=str))
        print(f"\nreport written to {args.out}")
    return 0 if not failures and not aborted else 1


if __name__ == "__main__":
    raise SystemExit(main())
