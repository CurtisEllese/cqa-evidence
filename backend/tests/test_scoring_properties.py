"""Arithmetic invariants on varying rubrics, independent of provider output."""
from copy import deepcopy
from decimal import Decimal, localcontext

from hypothesis import given, strategies as st

from backend.domain import score_evaluation


def evaluation(weights, values):
    criteria = [{"id": str(i), "title": "Synthetic", "description": "Synthetic criterion", "section": "test", "type": "scale", "weight": weight, "max": 3, "allowedValues": [0, 1, 2, 3], "allowNA": True, "naCondition": "Not relevant"} for i, weight in enumerate(weights)]
    return {"id": "synthetic", "source": "api", "createdAt": "2026-11-07T00:00:00Z", "checklist": {"id": "rubric", "name": "Synthetic", "version": 1, "criteria": criteria}, "results": [{"criterionId": str(i), "value": value, "explanation": "Synthetic explanation", "evidenceIds": ["synthetic-m1"]} for i, value in enumerate(values)]}


@given(st.lists(st.tuples(st.integers(0, 1000), st.one_of(st.none(), st.integers(0, 3))), min_size=1, max_size=20))
def test_bounded_score_and_na_denominator_are_invariant_to_order(rows):
    item = evaluation([weight for weight, _ in rows], [value for _, value in rows])
    original = deepcopy(item)
    score = score_evaluation(item, wire_decimals=True)
    possible = sum(weight for weight, value in rows if value is not None)
    assert Decimal(score["possible"]) == possible
    assert (score["total"] is None) == (possible == 0)
    if possible:
        assert 0 <= Decimal(score["total"]) <= 100
    item["checklist"]["criteria"].reverse()
    item["results"].reverse()
    reversed_score = score_evaluation(item, wire_decimals=True)
    for field in ("total", "possible", "applicable", "critical"):
        assert score[field] == reversed_score[field]
    item["checklist"]["criteria"].reverse()
    item["results"].reverse()
    assert item == original


def test_wire_arithmetic_never_roundtrips_earned_through_binary_float():
    score = score_evaluation(evaluation([0.1], [1]), wire_decimals=True)
    with localcontext() as context:
        context.prec = 50
        expected = Decimal(1) / Decimal(3) * Decimal("0.1")
    assert score["earned"] == format(expected, "f")
    assert len(score["earned"]) > 40
    assert score["total"] == "33.33"
