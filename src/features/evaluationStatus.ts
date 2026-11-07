import type { Evaluation } from "../domain/types";
export function evaluationLabel(evaluation: Evaluation | null): string {
  if (!evaluation) return "Ожидает оценки";
  return {
    queued: "В очереди",
    running: "Оценивается",
    completed: "Оценён",
    needs_review: "Требует проверки",
    failed_validation: "Ответ не прошёл проверку",
    failed: "Ошибка оценки",
    retryable: "Нужна повторная попытка",
  }[evaluation.status];
}
export function evaluationTone(
  evaluation: Evaluation | null,
): "green" | "amber" | "red" | "neutral" {
  if (!evaluation) return "neutral";
  if (evaluation.status === "completed") return "green";
  if (["failed", "failed_validation", "retryable"].includes(evaluation.status))
    return "red";
  return "amber";
}
