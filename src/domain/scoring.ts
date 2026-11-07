import type { Appeal, Criterion, CriterionResult, Evaluation } from "./types";

export type EvaluationScore = {
  total: number | null;
  earned: number;
  possible: number;
  applicable: number;
  critical: boolean;
  results: CriterionResult[];
};

function validateValue(criterion: Criterion, value: number | null): void {
  const invalid =
    value === null
      ? !criterion.allowNA
      : !Number.isFinite(value) ||
        value < 0 ||
        value > criterion.max ||
        ((criterion.type === "binary" || criterion.type === "flag") &&
          value !== 0 &&
          value !== 1) ||
        (criterion.allowedValues !== undefined &&
          !criterion.allowedValues.includes(value));
  if (invalid)
    throw new Error(
      `Недопустимое значение критерия «${criterion.title}»: ${value === null ? "N/A" : value}.`,
    );
}

/** Deterministic UI recalculation using the saved rubric, also used for appeal previews. */
export function scoreEvaluation(
  evaluation: Evaluation,
  appeals: Appeal[] = [],
): EvaluationScore {
  const criterionIds = new Set(
    evaluation.checklist.criteria.map((criterion) => criterion.id),
  );
  if (criterionIds.size !== evaluation.checklist.criteria.length)
    throw new Error("В чек-листе повторяются идентификаторы критериев.");
  const resultIds = new Set(
    evaluation.results.map((result) => result.criterionId),
  );
  if (
    resultIds.size !== evaluation.results.length ||
    resultIds.size !== criterionIds.size ||
    [...resultIds].some((id) => !criterionIds.has(id))
  ) {
    throw new Error(
      "Результат оценки должен содержать ровно один ответ для каждого критерия чек-листа.",
    );
  }
  const results = evaluation.results.map((result) => ({
    ...result,
    evidenceIds: [...result.evidenceIds],
  }));
  for (const criterion of evaluation.checklist.criteria) {
    if (
      !Number.isFinite(criterion.weight) ||
      criterion.weight < 0 ||
      !Number.isFinite(criterion.max) ||
      criterion.max <= 0 ||
      (criterion.type === "flag" && criterion.weight !== 0)
    ) {
      throw new Error(
        `Некорректные вес или максимум критерия «${criterion.title}».`,
      );
    }
    const result = results.find((item) => item.criterionId === criterion.id)!;
    validateValue(criterion, result.value);
    result.critical = criterion.type === "flag" && result.value === 1;
  }
  // Decisions apply only to the precise saved evaluation. The last accepted decision wins.
  for (const appeal of appeals) {
    if (
      appeal.status !== "accepted" ||
      appeal.evaluationId !== evaluation.id ||
      appeal.newValue === undefined
    )
      continue;
    const criterion = evaluation.checklist.criteria.find(
      (item) => item.id === appeal.criterionId,
    );
    const result = results.find(
      (item) => item.criterionId === appeal.criterionId,
    );
    if (!criterion || !result) continue;
    validateValue(criterion, appeal.newValue);
    result.value = appeal.newValue;
    result.critical = criterion.type === "flag" && appeal.newValue === 1;
    result.explanation =
      appeal.decision || "Оценка изменена по принятой апелляции.";
  }
  let earned = 0;
  let possible = 0;
  let applicable = 0;
  let critical = false;
  for (const criterion of evaluation.checklist.criteria) {
    const result = results.find((item) => item.criterionId === criterion.id)!;
    if (criterion.type === "flag") {
      critical ||= result.value === 1;
      continue;
    }
    if (result.value === null || criterion.weight === 0) continue;
    earned += (result.value / criterion.max) * criterion.weight;
    possible += criterion.weight;
    applicable += 1;
  }
  return {
    total:
      possible === 0 ? null : Math.round((earned / possible) * 10000) / 100,
    earned,
    possible,
    applicable,
    critical,
    results,
  };
}
