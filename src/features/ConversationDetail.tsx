import { useState } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronRight,
  Clock3,
  FileDown,
  Flag,
  Headphones,
  Link2,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import type { Conversation, Criterion } from "../domain/types";
import { useStore } from "../store";
import {
  Avatar,
  Badge,
  Button,
  EmptyState,
  Modal,
  Notice,
  Score,
} from "../components/ui";
import { downloadExport } from "../services/api";
import { evaluationLabel, evaluationTone } from "./evaluationStatus";

export function ConversationDetail({
  id,
  onBack,
  onAppeals,
}: {
  id: string;
  onBack: () => void;
  onAppeals: () => void;
}) {
  const {
    data,
    evaluate,
    evaluationAvailable,
    evaluatingIds,
    evaluationErrors,
  } = useStore();
  const conversation = data.conversations.find((item) => item.id === id);
  const [highlight, setHighlight] = useState<string[]>([]);
  const [appealCriterion, setAppealCriterion] = useState<Criterion | null>(
    null,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [tab, setTab] = useState("results");
  if (!conversation)
    return (
      <EmptyState
        title="Диалог не найден"
        description="Диалог отсутствует в текущем наборе данных."
      >
        <Button onClick={onBack}>К диалогам</Button>
      </EmptyState>
    );
  const evaluation = conversation.evaluation;
  const evaluating = busy || evaluatingIds.includes(id);
  const completed = evaluation?.status === "completed";
  const original = conversation.scores?.original;
  const final = conversation.scores?.final;
  const appeals = data.appeals.filter(
    (item) =>
      item.conversationId === id && item.evaluationId === evaluation?.id,
  );
  const hasAccepted = appeals.some((a) => a.status === "accepted");
  async function runEvaluation() {
    if (!evaluationAvailable || appeals.length > 0 || evaluating) return;
    setBusy(true);
    setError("");
    try {
      await evaluate(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выполнить оценку");
    } finally {
      setBusy(false);
    }
  }
  async function exportConversation() {
    if (exporting) return;
    setExporting(true);
    setError("");
    try {
      await downloadExport(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выгрузить диалог.");
    } finally {
      setExporting(false);
    }
  }
  function showEvidence(ids: string[]) {
    setHighlight(ids);
    if (ids.length) {
      const element = document.getElementById(`message-${ids[0]}`);
      element?.scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "center",
      });
      element?.focus({ preventScroll: true });
    }
  }
  return (
    <>
      <button className="back-link" onClick={onBack}>
        <ArrowLeft size={16} />
        Все диалоги
      </button>
      <div className="detail-heading">
        <div>
          <div className="eyebrow">ДИАЛОГ · {conversation.id}</div>
          <h1>{conversation.title}</h1>
          <div className="detail-metadata">
            <span>
              {conversation.channel === "voice call" ? (
                <Headphones size={15} />
              ) : (
                <MessageSquare size={15} />
              )}{" "}
              {conversation.channel === "voice call" ? "Звонок" : "Чат"}
            </span>
            <span>{conversation.language}</span>
            <span>
              <Clock3 size={15} />
              {conversation.duration}
            </span>
            <Badge tone={evaluationTone(evaluation)}>
              {evaluationLabel(evaluation)}
            </Badge>
          </div>
        </div>
        <Button disabled={exporting} onClick={() => void exportConversation()}>
          <FileDown size={16} />
          {exporting ? "Выгружаем…" : "Экспорт"}
        </Button>
      </div>
      <div className="detail-grid">
        <section className="panel transcript-panel">
          <div className="panel-heading">
            <div className="operator-cell">
              <Avatar name={conversation.initials} />
              <div>
                <h2>{conversation.operator}</h2>
                <p>
                  Оператор ·{" "}
                  {conversation.date
                    ? new Intl.DateTimeFormat("ru-RU", {
                        dateStyle: "long",
                        timeZone: "Asia/Qyzylorda",
                      }).format(new Date(conversation.date))
                    : "Дата не указана"}
                </p>
              </div>
            </div>
            <ShieldCheck size={21} className="muted" />
          </div>
          {conversation.channel === "voice call" && (
            <div className="audio-panel">
              <Headphones size={20} />
              {conversation.audioUrl ? (
                <div>
                  <span>Запись разговора</span>
                  <audio controls preload="none" src={conversation.audioUrl}>
                    Ваш браузер не поддерживает аудио.
                  </audio>
                  <a
                    href={
                      conversation.audioPageUrl ??
                      `/api/conversations/${encodeURIComponent(conversation.id)}/audio-page`
                    }
                    target="_blank"
                    rel="noreferrer"
                  >
                    Открыть запись <ArrowUpRight size={14} />
                  </a>
                </div>
              ) : (
                <div>
                  <strong>Транскрипт звонка</strong>
                  <span>Ссылка на запись не предоставлена</span>
                </div>
              )}
            </div>
          )}
          <div className="transcript-toolbar">
            <span>{conversation.transcript.length} реплик</span>
            <span>Исходная транскрипция</span>
            {highlight.length > 0 && (
              <button
                onClick={() => setHighlight([])}
                aria-label="Убрать подсветку"
              >
                <X size={15} />
              </button>
            )}
          </div>
          <div className="transcript">
            {conversation.transcript.map((message) => (
              <div
                id={`message-${message.id}`}
                tabIndex={-1}
                key={message.id}
                className={`message message-${message.speaker} ${highlight.includes(message.id) ? "message-highlight" : ""}`}
              >
                <div className="message-meta">
                  <span>
                    {message.speaker === "operator"
                      ? conversation.operator
                      : message.speaker === "client"
                        ? "Клиент"
                        : message.speaker === "bot"
                          ? "Бот"
                          : "Система"}
                  </span>
                  <time>
                    {message.timestamp.includes("T")
                      ? new Intl.DateTimeFormat("ru-RU", {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                          timeZone: "Asia/Qyzylorda",
                        }).format(new Date(message.timestamp))
                      : message.timestamp}
                  </time>
                </div>
                <div className="message-bubble">{message.text}</div>
                {highlight.includes(message.id) && (
                  <span className="evidence-label">
                    <Link2 size={12} />
                    Доказательство по критерию
                  </span>
                )}
              </div>
            ))}
          </div>
        </section>
        <section className="evaluation-panel">
          <div className="panel score-summary">
            <div>
              <div className="eyebrow">
                {hasAccepted ? "ФИНАЛЬНАЯ ОЦЕНКА" : "ОЦЕНКА КАЧЕСТВА"}
              </div>
              <Score value={final?.total ?? null} size="large" />
              {hasAccepted && (
                <div className="original-score">
                  Исходная оценка: {original?.total ?? "—"}
                </div>
              )}
            </div>
            <div className="score-summary-meta">
              <Badge tone={evaluationTone(evaluation)}>
                <Sparkles size={12} />
                {evaluationLabel(evaluation)}
              </Badge>
              <span>
                {evaluation
                  ? `Чек-лист v${evaluation.checklist.version}`
                  : "Оценка ещё не запущена"}
              </span>
              <span>
                {final
                  ? `${final.applicable} применимых критериев`
                  : `${data.checklist.criteria.length} критериев`}
              </span>
            </div>
            {final?.critical && (
              <div className="critical-notice">
                <Flag size={15} />
                Отмечена грубая ошибка
              </div>
            )}
          </div>
          <div className="evaluation-actions">
            <Button
              variant="primary"
              onClick={runEvaluation}
              disabled={
                evaluating || !evaluationAvailable || appeals.length > 0
              }
            >
              <Sparkles size={16} />{" "}
              {evaluating
                ? "Оцениваем…"
                : !evaluationAvailable
                  ? "Оценка пока недоступна"
                  : appeals.length > 0
                    ? "Оценка зафиксирована"
                    : evaluation &&
                        (evaluation.status === "queued" ||
                          evaluation.status === "running")
                      ? "Проверить результат"
                      : evaluation
                        ? "Обновить оценку"
                        : "Оценить диалог"}
            </Button>
            <span>
              {appeals.length
                ? "История апелляций сохранена"
                : !evaluationAvailable
                  ? "Сервис автоматической оценки ещё не подключён"
                  : "На текущей версии чек-листа"}
            </span>
          </div>
          {(error || evaluationErrors[id]) && (
            <Notice kind="error">{error || evaluationErrors[id]}</Notice>
          )}
          {evaluation && !completed && (
            <Notice
              kind={
                evaluation.status === "needs_review" ||
                evaluation.status === "queued" ||
                evaluation.status === "running"
                  ? "warning"
                  : "error"
              }
            >
              {evaluation.status === "needs_review"
                ? "Доказательства требуют проверки. Итоговый балл не опубликован; неподтверждённые ссылки не подсвечиваются."
                : evaluation.status === "queued" ||
                    evaluation.status === "running"
                  ? "Сервер обрабатывает диалог. Результат появится после проверки ответа модели."
                  : "Оценка не завершена. Итоговый балл не опубликован."}
              {evaluation.error && <p>{evaluation.error}</p>}
            </Notice>
          )}
          {!evaluation ? (
            <div className="panel">
              <EmptyState
                title="Всё начинается с оценки"
                description={
                  evaluationAvailable
                    ? "Запустите оценку, чтобы получить проверяемые ответы по критериям и ссылки на реплики."
                    : "Диалог доступен для просмотра. Оценки и доказательства появятся после подключения сервиса автоматической оценки."
                }
              />
            </div>
          ) : (
            <>
              <div
                className="detail-tabs"
                role="tablist"
                aria-label="Результат диалога"
              >
                <button
                  role="tab"
                  aria-selected={tab === "results"}
                  onClick={() => setTab("results")}
                >
                  Критерии <span>{evaluation.results.length}</span>
                </button>
                <button
                  role="tab"
                  aria-selected={tab === "history"}
                  onClick={() => setTab("history")}
                >
                  История апелляций <span>{appeals.length}</span>
                </button>
              </div>
              {tab === "results" ? (
                <div className="criterion-results">
                  {completed && (
                    <section className="panel section-scores">
                      <h3>Результаты по разделам</h3>
                      {[
                        ...new Set(
                          evaluation.checklist.criteria
                            .map((c) => c.section)
                            .filter(Boolean),
                        ),
                      ].map((section) => {
                        const criteria = evaluation.checklist.criteria.filter(
                          (c) => c.section === section,
                        );
                        const score = conversation.scores?.sections[section];
                        if (!score) return null;
                        return (
                          <div className="section-score-row" key={section}>
                            <span>{section}</span>
                            <strong>
                              {criteria.every(
                                (criterion) => criterion.type === "flag",
                              )
                                ? score.critical
                                  ? "Есть нарушение"
                                  : "Нет нарушений"
                                : score.total === null
                                  ? "N/A"
                                  : `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(score.total)} / 100`}
                            </strong>
                          </div>
                        );
                      })}
                    </section>
                  )}
                  {(final?.results ?? evaluation.results).map(
                    (result, index) => {
                      const criterion = evaluation.checklist.criteria.find(
                        (c) => c.id === result.criterionId,
                      );
                      if (!criterion) return null;
                      const existing = appeals.find(
                        (a) => a.criterionId === criterion.id,
                      );
                      const passed =
                        criterion.type === "flag"
                          ? result.value === 0
                          : result.value === criterion.max;
                      return (
                        <article
                          className="panel result-card"
                          key={result.criterionId}
                        >
                          <div className="result-card-top">
                            <span
                              className={`result-indicator ${result.value === null ? "na" : passed ? "pass" : "partial"}`}
                            >
                              {result.value === null ? (
                                "—"
                              ) : passed ? (
                                <Check size={14} />
                              ) : (
                                <Flag size={13} />
                              )}
                            </span>
                            <h3>
                              <span>{String(index + 1).padStart(2, "0")}</span>
                              {criterion.title}
                            </h3>
                            <strong>
                              {result.value === null
                                ? "N/A"
                                : criterion.type === "flag"
                                  ? result.value === 1
                                    ? "Да · нарушение"
                                    : "Нет · без нарушения"
                                  : criterion.type === "binary"
                                    ? result.value === 1
                                      ? "Да"
                                      : "Нет"
                                    : `${result.value} / ${criterion.max}`}
                            </strong>
                          </div>
                          <p>{result.explanation}</p>
                          {result.value === null &&
                            result.applicabilityReason && (
                              <p>Не применимо: {result.applicabilityReason}</p>
                            )}
                          {result.evidence.map((evidence, evidenceIndex) => (
                            <blockquote
                              className="review-evidence"
                              key={evidenceIndex}
                            >
                              <small>
                                {evidence.verified
                                  ? "Проверенное доказательство"
                                  : "Доказательство не подтверждено"}
                                {evidence.kind === "absence"
                                  ? " · отсутствие в проверенном участке"
                                  : evidence.kind === "not_applicable"
                                    ? " · неприменимость"
                                    : ""}
                              </small>
                              {evidence.quote && <p>{evidence.quote}</p>}
                              {evidence.sourceText &&
                                evidence.sourceText !== evidence.quote && (
                                  <details>
                                    <summary>Исходный текст</summary>
                                    <p>{evidence.sourceText}</p>
                                  </details>
                                )}
                              {evidence.reason && <p>{evidence.reason}</p>}
                            </blockquote>
                          ))}
                          <div className="result-evidence">
                            {result.evidenceIds.length > 0 ? (
                              <button
                                onClick={() => showEvidence(result.evidenceIds)}
                              >
                                <Link2 size={14} />
                                {result.value === 0
                                  ? "Проверенный участок"
                                  : "Показать доказательство"}
                                <ChevronRight size={14} />
                              </button>
                            ) : (
                              <span>Нет привязанных реплик</span>
                            )}
                          </div>
                          <div className="result-card-bottom">
                            <span>
                              {criterion.type === "flag" ? (
                                "Флаг нарушения · вес 0 · не влияет на балл"
                              ) : (
                                <>
                                  Вес {criterion.weight}
                                  {result.value === null
                                    ? " · исключён из расчёта"
                                    : ""}
                                </>
                              )}
                            </span>
                            {existing ? (
                              <button
                                className="appeal-status-link"
                                onClick={onAppeals}
                              >
                                {existing.status === "pending"
                                  ? "На рассмотрении"
                                  : existing.status === "accepted"
                                    ? "Пересмотрено"
                                    : "Подтверждено"}
                                <ArrowUpRight size={13} />
                              </button>
                            ) : completed ? (
                              <button
                                onClick={() => setAppealCriterion(criterion)}
                              >
                                Оспорить
                                <Flag size={12} />
                              </button>
                            ) : (
                              <span>Сначала требуется проверенная оценка</span>
                            )}
                          </div>
                        </article>
                      );
                    },
                  )}
                  {completed && (
                    <Notice>
                      Оценка рассчитана по сохранённой версии чек-листа.
                      Принятые апелляции учитываются в итоговом балле.
                    </Notice>
                  )}
                </div>
              ) : (
                <div className="panel history-panel">
                  {appeals.length ? (
                    appeals.map((appeal) => (
                      <article className="history-item" key={appeal.id}>
                        <Badge
                          tone={
                            appeal.status === "pending"
                              ? "amber"
                              : appeal.status === "accepted"
                                ? "green"
                                : "neutral"
                          }
                        >
                          {appeal.status === "pending"
                            ? "На рассмотрении"
                            : appeal.status === "accepted"
                              ? "Оценка изменена"
                              : "Оценка подтверждена"}
                        </Badge>
                        <h3>
                          {
                            evaluation.checklist.criteria.find(
                              (c) => c.id === appeal.criterionId,
                            )?.title
                          }
                        </h3>
                        <p>{appeal.comment}</p>
                        {appeal.decision && (
                          <blockquote>{appeal.decision}</blockquote>
                        )}
                        <small>
                          Исходное значение: {appeal.originalValue ?? "N/A"}
                          {appeal.status === "accepted"
                            ? ` → ${appeal.newValue ?? "N/A"}`
                            : ""}
                        </small>
                      </article>
                    ))
                  ) : (
                    <EmptyState
                      title="Пока без апелляций"
                      description="Если оценка вызывает вопросы, её можно оспорить по отдельному критерию."
                    />
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>
      {appealCriterion && (
        <AppealDialog
          conversation={conversation}
          criterion={appealCriterion}
          onClose={() => setAppealCriterion(null)}
        />
      )}
    </>
  );
}
function AppealDialog({
  conversation,
  criterion,
  onClose,
}: {
  conversation: Conversation;
  criterion: Criterion;
  onClose: () => void;
}) {
  const { createAppeal } = useStore();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const result = conversation.evaluation!.results.find(
    (r) => r.criterionId === criterion.id,
  )!;
  async function submit() {
    if (comment.trim().length < 10 || busy) return;
    setBusy(true);
    setError("");
    try {
      await createAppeal({
        conversationId: conversation.id,
        evaluationId: conversation.evaluation!.id,
        criterionId: criterion.id,
        comment: comment.trim(),
      });
      onClose();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось отправить апелляцию.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Оспорить оценку"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="appeal-criterion">
        <Flag size={19} />
        <div>
          <strong>{criterion.title}</strong>
          <span>
            Исходная оценка: {result.value ?? "N/A"} · вес {criterion.weight}
          </span>
        </div>
      </div>
      <label className="field-label" htmlFor="appeal-comment">
        Почему оценку нужно пересмотреть?
      </label>
      <textarea
        id="appeal-comment"
        disabled={busy}
        rows={5}
        value={comment}
        maxLength={2000}
        onChange={(e) => setComment(e.target.value)}
        placeholder="Укажите, какие реплики или обстоятельства не были учтены…"
      />
      <div className="field-hint">
        Минимум 10 символов · {comment.length}/2000
      </div>
      <Notice>
        Проверяющий увидит критерий, ваше обращение и исходный диалог.
      </Notice>
      <div className="modal-actions">
        <Button disabled={busy} onClick={onClose}>
          Отмена
        </Button>
        <Button
          variant="primary"
          disabled={busy || comment.trim().length < 10}
          onClick={submit}
        >
          {busy ? "Отправляем…" : "Отправить апелляцию"}
          <ArrowUpRight size={16} />
        </Button>
      </div>
      {error && <Notice kind="error">{error}</Notice>}
    </Modal>
  );
}
