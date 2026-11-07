"""Explicit live smoke for the application's active v2 contract; synthetic by default."""
import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from dotenv import load_dotenv
from backend.contracts.v2 import EvaluationRequest
from backend.evaluator import AdapterFailure, inspect_output
from .settings import EvaluatorSettings, OpenAISettings
from .openai_v2 import OpenAIV2Evaluator
from .v2 import GeminiV2Evaluator


async def run(args):
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / '.env.local', override=False)
    load_dotenv(root / '.env', override=False)
    cases = json.loads(args.cases.read_text())
    prepared = [(EvaluationRequest.model_validate(c['request']), c['expected']) for c in cases]
    events = []
    provider = os.getenv("LLM_PROVIDER", "openai")
    if provider == "openai":
        judge = OpenAIV2Evaluator(OpenAISettings.from_env(), on_attempt=events.append)
    elif provider == "gemini":
        judge = GeminiV2Evaluator(EvaluatorSettings.from_env(), on_attempt=events.append)
    else:
        raise ValueError("Unsupported LLM_PROVIDER")
    rows = []
    for request, expected in prepared:
        if set(expected) != {c.id for c in request.checklist.criteria}:
            raise ValueError('Reference must cover every criterion')
        try:
            response = await judge.evaluate(request)
            _, errors, reviews = inspect_output(response, request)
            actual = {r.criterionId: r.value for r in response.results}
            rows.append({'language':request.language,'channel':request.channel,
                         'status':'invalid' if errors else 'needs_review' if reviews else 'valid',
                         'matched':sum(actual.get(k) == v for k,v in expected.items()),'criteria':len(expected),
                         'errors':errors,'evidence_issues':reviews,
                         'differences':[{'criterion':k,'expected':v,'actual':actual.get(k)} for k,v in expected.items() if actual.get(k)!=v]})
        except json.JSONDecodeError:
            rows.append({"language":request.language,"status":"failed","code":"MODEL_OUTPUT_INVALID"})
        except AdapterFailure as exc:
            rows.append({'language':request.language,'status':'failed','code':exc.code})
    summary={'notice':'Synthetic integration checks, not independent accuracy or Ground Truth.',
             'cases':rows,'attempts':[asdict(e) for e in events]}
    text=json.dumps(summary,ensure_ascii=False,indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(text+'\n')
    print(text)
    return 0 if all(r['status']=='valid' and r['matched']==r['criteria'] for r in rows) else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--cases',type=Path,default=Path(__file__).parent/'fixtures/synthetic_v2.json')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if not args.live: parser.error('Explicit --live is required to call the provider')
    try: return asyncio.run(run(args))
    except Exception:
        print('Configuration or adapter error; no raw provider response is logged.')
        return 2


if __name__=='__main__': raise SystemExit(main())
