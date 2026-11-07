"""Generate portable JSON Schemas. Semantic validators remain server-side."""
import json
from pathlib import Path
from .v1 import Checklist, ModelOutput, Evaluation, ErrorResponse, EvaluationJob, EvaluatorInput

def main():
    directory = Path(__file__).parent / "schemas"
    directory.mkdir(exist_ok=True)
    for name, cls in {"checklist": Checklist, "model-output": ModelOutput, "evaluation": Evaluation, "error": ErrorResponse, "evaluation-job": EvaluationJob, "evaluator-input": EvaluatorInput}.items():
        schema = cls.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        (directory / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
