import { useEffect, useState } from "react";
import type { Conversation, Evaluation } from "../domain/types";
import { fetchEvaluationHistory } from "../services/api";
import { Button, Notice } from "../components/ui";
import { evaluationLabel } from "./evaluationStatus";

export function EvaluationHistory({
  conversation,
  onEvidence,
}: {
  conversation: Conversation;
  onEvidence: (ids: string[]) => void;
}) {
  const [entries, setEntries] = useState<Evaluation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void fetchEvaluationHistory(
      conversation.id,
      conversation.transcript,
      controller.signal,
    )
      .then((rows) => {
        if (!controller.signal.aborted) setEntries(rows);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Не удалось загрузить историю.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [
    conversation.id,
    conversation.evaluation?.id,
    conversation.evaluation?.status,
    opened,
    retry,
  ]);
  return (
    <section
      className="panel evaluation-history"
      aria-label="История запусков оценки"
    >
      <Button
        onClick={() => setOpened((value) => !value)}
        aria-expanded={opened}
      >
        {opened ? "Скрыть историю оценок" : "История оценок"}
      </Button>
      {opened && (
        <>
          <p>
            Сохранённые исходные результаты всех запусков. Решения проверяющего
            доступны в истории апелляций.
          </p>
          {loading && <p role="status">Загружаем историю…</p>}
          {error && (
            <Notice kind="error">
              {error}{" "}
              <Button onClick={() => setRetry((value) => value + 1)}>
                Повторить
              </Button>
            </Notice>
          )}
          {!loading && !error && !entries.length && (
            <p>Оценка ещё не запускалась.</p>
          )}
          {!loading &&
            !error &&
            entries.map((entry) => (
              <details key={entry.id} className="history-entry">
                <summary>
                  Чек-лист v{entry.checklist.version} · {evaluationLabel(entry)}{" "}
                  · {entry.total ?? "—"} / 100 ·{" "}
                  {new Date(entry.createdAt).toLocaleString("ru-RU")}
                </summary>
                {entry.error && <Notice kind="error">{entry.error}</Notice>}
                {entry.results.map((result) => {
                  const criterion = entry.checklist.criteria.find(
                    (item) => item.id === result.criterionId,
                  );
                  const value =
                    result.assessmentStatus === "unassessable"
                      ? "Недостаточно данных"
                      : result.value === null
                      ? "N/A"
                      : criterion?.type === "binary" ||
                          criterion?.type === "flag"
                        ? result.value === 1
                          ? "Да"
                          : "Нет"
                        : `${result.value} / ${criterion?.max}`;
                  return (
                    <article key={result.criterionId}>
                      <h3>
                        {criterion?.title ?? result.criterionId}: {value}
                      </h3>
                      <p>{result.explanation}</p>
                      {result.assessmentStatus === "unassessable" && result.reviewReason && (
                        <p>Причина: {result.reviewReason}</p>
                      )}
                      {result.assessmentStatus !== "unassessable" && result.applicabilityReason && (
                        <p>N/A: {result.applicabilityReason}</p>
                      )}
                      <Button
                        disabled={!result.evidenceIds.length}
                        onClick={() => onEvidence(result.evidenceIds)}
                      >
                        Показать реплики
                      </Button>
                    </article>
                  );
                })}
              </details>
            ))}
        </>
      )}
    </section>
  );
}
