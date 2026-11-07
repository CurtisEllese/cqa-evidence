"""Offline, provenance-bound expert annotation and comparison. Never calls an AI."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from itertools import combinations
import json
from typing import Literal

from pydantic import Field, TypeAdapter, model_validator

from backend.contracts.v2.models import Contract, DecimalString, EvaluationRequest, NonEmpty


def digest(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


class AnnotationCase(Contract):
    caseId: NonEmpty
    sourceKind: Literal["real", "synthetic"]
    sourceReference: NonEmpty
    split: Literal["calibration", "holdout"]
    request: EvaluationRequest

    @property
    def case_hash(self):
        # Bind actual contents as well as externally supplied conversationHash.
        return digest(self.model_dump())

    @property
    def rubric_hash(self):
        return digest(self.request.checklist.model_dump())


class Judgment(Contract):
    status: Literal["assessed", "unassessable"]
    value: DecimalString | None
    reason: NonEmpty
    messageIds: list[NonEmpty] = Field(min_length=1)
    sourceRefs: list[NonEmpty] = Field(default_factory=list)

    @model_validator(mode="after")
    def unknown_is_not_na(self):
        if self.status == "unassessable" and self.value is not None:
            raise ValueError("unassessable cannot have a score")
        return self


class HumanLabel(Contract):
    labelId: NonEmpty
    caseId: NonEmpty
    caseHash: NonEmpty
    rubricHash: NonEmpty
    annotatorId: NonEmpty
    phase: Literal["independent", "adjudicated"]
    blindedToModel: Literal[True]
    independentOfOtherAnnotators: bool
    reviewedAt: NonEmpty
    basedOn: list[NonEmpty]
    judgments: dict[str, Judgment]

    @model_validator(mode="after")
    def provenance(self):
        timestamp = datetime.fromisoformat(self.reviewedAt.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("reviewedAt needs a timezone")
        if self.phase == "independent" and (self.basedOn or not self.independentOfOtherAnnotators):
            raise ValueError("independent labels must be blind and independently authored")
        if self.phase == "adjudicated" and (len(set(self.basedOn)) < 2 or self.independentOfOtherAnnotators):
            raise ValueError("adjudication needs at least two independent label IDs")
        return self


class Prediction(Contract):
    caseId: NonEmpty
    caseHash: NonEmpty
    rubricHash: NonEmpty
    model: Literal["gpt-6-luna"]
    promptVersion: NonEmpty
    runId: NonEmpty
    values: dict[str, DecimalString | None]


def validate_cases(raw) -> list[AnnotationCase]:
    cases = TypeAdapter(list[AnnotationCase]).validate_python(raw)
    if len({case.caseId for case in cases}) != len(cases):
        raise ValueError("duplicate case ID")
    # Repeated transcripts must not masquerade as independent sample observations.
    if len({(case.request.conversationId, case.request.conversationHash) for case in cases}) != len(cases):
        raise ValueError("duplicate conversation")
    if len({case.rubric_hash for case in cases}) > 1:
        raise ValueError("report one frozen checklist revision at a time")
    if any(case.request.repair_feedback is not None for case in cases):
        raise ValueError("blind packets must exclude model repair feedback")
    if len({digest([m.model_dump(exclude={"id", "message_index"}) for m in case.request.messages]) for case in cases}) != len(cases):
        raise ValueError("duplicate transcript contents must not cross calibration and holdout")
    return cases


def _values_valid(case, values, *, unknown=()):
    criteria = {c.id: c for c in case.request.checklist.criteria}
    if values.keys() != criteria.keys():
        raise ValueError("criteria must cover the frozen checklist exactly")
    for cid, value in values.items():
        if cid in unknown:
            continue
        c = criteria[cid]
        if value is None:
            if not c.allowNA:
                raise ValueError("N/A is forbidden for this criterion")
        elif Decimal(value) not in map(Decimal, c.allowedValues):
            raise ValueError("score is outside the frozen checklist")


def _bound(case, record):
    if case.case_hash != record.caseHash or case.rubric_hash != record.rubricHash:
        raise ValueError("stale case or checklist; re-annotate the frozen revision")


def validate_labels(cases, raw) -> list[HumanLabel]:
    labels = TypeAdapter(list[HumanLabel]).validate_python(raw)
    by_case = {c.caseId: c for c in cases}
    by_id = {label.labelId: label for label in labels}
    if len(by_id) != len(labels):
        raise ValueError("duplicate label ID")
    seen, adjudicated = set(), set()
    for label in labels:
        if label.caseId not in by_case:
            raise ValueError("unknown case")
        case = by_case[label.caseId]
        _bound(case, label)
        key = (label.caseId, label.annotatorId)
        if key in seen:
            raise ValueError("one annotation per person per case; adjudicator must be a third person")
        seen.add(key)
        _values_valid(case, {k: v.value for k, v in label.judgments.items()},
                      unknown={k for k, v in label.judgments.items() if v.status == "unassessable"})
        message_ids = {m.id for m in case.request.messages}
        for judgment in label.judgments.values():
            if not set(judgment.messageIds) <= message_ids:
                raise ValueError("unknown evidence message ID")
        if label.phase == "adjudicated":
            if label.caseId in adjudicated:
                raise ValueError("only one final adjudication per case")
            adjudicated.add(label.caseId)
            parents = [by_id.get(identifier) for identifier in label.basedOn]
            if any(p is None or p.phase != "independent" or p.caseId != label.caseId for p in parents):
                raise ValueError("adjudication references invalid independent labels")
            if len({p.annotatorId for p in parents}) < 2:
                raise ValueError("two distinct experts are required")
            if any(datetime.fromisoformat(p.reviewedAt.replace("Z", "+00:00")) >
                   datetime.fromisoformat(label.reviewedAt.replace("Z", "+00:00")) for p in parents):
                raise ValueError("adjudication precedes its independent labels")
    return labels


def validate_predictions(cases, raw) -> list[Prediction]:
    predictions = TypeAdapter(list[Prediction]).validate_python(raw)
    by_case = {c.caseId: c for c in cases}
    if len({p.caseId for p in predictions}) != len(predictions):
        raise ValueError("one prediction per case per report")
    if len({(p.model, p.promptVersion, p.runId) for p in predictions}) > 1:
        raise ValueError("do not pool different model/prompt runs")
    for prediction in predictions:
        if prediction.caseId not in by_case:
            raise ValueError("unknown predicted case")
        _bound(by_case[prediction.caseId], prediction)
        _values_valid(by_case[prediction.caseId], prediction.values)
    return predictions


def blank_label(case: AnnotationCase, annotator: str):
    return {"labelId": f"{case.caseId}-{annotator}", "caseId": case.caseId,
            "caseHash": case.case_hash, "rubricHash": case.rubric_hash,
            "annotatorId": annotator, "phase": "independent", "blindedToModel": True,
            "independentOfOtherAnnotators": True, "reviewedAt": "FILL_ISO_TIMESTAMP_WITH_TIMEZONE",
            "basedOn": [], "judgments": {c.id: {"status": "unassessable", "value": None,
                "reason": "", "messageIds": [], "sourceRefs": []} for c in case.request.checklist.criteria}}


def _category(judgment):
    if judgment.status == "unassessable":
        return "UNASSESSABLE"
    return "NA" if judgment.value is None else str(Decimal(judgment.value).normalize())


def agreement(pairs):
    n = len(pairs)
    if not n:
        return {"pairedDecisions": 0, "exactAgreement": None, "cohenKappa": None,
                "categoryCountsA": {}, "categoryCountsB": {}}
    a, b = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    observed = sum(x == y for x, y in pairs) / n
    expected = sum(a[x] * b[x] for x in a.keys() | b.keys()) / (n * n)
    return {"pairedDecisions": n, "exactAgreement": observed,
            "categoryCountsA": dict(a), "categoryCountsB": dict(b),
            "cohenKappa": (observed - expected) / (1 - expected) if expected < 1 else None}


def _score(case, values, unknown=()):
    # This is a separate reference calculation using the frozen public rubric.
    if any(c.id in unknown and c.type != "flag" and Decimal(c.weight) > 0
           for c in case.request.checklist.criteria):
        return None
    earned, possible = Decimal(0), Decimal(0)
    for c in case.request.checklist.criteria:
        value = values[c.id]
        if c.type == "flag" or value is None:
            continue
        possible += Decimal(c.weight)
        earned += Decimal(c.weight) * Decimal(value) / Decimal(c.max)
    return float((100 * earned / possible).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if possible else None


def _critical(case, values, unknown=()):
    flags = [c.id for c in case.request.checklist.criteria if c.type == "flag"]
    if any(cid not in unknown and values[cid] is not None and Decimal(values[cid]) == 1 for cid in flags):
        return True
    return None if not flags or any(cid in unknown for cid in flags) else False


def build_report(cases, labels, predictions):
    # Validate even callers that construct model instances directly.
    cases = validate_cases([c.model_dump() for c in cases])
    labels = validate_labels(cases, [l.model_dump() for l in labels])
    predictions = validate_predictions(cases, [p.model_dump() for p in predictions])
    sections = {}
    for source in ("real", "synthetic"):
        sections[source] = {}
        for split in ("calibration", "holdout"):
            for language in ("RU", "KZ", "EN"):
                subset = [c for c in cases if c.sourceKind == source and c.split == split
                          and c.request.language == language]
                sections[source][f"{split}/{language}"] = _slice(subset, labels, predictions)
    real_refs = sum(l.phase == "adjudicated" and next(c for c in cases if c.caseId == l.caseId).sourceKind == "real" for l in labels)
    return {"schemaVersion": "human-evaluation-1", "realAdjudicatedCases": real_refs,
            "datasetHash": digest([c.model_dump() for c in sorted(cases, key=lambda c: c.caseId)]),
            "labelSetHash": digest([l.model_dump() for l in sorted(labels, key=lambda l: l.labelId)]),
            "predictionSetHash": digest([p.model_dump() for p in sorted(predictions, key=lambda p: p.caseId)]),
            "accuracyClaim": "not_established" if real_refs == 0 else "sample_comparison_only_not_hidden_Freedom_accuracy",
            "notice": "Synthetic results are regression checks. Human identity, independence, expertise and representativeness require external audit. No hidden Freedom labels are fabricated.",
            "slices": sections}


def _slice(cases, labels, predictions):
    by_prediction = {p.caseId: p for p in predictions}
    expert_pairs, expert_totals, expert_critical, criterion_comparisons, errors, gaps = {}, {}, {}, {}, [], []
    confusion = Counter({"tp": 0, "fp": 0, "tn": 0, "fn": 0})
    score_errors, critical_pairs, adjudicated_count, predicted_count = [], [], 0, 0
    for case in cases:
        local = [l for l in labels if l.caseId == case.caseId]
        independent = sorted((l for l in local if l.phase == "independent"), key=lambda l: l.annotatorId)
        for a, b in combinations(independent, 2):
            pair_key = f"{a.annotatorId}/{b.annotatorId}"
            # Keep observer pairs separate: pooled pair decisions are not independent samples.
            for cid in a.judgments:
                expert_pairs.setdefault(pair_key, {}).setdefault(cid, []).append((_category(a.judgments[cid]), _category(b.judgments[cid])))
            compared = []
            for label in (a, b):
                vals = {cid: j.value for cid, j in label.judgments.items()}
                missing = {cid for cid, j in label.judgments.items() if j.status == "unassessable"}
                compared.append((_score(case, vals, missing), _critical(case, vals, missing)))
            if all(score is not None for score, _ in compared):
                expert_totals.setdefault(pair_key, []).append(abs(compared[0][0] - compared[1][0]))
            if all(critical is not None for _, critical in compared):
                expert_critical.setdefault(pair_key, []).append(tuple(str(critical) for _, critical in compared))
        final = next((l for l in local if l.phase == "adjudicated"), None)
        prediction = by_prediction.get(case.caseId)
        adjudicated_count += final is not None
        predicted_count += prediction is not None
        if final is None or prediction is None:
            gaps.append({"caseId": case.caseId, "reason": "missing_adjudication" if final is None else "missing_prediction"})
            continue
        unknown = {cid for cid, j in final.judgments.items() if j.status == "unassessable"}
        values = {cid: j.value for cid, j in final.judgments.items()}
        for cid, j in final.judgments.items():
            if cid in unknown:
                gaps.append({"caseId": case.caseId, "criterionId": cid, "reason": "expert_unassessable"})
                continue
            expected, actual = _category(j), "NA" if prediction.values[cid] is None else str(Decimal(prediction.values[cid]).normalize())
            criterion_comparisons.setdefault(cid, []).append((expected, actual))
            if expected != actual:
                errors.append({"caseId": case.caseId, "criterionId": cid, "human": expected, "model": actual})
        human_score = _score(case, values, unknown)
        model_score = _score(case, prediction.values)
        if human_score is not None and model_score is not None:
            score_errors.append(abs(human_score - model_score))
        hc, pc = _critical(case, values, unknown), _critical(case, prediction.values)
        if hc is not None and pc is not None:
            confusion[("t" if hc == pc else "f") + ("p" if pc else "n")] += 1
            critical_pairs.append((str(hc), str(pc)))
    tp, fp, fn = (confusion[k] for k in ("tp", "fp", "fn"))
    return {"cases": len(cases), "adjudicatedCases": adjudicated_count, "predictedCases": predicted_count,
            "expertAgreement": {pair: {cid: agreement(rows) for cid, rows in criteria.items()} for pair, criteria in expert_pairs.items()},
            "expertScoreAgreement": {pair: {"pairedCases": len(rows), "maePoints": sum(rows) / len(rows)} for pair, rows in expert_totals.items()},
            "expertCriticalAgreement": {pair: agreement(rows) for pair, rows in expert_critical.items()},
            "modelVsAdjudicatedByCriterion": {cid: agreement(rows) for cid, rows in criterion_comparisons.items()},
            "score": {"pairedCases": len(score_errors), "maePoints": sum(score_errors) / len(score_errors) if score_errors else None,
                      "maxAbsoluteErrorPoints": max(score_errors) if score_errors else None},
            "criticalErrors": {**confusion, **agreement(critical_pairs),
                               "precision": tp / (tp + fp) if tp + fp else None,
                               "recall": tp / (tp + fn) if tp + fn else None},
            "disagreements": errors, "coverageGaps": gaps}
