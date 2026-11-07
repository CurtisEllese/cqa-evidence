import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import type { Checklist } from "../domain/types";
import { fetchChecklists } from "../services/api";
import { Button, Notice } from "../components/ui";

export function EvaluationControls({
  latestVersion,
  available,
  busy,
  locked,
  hasEvaluation,
  onRun,
}: {
  latestVersion: number;
  available: boolean;
  busy: boolean;
  locked: boolean;
  hasEvaluation: boolean;
  onRun: (version: number) => Promise<void>;
}) {
  const [versions, setVersions] = useState<Checklist[]>([]);
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setSelected(0);
    void fetchChecklists(controller.signal)
      .then((saved) => {
        if (controller.signal.aborted) return;
        if (!saved.length) throw new Error("Нет сохранённых версий чек-листа.");
        const ordered = [...saved].sort((a, b) => b.version - a.version);
        setVersions(ordered);
        setSelected(ordered[0].version);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Не удалось загрузить версии.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [latestVersion, retry]);
  const disabled = busy || locked || !available;
  return (
    <div className="evaluation-controls">
      <label className="checklist-version-field">
        Версия для новой оценки
        <select
          aria-label="Версия для новой оценки"
          value={selected}
          disabled={disabled || loading || !!error}
          onChange={(event) => setSelected(Number(event.target.value))}
        >
          {selected === 0 && (
            <option value={0}>
              {loading ? "Загружаем версии…" : "Версия не выбрана"}
            </option>
          )}
          {versions.map((item) => (
            <option key={item.version} value={item.version}>
              v{item.version} · {item.name}
            </option>
          ))}
        </select>
      </label>
      {error && (
        <Notice kind="error">
          {error}{" "}
          <Button onClick={() => setRetry((value) => value + 1)}>
            Повторить загрузку версий
          </Button>
        </Notice>
      )}
      <div className="evaluation-actions">
        <Button
          variant="primary"
          disabled={disabled || loading || !!error || selected === 0}
          onClick={() => void onRun(selected)}
        >
          <Sparkles size={16} />
          {busy
            ? "Оцениваем…"
            : locked
              ? "Оценка зафиксирована"
              : !available
                ? "Оценка пока недоступна"
                : hasEvaluation
                  ? "Оценить заново"
                  : "Оценить диалог"}
        </Button>
        <span>
          {locked
            ? "Переоценка заблокирована: у диалога есть история апелляций. Для проверки другого чек-листа используйте новый диалог."
            : !available
              ? "Сервис автоматической оценки ещё не подключён"
              : "Сохранённый результат остаётся связан с версией, использованной при его оценке."}
        </span>
      </div>
    </div>
  );
}
