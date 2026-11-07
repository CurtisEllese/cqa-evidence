import type { AppData, Evaluation } from "../domain/types";
import type { DatasetInfo } from "../services/api";

export function isEvaluationFailure(evaluation: Evaluation | null | undefined) {
  return (
    !!evaluation &&
    ["failed", "failed_validation", "retryable"].includes(evaluation.status)
  );
}

/** Account/provider failures affect the entire queue, not just one dialogue. */
export function isBatchBlockingFailure(
  evaluation: Evaluation | null | undefined,
) {
  if (!isEvaluationFailure(evaluation)) return false;
  const code = evaluation?.errorCode ?? "";
  return (
    code.startsWith("BUDGET_") ||
    code.startsWith("PROVIDER_") ||
    code === "MODEL_NOT_ALLOWED"
  );
}

/** Preview only: this function never starts model requests. */
export function planBatch(
  data: AppData,
  datasets: DatasetInfo[],
  selection: string,
  mode: string,
) {
  const group = datasets.find((item) => `group:${item.group}` === selection);
  const ids = new Set(group?.conversationIds ?? []);
  const selected =
    selection === "workspace:"
      ? data.conversations
      : data.conversations.filter((item) => ids.has(item.id));
  const locked = new Set(data.appeals.map((item) => item.conversationId));
  const eligible = selected.filter(
    (item) =>
      !locked.has(item.id) &&
      !["queued", "running"].includes(item.evaluation?.status ?? ""),
  );
  return {
    group,
    selected,
    locked,
    candidates: eligible.filter((item) => mode === "all" || !item.evaluation),
    failed: eligible.filter((item) => isEvaluationFailure(item.evaluation)),
  };
}
