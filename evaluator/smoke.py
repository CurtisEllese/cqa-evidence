"""Explicitly opt-in live checks. Defaults to three synthetic labeled dialogues."""
import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from contracts.v1 import EvaluatorInput, validate_output
from .gemini import EvaluationFailure, EvaluatorSettings, GeminiEvaluator


def compare(request, output, expected):
    """Exact agreement and scale error; N/A is not replaced with zero."""
    actual = {r.criterion_id: r.value for r in output.results}
    criteria = {c.id: c for c in request.checklist.criteria}
    if set(expected) != set(criteria):
        raise ValueError("Labels must cover exactly the checklist criteria")
    for cid, value in expected.items():
        c = criteria[cid]
        if value is None:
            if not c.allow_na: raise ValueError("Reference uses forbidden N/A")
        elif type(value) is not int or value not in c.allowed_values:
            raise ValueError("Reference uses invalid scale step")
    errors = [abs(actual[k] - expected[k]) / criteria[k].max for k in expected
              if actual[k] is not None and expected[k] is not None and criteria[k].type != "flag"]
    return {
        "matched": sum(actual[k] == expected[k] for k in expected), "criteria": len(expected),
        "na_mismatches": sum((actual[k] is None) != (expected[k] is None) for k in expected),
        "flag_mismatches": sum(actual[k] != expected[k] for k in expected if criteria[k].type == "flag"),
        "normalized_mae_applicable": sum(errors) / len(errors) if errors else None,
        "differences": [{"criterion_id": k, "expected": expected[k], "actual": actual[k]}
                        for k in expected if actual[k] != expected[k]],
    }


async def run(args):
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)
    cases = json.loads(args.cases.read_text())
    # Validate every case/label before consuming any quota.
    selected = []
    for case in cases:
        req = EvaluatorInput.model_validate(case["request"])
        if req.conversation.language in args.languages:
            from contracts.v1 import ModelOutput
            # Use expected values to validate labels; evidence is not evaluated here.
            skeleton = ModelOutput.model_validate({"schema_version": "1.0", "results": [
                {"criterion_id": k, "value": v, "explanation": "reference", "na_reason": None,
                 "evidence": {"kind": "absence", "message_ids": [], "start_message_id": None,
                              "end_message_id": None, "quote": None}}
                for k, v in case["expected"].items()]})
            compare(req, skeleton, case["expected"])
            selected.append((req, case["expected"]))
    if not selected:
        raise ValueError("No selected cases")
    events = []
    judge = GeminiEvaluator(EvaluatorSettings.from_env(), on_attempt=events.append)
    rows = []
    try:
        for req, expected in selected:
            started = time.monotonic()
            try:
                result = await judge.evaluate(req)
                report = validate_output(result.output, req.checklist, req.conversation)
                rows.append({"language": req.conversation.language, "channel": req.conversation.channel,
                             "status": report.status, "seconds": round(time.monotonic() - started, 2),
                             "provenance": result.provenance.model_dump(),
                             "comparison": compare(req, result.output, expected),
                             "evidence_issues": [i.model_dump() for i in report.evidence_issues]})
            except EvaluationFailure as exc:
                rows.append({"language": req.conversation.language, "status": "failed", "error": exc.error.model_dump()})
    finally:
        await judge.aclose()
    summary = {"notice": "Synthetic references are smoke tests, NOT organizer Ground Truth or a quality benchmark.",
               "cases": rows, "attempts": [asdict(e) for e in events]}
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n")
    print(text)
    return 0 if all(r["status"] == "valid" and r["comparison"]["matched"] == r["comparison"]["criteria"] for r in rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Actually call Gemini and consume project quota")
    parser.add_argument("--cases", type=Path, default=Path(__file__).parent / "fixtures/synthetic.json")
    parser.add_argument("--languages", nargs="+", choices=["RU", "KZ", "EN"], default=["RU", "KZ", "EN"])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live explicitly to allow API calls; offline tests require no key.")
    try:
        return asyncio.run(run(args))
    except ValueError:
        print("Invalid configuration or test input. Check server environment and fixture schema.")
        return 2
    except Exception:
        # Provider/SDK exception text can contain request data; do not dump it to CI.
        print("Unexpected adapter failure. Inspect locally without logging keys or transcript contents.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
