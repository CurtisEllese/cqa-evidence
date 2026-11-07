"""Offline CLI: prepare blinded packets, validate/import labels, compare a frozen run."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluator.human_labels import (blank_label, build_report, validate_cases,
                                    validate_labels, validate_predictions)


def read(path):
    if Path(path).stat().st_size > 20 * 1024 * 1024:
        raise ValueError("input exceeds 20 MiB")
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_new(path, value):
    # Never overwrite labels, prior reports, DEV data, or another reviewer's packet.
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "import", "report"])
    parser.add_argument("--cases", required=True)
    parser.add_argument("--labels")
    parser.add_argument("--predictions")
    parser.add_argument("--annotator")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        cases = validate_cases(read(args.cases))
        if args.action == "prepare":
            if not args.annotator or not args.annotator.strip():
                raise ValueError("--annotator is required")
            value = {"notice": "Blind independent annotation. No predictions or reference answers. Fill templates and export labels array for import.",
                     "cases": [c.model_dump() for c in cases],
                     "labels": [blank_label(c, args.annotator) for c in cases]}
        else:
            labels = validate_labels(cases, read(args.labels) if args.labels else [])
            if args.action == "import":
                if not args.labels:
                    raise ValueError("--labels is required")
                value = [label.model_dump() for label in labels]
            else:
                predictions = validate_predictions(cases, read(args.predictions) if args.predictions else [])
                value = build_report(cases, labels, predictions)
        write_new(args.output, value)
    except (ValueError, OSError, TypeError, KeyError):
        # No transcript or validation input echoed to terminal/logs.
        parser.exit(2, "Invalid input or output already exists. Check the offline annotation contract; no source files were modified.\n")
    print(f"Offline {args.action} completed. No API calls.")


if __name__ == "__main__":
    main()
