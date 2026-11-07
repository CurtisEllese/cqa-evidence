"""All labels in this test are synthetic software fixtures, never expert Ground Truth."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from evaluator.human_labels import (agreement, blank_label, build_report, validate_cases,
                                    validate_labels, validate_predictions)


def cases(language="RU", source="synthetic"):
    request = json.loads((Path(__file__).parents[1] / "evaluator/fixtures/synthetic_v2.json").read_text(encoding="utf-8"))[0]["request"]
    request["language"] = language
    request["checklist"]["criteria"] = [
        {"id": "behavior", "title": "Behavior", "description": "Synthetic behavior",
         "section": "Test", "type": "scale", "weight": "80", "max": "2", "allowedValues": ["0", "1", "2"], "allowNA": True, "naCondition": "No relevant action"},
        {"id": "resolution", "title": "Resolution", "description": "Synthetic resolution",
         "section": "Test", "type": "yes_no", "weight": "20", "max": "1", "allowedValues": ["0", "1"], "allowNA": False, "naCondition": None},
        {"id": "critical", "title": "Critical", "description": "Synthetic critical error",
         "section": "Test", "type": "flag", "weight": "0", "max": "1", "allowedValues": ["0", "1"], "allowNA": False, "naCondition": None}]
    return validate_cases([{"caseId": "fixture-1", "sourceKind": source, "sourceReference": "synthetic-only test", "split": "holdout", "request": request}])


def labels(cs):
    result = []
    for name in ("expert-A", "expert-B", "arbiter-C"):
        label = blank_label(cs[0], name)
        label["reviewedAt"] = "2026-11-08T10:00:00+05:00"
        for cid, j in label["judgments"].items():
            j.update(status="assessed", value="1", reason="Synthetic assertion", messageIds=[cs[0].request.messages[0].id])
        result.append(label)
    result[-1].update(phase="adjudicated", independentOfOtherAnnotators=False,
                      basedOn=[x["labelId"] for x in result[:2]])
    return result


def predictions(cs):
    case = cs[0]
    return [{"caseId": case.caseId, "caseHash": case.case_hash, "rubricHash": case.rubric_hash,
             "model": "gpt-6-luna", "promptVersion": "synthetic-fixture", "runId": "synthetic-test",
             "values": {"behavior": "2", "resolution": "1", "critical": "0"}}]


@pytest.mark.parametrize("language", ["RU", "KZ", "EN"])
def test_synthetic_never_claims_accuracy_and_metrics_are_known(language):
    cs = cases(language)
    report = build_report(cs, validate_labels(cs, labels(cs)), validate_predictions(cs, predictions(cs)))
    assert report["accuracyClaim"] == "not_established"
    assert report["realAdjudicatedCases"] == 0
    section = report["slices"]["synthetic"][f"holdout/{language}"]
    assert section["score"]["maePoints"] == 40
    assert section["criticalErrors"]["fn"] == 1
    assert section["criticalErrors"]["recall"] == 0
    assert section["criticalErrors"]["precision"] is None
    assert section["expertScoreAgreement"]["expert-A/expert-B"]["maePoints"] == 0
    assert section["expertCriticalAgreement"]["expert-A/expert-B"]["exactAgreement"] == 1


def test_no_labels_reports_no_metrics():
    report = build_report(cases(), [], [])
    assert report["accuracyClaim"] == "not_established"
    part = report["slices"]["synthetic"]["holdout/RU"]
    assert part["score"]["maePoints"] is None
    assert part["coverageGaps"] == [{"caseId": "fixture-1", "reason": "missing_adjudication"}]


def test_unassessable_is_not_na_or_zero_and_unknown_critical_has_no_recall():
    cs = cases()
    raw = labels(cs)
    raw[-1]["judgments"]["behavior"].update(status="unassessable", value=None)
    raw[-1]["judgments"]["critical"].update(status="unassessable", value=None)
    report = build_report(cs, validate_labels(cs, raw), validate_predictions(cs, predictions(cs)))
    part = report["slices"]["synthetic"]["holdout/RU"]
    assert part["score"]["pairedCases"] == 0
    assert "behavior" not in part["modelVsAdjudicatedByCriterion"]
    assert part["criticalErrors"]["recall"] is None
    assert part["criticalErrors"]["pairedDecisions"] == 0


def test_na_renormalizes_remaining_weight():
    cs = cases()
    raw = labels(cs)
    raw[-1]["judgments"]["behavior"]["value"] = None
    report = build_report(cs, validate_labels(cs, raw), validate_predictions(cs, predictions(cs)))
    assert report["slices"]["synthetic"]["holdout/RU"]["score"]["maePoints"] == 0


@pytest.mark.parametrize("change", [
    lambda rows: rows.append(copy.deepcopy(rows[0])),
    lambda rows: rows[0].update(caseHash="stale"),
    lambda rows: rows[0].update(rubricHash="stale"),
    lambda rows: rows[0].update(blindedToModel=False),
    lambda rows: rows[0].update(reviewedAt="2026-11-08"),
    lambda rows: rows[-1].update(annotatorId="expert-A"),
    lambda rows: rows[-1].update(basedOn=["absent", "missing"]),
    lambda rows: rows[-1].update(reviewedAt="2026-11-07T10:00:00Z"),
    lambda rows: rows[0]["judgments"].pop("critical"),
    lambda rows: rows[0]["judgments"]["critical"].update(value=None),
    lambda rows: rows[0]["judgments"]["behavior"].update(value="3"),
    lambda rows: rows[0]["judgments"]["behavior"].update(messageIds=["missing"]),
    lambda rows: rows[0]["judgments"]["behavior"].update(reason=""),
])
def test_invalid_or_nonindependent_labels_rejected(change):
    cs = cases()
    raw = labels(cs)
    change(raw)
    with pytest.raises(ValueError):
        validate_labels(cs, raw)


def test_actual_transcript_change_invalidates_labels_even_if_external_hash_unchanged():
    cs = cases()
    raw = labels(cs)
    cs[0].request.messages[0].text = "Different source"
    with pytest.raises(ValueError, match="stale"):
        validate_labels(cs, raw)


def test_kappa_and_degenerate_marginals():
    assert agreement([("0", "0"), ("0", "1"), ("1", "1"), ("1", "0")])["cohenKappa"] == 0
    assert agreement([("NA", "NA")])["cohenKappa"] is None
    assert agreement([])["exactAgreement"] is None


def test_prediction_rejects_unknown_model_and_stale_data():
    cs = cases()
    for field, value in (("model", "other-model"), ("caseHash", "stale"), ("rubricHash", "stale")):
        raw = predictions(cs)
        raw[0][field] = value
        with pytest.raises(ValueError):
            validate_predictions(cs, raw)


@pytest.mark.parametrize("human,model,cell", [("1", "1", "tp"), ("0", "1", "fp"),
                                             ("0", "0", "tn"), ("1", "0", "fn")])
def test_critical_confusion_matrix_all_cells(human, model, cell):
    cs = cases()
    raw_labels, raw_predictions = labels(cs), predictions(cs)
    raw_labels[-1]["judgments"]["critical"]["value"] = human
    raw_predictions[0]["values"]["critical"] = model
    part = build_report(cs, validate_labels(cs, raw_labels), validate_predictions(cs, raw_predictions))["slices"]["synthetic"]["holdout/RU"]
    assert part["criticalErrors"][cell] == 1
    assert sum(part["criticalErrors"][key] for key in ("tp", "fp", "tn", "fn")) == 1


def test_duplicate_text_and_model_feedback_cannot_enter_blind_holdout():
    raw = cases()[0].model_dump()
    duplicate = copy.deepcopy(raw)
    duplicate["caseId"] = "duplicate"
    duplicate["split"] = "calibration"
    duplicate["request"]["conversationId"] = "new-id-same-text"
    duplicate["request"]["conversationHash"] = "new-external-hash"
    with pytest.raises(ValueError, match="duplicate transcript"):
        validate_cases([raw, duplicate])
    raw["request"]["repair_feedback"] = []
    with pytest.raises(ValueError, match="feedback"):
        validate_cases([raw])


def test_offline_cli_roundtrip_and_no_overwrite(tmp_path):
    cs = cases()
    case_file, labels_file = tmp_path / "cases.json", tmp_path / "labels.json"
    case_file.write_text(json.dumps([c.model_dump() for c in cs]), encoding="utf-8")
    labels_file.write_text(json.dumps(labels(cs)), encoding="utf-8")
    script = str(Path(__file__).parents[1] / "scripts/expert_evaluation.py")
    packet, imported, report = (tmp_path / x for x in ("packet.json", "imported.json", "report.json"))
    for action, output, extra in (("prepare", packet, ["--annotator", "expert-D"]),
                                   ("import", imported, ["--labels", str(labels_file)]),
                                   ("report", report, ["--labels", str(imported)])):
        cmd = [sys.executable, script, action, "--cases", str(case_file), "--output", str(output), *extra]
        assert subprocess.run(cmd, capture_output=True).returncode == 0
        before = output.read_bytes()
        assert subprocess.run(cmd, capture_output=True).returncode == 2
        assert output.read_bytes() == before
    assert "predictions" not in json.loads(packet.read_text())
    assert json.loads(report.read_text())["accuracyClaim"] == "not_established"
