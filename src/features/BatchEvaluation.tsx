import { useEffect, useState } from "react";
import {
  ArrowRight,
  CheckCheck,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import type { Checklist } from "../domain/types";
import { fetchChecklists } from "../services/api";
import { useStore } from "../store";
import { Button, Notice } from "../components/ui";
import { planBatch, isUncertainEvaluation } from "./batchPlan";
import { conversationTitle } from "./conversationTitle";

export function BatchEvaluation({ onOpen }: { onOpen: (id: string) => void }) {
  const {
    data,
    datasets,
    selectedGroup,
    setSelectedGroup,
    setFilters,
    batch,
    startBatch,
    stopBatch,
    resumeBatch,
    cancelBatch,
    batchReady,
    batchError,
    evaluatingIds,
    evaluationAvailable,
    loading,
  } = useStore();
  const [versions, setVersions] = useState<Checklist[]>([]);
  const [version, setVersion] = useState(0);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [mode, setMode] = useState("new");
  useEffect(() => {
    const controller = new AbortController();
    setVersion(0);
    setError("");
    void fetchChecklists(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        const ordered = items.sort((a, b) => b.version - a.version);
        if (!ordered.length) throw new Error("Сначала сохраните чек-лист.");
        setVersions(ordered);
        setVersion(ordered[0].version);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Не удалось загрузить версии чек-листа.",
          );
      });
    return () => controller.abort();
  }, [data.checklist.version, retry]);
  const { selected, locked, candidates, failed } = planBatch(
    data,
    datasets,
    selectedGroup,
    mode,
  );
  const completed = selected.filter(
    (item) => item.evaluation?.status === "completed",
  ).length;
  const needsReview = selected.filter(
    (item) => item.evaluation?.status === "needs_review",
  ).length;
  const busy = !!batch?.running || evaluatingIds.length > 0 || loading;
  const canResume = !busy && batchReady && !batchError && evaluationAvailable;
  const canRun =
    canResume &&
    version > 0 &&
    !error &&
    (!batch || batch.status === "completed");
  const terminal =
    batch?.items.filter((item) => !["queued", "running"].includes(item.status))
      .length ?? 0;
  const remaining =
    batch?.items.filter((item) => item.status === "queued") ?? [];
  const progress = batch
    ? Math.round((terminal / batch.items.length) * 100)
    : 0;
  function run(ids: string[]) {
    void startBatch(selectedGroup, ids, version);
  }
  return (
    <section className="panel batch-panel" aria-labelledby="batch-title">
      <div className="batch-heading">
        <div>
          <h2 id="batch-title">Оценка диалогов</h2>
          <p>Выберите набор и чек-лист для оценки.</p>
        </div>
        <ShieldCheck size={30} aria-hidden="true" />
      </div>
      <div className="batch-fields">
        <label>
          Набор диалогов
          <select
            value={selectedGroup}
            disabled={busy}
            onChange={(event) => {
              setSelectedGroup(event.target.value);
              setFilters({ page: 0 });
            }}
          >
            <option value="">Выберите загруженный набор</option>
            {datasets.map((item) => (
              <option key={item.group} value={`group:${item.group}`}>
                {item.group} · {item.total} диалогов
              </option>
            ))}
            <option value="workspace:">
              Всё рабочее пространство · {data.conversations.length}
            </option>
          </select>
        </label>
        <label>
          Сохранённый чек-лист
          <select
            value={version}
            disabled={busy || !version}
            onChange={(event) => setVersion(Number(event.target.value))}
          >
            {!version && (
              <option value={0}>
                {error ? "Версии недоступны" : "Загружаем версии…"}
              </option>
            )}
            {versions.map((item) => (
              <option key={item.version} value={item.version}>
                v{item.version} · {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Состав запуска
          <select
            value={mode}
            disabled={busy}
            onChange={(event) => setMode(event.target.value)}
          >
            <option value="new">Только без оценки</option>
            <option value="all">Все доступные, включая оценённые</option>
          </select>
        </label>
      </div>
      {error && (
        <Notice kind="error">
          {error}{" "}
          <Button onClick={() => setRetry((n) => n + 1)}>
            Повторить загрузку
          </Button>
        </Notice>
      )}
      {batchError && (
        <Notice kind="warning">
          Связь с очередью потеряна: {batchError} Оценка на сервере
          продолжается. Состояние обновится после восстановления связи.
        </Notice>
      )}
      {!batchReady && <Notice>Подключаемся к сохранённой очереди…</Notice>}
      {!evaluationAvailable && (
        <Notice>
          Автоматическая оценка временно недоступна. Обратитесь к
          администратору.
        </Notice>
      )}
      {selectedGroup && (
        <>
          <div className="batch-metrics">
            <span>
              <strong>{selected.length}</strong> в наборе
            </span>
            <span>
              <strong>{completed}</strong> оценено
            </span>
            <span>
              <strong>{needsReview}</strong> требуют проверки
            </span>
            <span>
              <strong>{failed.length}</strong> с ошибкой
            </span>
          </div>
          <div className="batch-actions">
            <Button
              variant="primary"
              disabled={!canRun || !candidates.length}
              onClick={() => run(candidates.map((item) => item.id))}
            >
              <Play size={16} />
              Оценить набор · {candidates.length}
            </Button>
            <Button
              disabled={!canRun || !failed.length}
              onClick={() => run(failed.map((item) => item.id))}
            >
              <RotateCcw size={16} />
              Повторить ошибки · {failed.length}
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                setFilters({
                  status: "review",
                  critical: "all",
                  page: 0,
                  filtersOpen: true,
                })
              }
            >
              Требуют проверки <ArrowRight size={16} />
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                setFilters({ critical: "present", status: "all", page: 0 })
              }
            >
              Грубые ошибки <ArrowRight size={16} />
            </Button>
          </div>
          <p className="batch-note">
            Новый запуск: {candidates.length} диалогов, чек-лист v
            {version || "—"}. Диалоги с историей апелляций (
            {selected.filter((item) => locked.has(item.id)).length}) защищены от
            переоценки. Поиск и фильтры таблицы не меняют состав запуска.
          </p>
          {selected.some((item) => isUncertainEvaluation(item.evaluation)) && (
            <Notice kind="warning">
              Диалоги с неопределённым результатом исключены из массового
              запуска. Проверьте их результат и расходы перед отдельной
              переоценкой.
            </Notice>
          )}
          {mode === "all" && (
            <Notice kind="warning">
              Для уже оценённых диалогов будет создана новая оценка. Прежние
              результаты останутся в истории.
            </Notice>
          )}
          <details className="batch-composition">
            <summary>Посмотреть состав запуска ({candidates.length})</summary>
            <ul>
              {candidates.map((item) => (
                <li key={item.id}>
                  {item.id} · {conversationTitle(item)}
                </li>
              ))}
              {!candidates.length && (
                <li>
                  Нет подходящих диалогов. Выберите другой набор или состав
                  запуска.
                </li>
              )}
            </ul>
          </details>
        </>
      )}
      {batch && (
        <div className="batch-progress" aria-label="Ход оценки набора">
          <div className="batch-progress-heading">
            <div>
              <strong>
                {batch.running
                  ? batch.stopping
                    ? "Останавливаем очередь…"
                    : "Оцениваем набор"
                  : remaining.length
                    ? "Очередь остановлена"
                    : "Запуск завершён"}
              </strong>
              <p>
                {batch.group === "workspace:"
                  ? "Всё рабочее пространство"
                  : batch.group.replace(/^group:/, "")}{" "}
                · чек-лист v{batch.version} · обработано {terminal} из{" "}
                {batch.items.length}
              </p>
            </div>
            {batch.running && (
              <Button disabled={batch.stopping} onClick={stopBatch}>
                <Pause size={16} />
                Остановить очередь
              </Button>
            )}
          </div>
          <progress
            max={batch.items.length}
            value={terminal}
            aria-label="Обработанные диалоги"
          />
          <span className="batch-progress-label" role="status">
            {progress}% · готовы:{" "}
            {batch.items.filter((item) => item.status === "completed").length} ·
            требуют проверки:{" "}
            {
              batch.items.filter((item) => item.status === "needs_review")
                .length
            }{" "}
            · ошибки:{" "}
            {batch.items.filter((item) => item.status === "failed").length} ·
            результат неизвестен:{" "}
            {batch.items.filter((item) => item.status === "uncertain").length}
          </span>
          <p className="batch-note">
            Оценка продолжится, даже если закрыть страницу. Остановка завершит
            текущий диалог и приостановит оставшиеся.
          </p>
          {batch.blockedReason && (
            <Notice kind="warning">
              {batch.blockedReason} Неопределённые запросы не повторяются при
              продолжении; сначала проверьте их результат и расходы.
            </Notice>
          )}
          {!batch.running && remaining.length > 0 && (
            <Button disabled={!canResume} onClick={resumeBatch}>
              <Play size={16} />
              Продолжить оставшиеся · {remaining.length}
            </Button>
          )}
          {remaining.length > 0 && (
            <Button
              disabled={!batchReady || !!batchError}
              onClick={cancelBatch}
            >
              Отменить оставшиеся · {remaining.length}
            </Button>
          )}
          <details className="batch-composition">
            <summary>
              <CheckCheck size={16} /> Результаты запуска
            </summary>
            <ul>
              {batch.items.map((item) => (
                <li key={item.id}>
                  <button className="text-link" onClick={() => onOpen(item.id)}>
                    {item.id}
                  </button>
                  <span>
                    {
                      {
                        queued: "Ожидает запуска",
                        running: "Оценивается",
                        completed: "Готово",
                        needs_review: "Требует проверки",
                        failed: "Ошибка",
                        uncertain: "Статус нужно уточнить",
                        cancelled: "Отменено до отправки",
                      }[item.status]
                    }
                  </span>
                  {item.error && <p>{item.error}</p>}
                </li>
              ))}
            </ul>
          </details>
          {batch.items.some((item) => item.status === "uncertain") && (
            <Notice kind="warning">
              Результат одного из запросов неизвестен. Проверьте этот диалог и
              расходы перед отдельным повтором.
            </Notice>
          )}
        </div>
      )}
    </section>
  );
}
