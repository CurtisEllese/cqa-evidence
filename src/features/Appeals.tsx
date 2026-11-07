import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  Check,
  Flag,
  MessageSquare,
  ShieldCheck,
} from "lucide-react";
import { useStore } from "../store";
import {
  Avatar,
  Badge,
  Button,
  EmptyState,
  Modal,
  Notice,
  PageHeading,
  Score,
} from "../components/ui";
import { previewAppeal } from "../services/api";
import { isDecimalText } from "../services/decimal";
import type { AppealDecision } from "../services/api";
import type { Appeal, EvaluationScore } from "../domain/types";

export function Appeals({ onOpen }: { onOpen: (id: string) => void }) {
  const { data } = useStore();
  const [filter, setFilter] = useState("pending");
  const [selected, setSelected] = useState<Appeal | null>(null);
  const appeals = data.appeals.filter(
    (item) => filter === "all" || item.status === filter,
  );
  return (
    <>
      <PageHeading
        eyebrow="ОБРАТНАЯ СВЯЗЬ"
        title="Каждая оценка заслуживает внимания"
        description="Рассматривайте спорные критерии и сохраняйте обоснованные решения."
      />
      <div className="appeals-intro">
        <ShieldCheck size={23} />
        <div>
          <strong>Прозрачность на каждом этапе</strong>
          <p>
            Исходная оценка, комментарий оператора и решение проверяющего
            сохраняются вместе.
          </p>
        </div>
      </div>
      <div className="list-tabs" role="tablist" aria-label="Статус апелляции">
        {[
          ["pending", "На рассмотрении"],
          ["accepted", "Принятые"],
          ["rejected", "Отклонённые"],
          ["all", "Все"],
        ].map(([value, label]) => (
          <button
            key={value}
            role="tab"
            aria-selected={filter === value}
            onClick={() => setFilter(value)}
          >
            {label}
            <span>
              {
                data.appeals.filter(
                  (a) => value === "all" || a.status === value,
                ).length
              }
            </span>
          </button>
        ))}
      </div>
      <div className="appeals-grid">
        {appeals.map((appeal) => {
          const conversation = data.conversations.find(
            (c) => c.id === appeal.conversationId,
          );
          const evaluation = conversation?.evaluation;
          const criterion = evaluation?.checklist.criteria.find(
            (c) => c.id === appeal.criterionId,
          );
          return (
            <article className="panel appeal-card" key={appeal.id}>
              <div className="appeal-card-header">
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
                      ? "Принята"
                      : "Отклонена"}
                </Badge>
                <span>
                  {new Intl.DateTimeFormat("ru-RU", {
                    day: "2-digit",
                    month: "short",
                    timeZone: "Asia/Qyzylorda",
                  }).format(new Date(appeal.createdAt))}
                </span>
              </div>
              <h2>{criterion?.title || "Критерий предыдущей оценки"}</h2>
              <button
                className="appeal-conversation"
                onClick={() => onOpen(appeal.conversationId)}
              >
                <MessageSquare size={15} />
                {conversation?.title || appeal.conversationId}
                <ArrowUpRight size={14} />
              </button>
              <blockquote>{appeal.comment}</blockquote>
              <div className="appeal-author">
                <Avatar name={conversation?.initials || "ОП"} small />
                <div>
                  <strong>{conversation?.operator || "Оператор"}</strong>
                  <span>Автор обращения</span>
                </div>
              </div>
              {appeal.decision && (
                <div className="decision-note">
                  <Check size={16} />
                  <div>
                    <strong>Решение проверяющего</strong>
                    <p>{appeal.decision}</p>
                  </div>
                </div>
              )}
              <div className="appeal-card-bottom">
                <span>
                  Исходное значение <b>{appeal.originalValue ?? "N/A"}</b>
                  {appeal.status === "accepted" && (
                    <>
                      {" "}
                      → <b>{appeal.newValue ?? "N/A"}</b>
                    </>
                  )}
                </span>
                {appeal.status === "pending" ? (
                  <Button onClick={() => setSelected(appeal)}>
                    Рассмотреть
                    <ArrowUpRight size={15} />
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    onClick={() => onOpen(appeal.conversationId)}
                  >
                    К результату
                    <ArrowUpRight size={15} />
                  </Button>
                )}
              </div>
            </article>
          );
        })}
      </div>
      {!appeals.length && (
        <div className="panel">
          <EmptyState
            title={
              filter === "pending"
                ? "Все обращения рассмотрены"
                : "Здесь пока нет обращений"
            }
            description="Новая апелляция появится здесь после отправки из карточки диалога."
          />
        </div>
      )}
      {selected && (
        <ReviewDialog
          appeal={selected}
          onClose={() => setSelected(null)}
          onOpen={onOpen}
        />
      )}
    </>
  );
}
function ReviewDialog({
  appeal,
  onClose,
  onOpen,
}: {
  appeal: Appeal;
  onClose: () => void;
  onOpen: (id: string) => void;
}) {
  const { data, decideAppeal } = useStore();
  const [decision, setDecision] = useState("");
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [outcome, setOutcome] = useState<"accepted" | "rejected">("accepted");
  const [value, setValue] = useState(
    appeal.originalValue === null ? "na" : String(appeal.originalValue),
  );
  const conversation = data.conversations.find(
    (c) => c.id === appeal.conversationId,
  );
  const evaluation = conversation?.evaluation;
  const criterion = evaluation?.checklist.criteria.find(
    (c) => c.id === appeal.criterionId,
  );
  const result = evaluation?.results.find(
    (r) => r.criterionId === appeal.criterionId,
  );
  if (
    !conversation ||
    !evaluation ||
    !criterion ||
    evaluation.id !== appeal.evaluationId
  )
    return (
      <Modal title="Оценка была обновлена" onClose={onClose}>
        <Notice>
          Для этого обращения нужна сохранённая версия оценки. Вернитесь к
          карточке диалога.
        </Notice>
        <div className="modal-actions">
          <Button onClick={onClose}>Закрыть</Button>
        </div>
      </Modal>
    );
  const newValue = value === "na" ? null : Number(value);
  const valueValid =
    value === "na"
      ? criterion.allowNA && criterion.type !== "flag"
      : value.trim() !== "" &&
        newValue !== null &&
        isDecimalText(value) &&
        newValue! >= 0 &&
        newValue! <= criterion.max &&
        ((criterion.type !== "binary" && criterion.type !== "flag") ||
          newValue === 0 ||
          newValue === 1) &&
        (criterion.allowedValues === undefined ||
          criterion.allowedValues.includes(newValue!));
  const valid =
    decision.trim().length >= 10 && (outcome === "rejected" || valueValid);
  async function save() {
    if (!valid || busy) return;
    setBusy(true);
    setSaveError("");
    try {
      await decideAppeal(
        appeal.id,
        outcome,
        decision.trim(),
        outcome === "rejected" ? appeal.originalValue : newValue,
      );
      onClose();
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : "Не удалось сохранить решение.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Рассмотрение апелляции"
      onClose={() => {
        if (!busy) onClose();
      }}
      wide
    >
      <fieldset
        disabled={busy}
        style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
        aria-busy={busy}
      >
        <div className="review-grid">
          <div>
            <div className="appeal-criterion">
              <Flag size={19} />
              <div>
                <strong>{criterion.title}</strong>
                <span>
                  Исходное значение: {appeal.originalValue ?? "N/A"} · вес{" "}
                  {criterion.weight}
                </span>
              </div>
            </div>
            <h3 className="field-label">Комментарий оператора</h3>
            <blockquote className="review-quote">{appeal.comment}</blockquote>
            <h3 className="field-label">Фрагмент диалога</h3>
            {conversation.transcript
              .filter((m) => result?.evidenceIds.includes(m.id))
              .map((m) => (
                <blockquote key={m.id} className="review-evidence">
                  <small>
                    {m.speaker === "operator"
                      ? conversation.operator
                      : m.speaker === "client"
                        ? "Клиент"
                        : m.speaker === "bot"
                          ? "Бот"
                          : "Система"} ·{" "}
                    {m.timestamp}
                  </small>
                  {m.text}
                </blockquote>
              ))}
            <Button
              variant="ghost"
              onClick={() => {
                onClose();
                onOpen(conversation.id);
              }}
            >
              Открыть полный диалог
              <ArrowUpRight size={15} />
            </Button>
          </div>
          <div>
            <label className="field-label" htmlFor="review-outcome">
              Решение
            </label>
            <select
              id="review-outcome"
              value={outcome}
              onChange={(e) =>
                setOutcome(e.target.value as "accepted" | "rejected")
              }
            >
              <option value="accepted">Изменить оценку</option>
              <option value="rejected">Подтвердить исходную оценку</option>
            </select>
            {outcome === "accepted" && (
              <>
                <label className="field-label" htmlFor="review-value">
                  Новое значение
                </label>
                {criterion.type === "binary" ||
                criterion.type === "flag" ||
                criterion.allowedValues !== undefined ? (
                  <select
                    id="review-value"
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                  >
                    {(criterion.allowedValues ?? [0, 1]).map((option) => (
                      <option key={option} value={option}>
                        {criterion.type === "flag"
                          ? option === 1
                            ? "Да, есть нарушение · 1"
                            : "Нет нарушения · 0"
                          : criterion.type === "binary"
                            ? option === 1
                              ? "Да · 1"
                              : "Нет · 0"
                            : `${option} баллов`}
                      </option>
                    ))}
                    {criterion.allowNA && criterion.type !== "flag" && (
                      <option value="na">Не применимо · N/A</option>
                    )}
                  </select>
                ) : (
                  <>
                    <input
                      id="review-value"
                      type="number"
                      min="0"
                      max={criterion.max}
                      step="any"
                      disabled={value === "na"}
                      value={value === "na" ? "" : value}
                      onChange={(e) => setValue(e.target.value)}
                    />
                    <span className="field-hint">От 0 до {criterion.max}</span>
                    {criterion.allowNA && (
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={value === "na"}
                          onChange={(e) =>
                            setValue(e.target.checked ? "na" : "0")
                          }
                        />
                        Не применимо (N/A)
                      </label>
                    )}
                  </>
                )}
                {criterion.type === "flag" && (
                  <Notice kind="warning">
                    Флаг грубой ошибки отображается отдельно и не меняет
                    числовой итог.
                  </Notice>
                )}
                {criterion.allowNA && criterion.naCondition && (
                  <span className="field-hint">
                    «Не применимо»: {criterion.naCondition}
                  </span>
                )}
                {!valueValid && (
                  <Notice kind="error">
                    Выберите допустимое значение по сохранённой версии
                    чек-листа.
                  </Notice>
                )}
              </>
            )}
            <label className="field-label" htmlFor="review-reason">
              Обоснование решения
            </label>
            <textarea
              id="review-reason"
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
              rows={4}
              maxLength={2000}
              placeholder="Объясните, почему оценка изменена или подтверждена…"
            />
            <span className="field-hint">Минимум 10 символов</span>
            <DecisionPreview
              appealId={appeal.id}
              valid={valid}
              body={{
                status: outcome,
                decision: decision.trim(),
                newValue:
                  outcome === "rejected" ? appeal.originalValue : newValue,
              }}
            />
          </div>
        </div>
        <div className="modal-actions">
          <Button onClick={onClose}>Отмена</Button>
          <Button
            variant="primary"
            disabled={!valid || busy}
            onClick={() => void save()}
          >
            {busy ? "Сохраняем…" : "Сохранить решение"}
            <Check size={16} />
          </Button>
        </div>
      </fieldset>
      {saveError && <Notice kind="error">{saveError}</Notice>}
    </Modal>
  );
}

function DecisionPreview({
  appealId,
  valid,
  body,
}: {
  appealId: string;
  valid: boolean;
  body: AppealDecision;
}) {
  const requestKey = JSON.stringify(body);
  const [result, setResult] = useState<{
    key: string;
    score?: EvaluationScore;
    error?: string;
  } | null>(null);
  useEffect(() => {
    if (!valid) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void previewAppeal(
        appealId,
        JSON.parse(requestKey) as AppealDecision,
        controller.signal,
      )
        .then((score) => {
          if (!controller.signal.aborted) setResult({ key: requestKey, score });
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setResult({
              key: requestKey,
              error:
                error instanceof Error
                  ? error.message
                  : "Не удалось рассчитать предварительный итог.",
            });
        });
    }, 350);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [appealId, requestKey, valid]);
  if (!valid)
    return (
      <p className="field-hint">
        Заполните решение, чтобы получить предварительный итог с сервера.
      </p>
    );
  if (result?.key !== requestKey)
    return (
      <p className="field-hint" role="status">
        Сервер рассчитывает итог…
      </p>
    );
  if (result.error)
    return (
      <Notice kind="warning">
        {result.error} Итог будет рассчитан при сохранении.
      </Notice>
    );
  return (
    <>
      <div className="review-total">
        <span>Итог после решения</span>
        <Score value={result.score?.total ?? null} />
      </div>
      {result.score?.critical && <Badge tone="red">Есть грубая ошибка</Badge>}
    </>
  );
}
