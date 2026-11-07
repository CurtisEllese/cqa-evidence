import { useEffect, useState } from "react";
import {
  Check,
  ChevronRight,
  ClipboardCheck,
  Plus,
  Save,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { Badge, Button, Modal, Notice, PageHeading } from "../components/ui";
import type { Checklist, Criterion } from "../domain/types";
import { useStore } from "../store";
import "./checklist.css";

function criterionErrors(item: Criterion) {
  return {
    title: !item.title.trim() ? "Введите название критерия." : "",
    description: !item.description.trim()
      ? "Опишите, что нужно проверить в диалоге."
      : "",
    weight:
      item.type === "flag" && item.weight !== 0
        ? "Критический флаг не участвует в баллах: вес должен быть 0."
        : !Number.isFinite(item.weight) || item.weight < 0
          ? "Вес должен быть числом не меньше нуля."
          : "",
    max:
      item.type === "binary" || item.type === "flag"
        ? item.max !== 1
          ? "Для ответа да / нет максимум равен 1."
          : ""
        : !Number.isInteger(item.max) || item.max < 1 || item.max > 100
          ? "Введите целое число от 1 до 100."
          : "",
    allowedValues:
      !item.allowedValues?.length ||
      item.allowedValues.some(
        (value) => !Number.isInteger(value) || value < 0 || value > item.max,
      ) ||
      new Set(item.allowedValues).size !== item.allowedValues.length ||
      !item.allowedValues.includes(0) ||
      !item.allowedValues.includes(item.max) ||
      ((item.type === "binary" || item.type === "flag") &&
        (item.allowedValues.length !== 2 || !item.allowedValues.includes(1)))
        ? "Укажите уникальные целые значения от 0 до максимума, включая 0 и максимум."
        : "",
    naCondition:
      item.type === "flag" && item.allowNA
        ? "Для критического флага значение «Не применимо» недоступно."
        : item.allowNA && !item.naCondition?.trim()
          ? "Опишите, когда критерий не применяется."
          : "",
  };
}

export function ChecklistEditor() {
  const { data, saveChecklist, setChecklistDirty } = useStore();
  const [draft, setDraft] = useState<Checklist>(() =>
    structuredClone(data.checklist),
  );
  const [baseline, setBaseline] = useState(() =>
    JSON.stringify(data.checklist),
  );
  const [selectedId, setSelectedId] = useState(
    data.checklist.criteria[0]?.id ?? "",
  );
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [allowedValueInputs, setAllowedValueInputs] = useState<
    Record<string, string>
  >({});
  const dirty = JSON.stringify(draft) !== baseline;
  useEffect(() => {
    setChecklistDirty(dirty);
    return () => setChecklistDirty(false);
  }, [dirty, setChecklistDirty]);
  const selected = draft.criteria.find((item) => item.id === selectedId);
  const errors = selected ? criterionErrors(selected) : null;
  const totalWeight = draft.criteria.reduce(
    (sum, item) => sum + (Number.isFinite(item.weight) ? item.weight : 0),
    0,
  );
  const invalidCriteria = draft.criteria.filter((item) =>
    Object.values(criterionErrors(item)).some(Boolean),
  );
  const uniqueIds =
    new Set(draft.criteria.map((item) => item.id)).size ===
      draft.criteria.length && draft.criteria.every((item) => item.id.trim());
  const valid =
    Boolean(draft.name.trim()) &&
    uniqueIds &&
    invalidCriteria.length === 0 &&
    Number.isFinite(totalWeight) &&
    totalWeight > 0;

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function updateCriterion(patch: Partial<Criterion>) {
    setDraft((current) => ({
      ...current,
      criteria: current.criteria.map((item) =>
        item.id === selectedId ? { ...item, ...patch } : item,
      ),
    }));
  }
  function addCriterion() {
    const id = `criterion-${crypto.randomUUID()}`;
    setDraft((current) => ({
      ...current,
      criteria: [
        ...current.criteria,
        {
          id,
          title: "",
          description: "",
          section: "Общие критерии",
          weight: 1,
          type: "binary",
          max: 1,
          allowedValues: [0, 1],
          allowNA: false,
        },
      ],
    }));
    setSelectedId(id);
  }
  function save() {
    if (!valid || !dirty) return;
    const next = {
      ...draft,
      name: draft.name.trim(),
      version: data.checklist.version + 1,
      criteria: draft.criteria.map((item) => ({
        ...item,
        title: item.title.trim(),
        description: item.description.trim(),
        section: item.section.trim(),
        allowedValues: item.allowedValues
          ? [...item.allowedValues].sort((a, b) => a - b)
          : undefined,
        naCondition: item.allowNA ? item.naCondition?.trim() : undefined,
      })),
    };
    saveChecklist(next);
    setDraft(next);
    setBaseline(JSON.stringify(next));
    setAllowedValueInputs({});
  }
  function removeCriterion() {
    const remaining = draft.criteria.filter((item) => item.id !== deleteId);
    setDraft((current) => ({ ...current, criteria: remaining }));
    if (selectedId === deleteId) setSelectedId(remaining[0]?.id ?? "");
    setDeleteId(null);
  }
  function resetDraft() {
    const saved = JSON.parse(baseline) as Checklist;
    setDraft(saved);
    setSelectedId(saved.criteria[0]?.id ?? "");
    setAllowedValueInputs({});
    setConfirmReset(false);
  }

  return (
    <div className="checklist-page">
      <PageHeading
        eyebrow="СТАНДАРТЫ КАЧЕСТВА"
        title="Чек-лист оценки"
        description="Настройте критерии, по которым оценивается каждый диалог."
        actions={
          <>
            {dirty && (
              <Button variant="ghost" onClick={() => setConfirmReset(true)}>
                Отменить изменения
              </Button>
            )}
            <Button
              variant="primary"
              onClick={save}
              disabled={!dirty || !valid}
            >
              <Save size={17} />
              Сохранить версию
            </Button>
          </>
        }
      />
      <Notice>
        Исходная версия составлена по официальному чек-листу и уточнениям
        организаторов. Допустимые баллы и условия «Не применимо» задаются для
        каждого критерия. Грубые ошибки отмечаются отдельным флагом и не
        обнуляют числовой итог.
      </Notice>

      <section
        className="panel checklist-overview"
        aria-label="Настройки чек-листа"
      >
        <div className="checklist-name-field">
          <label className="field-label" htmlFor="checklist-name">
            Название чек-листа
          </label>
          <input
            id="checklist-name"
            className="checklist-name-input"
            value={draft.name}
            onChange={(event) =>
              setDraft((current) => ({ ...current, name: event.target.value }))
            }
            aria-invalid={!draft.name.trim()}
            aria-describedby={
              !draft.name.trim() ? "checklist-name-error" : undefined
            }
          />
          {!draft.name.trim() && (
            <span id="checklist-name-error" className="checklist-error">
              Введите название чек-листа.
            </span>
          )}
          <div className="checklist-version-row">
            <Badge>Версия {draft.version}</Badge>
            <span
              className={dirty ? "checklist-dirty" : "checklist-saved"}
              role="status"
            >
              {dirty ? (
                <>
                  <span className="checklist-status-dot" />
                  Есть несохранённые изменения
                </>
              ) : (
                <>
                  <Check size={14} />
                  Все изменения сохранены
                </>
              )}
            </span>
          </div>
        </div>
        <div className="checklist-stat">
          <span>Критерии</span>
          <strong>{draft.criteria.length}</strong>
        </div>
        <div className="checklist-stat">
          <span>Суммарный вес</span>
          <strong>
            {new Intl.NumberFormat("ru-RU", {
              maximumFractionDigits: 2,
            }).format(totalWeight)}
            <small>баллов</small>
          </strong>
        </div>
      </section>

      {!uniqueIds && (
        <Notice kind="error">
          Идентификаторы критериев должны быть уникальными и непустыми. Удалите
          повторяющийся критерий и добавьте его заново.
        </Notice>
      )}
      {totalWeight <= 0 && (
        <Notice kind="warning">
          Хотя бы один критерий должен иметь вес больше нуля.
        </Notice>
      )}
      {!Number.isFinite(totalWeight) && (
        <Notice kind="warning">
          Суммарный вес слишком велик. Уменьшите веса критериев.
        </Notice>
      )}

      <div className="checklist-workspace">
        <section
          className="panel checklist-criteria-panel"
          aria-labelledby="checklist-criteria-title"
        >
          <div className="checklist-panel-heading">
            <h2 id="checklist-criteria-title">
              <ClipboardCheck size={18} />
              Критерии
            </h2>
            <span>{draft.criteria.length}</span>
          </div>
          <div className="checklist-criteria-list">
            {draft.criteria.map((item, index) => {
              const hasError = Object.values(criterionErrors(item)).some(
                Boolean,
              );
              return (
                <button
                  type="button"
                  key={`${item.id}-${index}`}
                  className={`checklist-criterion ${selectedId === item.id ? "is-selected" : ""}`}
                  onClick={() => setSelectedId(item.id)}
                  aria-pressed={selectedId === item.id}
                >
                  <span className="checklist-criterion-number">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="checklist-criterion-copy">
                    <strong>{item.title || "Новый критерий"}</strong>
                    <span>
                      {item.section || "Без раздела"}
                      <span className="checklist-list-separator">·</span>
                      {hasError ? (
                        <span className="checklist-invalid-label">
                          Заполните поля
                        </span>
                      ) : item.type === "flag" ? (
                        "Критический флаг"
                      ) : (
                        `Вес ${item.weight}`
                      )}
                    </span>
                  </span>
                  <ChevronRight size={15} aria-hidden="true" />
                </button>
              );
            })}
          </div>
          <div className="checklist-add">
            <Button onClick={addCriterion}>
              <Plus size={17} />
              Добавить критерий
            </Button>
          </div>
        </section>

        {selected && errors ? (
          <section
            className="panel criterion-editor"
            aria-labelledby="criterion-editor-title"
          >
            <div className="criterion-editor-heading">
              <div>
                <div className="criterion-editor-kicker">
                  НАСТРОЙКА КРИТЕРИЯ
                </div>
                <h2 id="criterion-editor-title">
                  {selected.title || "Новый критерий"}
                </h2>
              </div>
              <Button
                variant="ghost"
                className="criterion-editor-delete"
                aria-label={`Удалить критерий ${selected.title || "без названия"}`}
                onClick={() => setDeleteId(selected.id)}
              >
                <Trash2 size={17} />
              </Button>
            </div>
            <div className="criterion-editor-fields">
              <label className="criterion-editor-field">
                <span className="field-label">
                  Название <span aria-hidden="true">*</span>
                </span>
                <input
                  value={selected.title}
                  onChange={(event) =>
                    updateCriterion({ title: event.target.value })
                  }
                  placeholder="Например, выявление потребности"
                  aria-invalid={Boolean(errors.title)}
                  aria-describedby={
                    errors.title ? "criterion-title-error" : undefined
                  }
                />
                {errors.title && (
                  <span id="criterion-title-error" className="checklist-error">
                    {errors.title}
                  </span>
                )}
              </label>
              <label className="criterion-editor-field">
                <span className="field-label">
                  Описание критерия <span aria-hidden="true">*</span>
                </span>
                <textarea
                  rows={4}
                  value={selected.description}
                  onChange={(event) =>
                    updateCriterion({ description: event.target.value })
                  }
                  placeholder="Что должен сделать оператор? На какие признаки опирается оценка?"
                  aria-invalid={Boolean(errors.description)}
                  aria-describedby={
                    errors.description
                      ? "criterion-description-error"
                      : undefined
                  }
                />
                {errors.description && (
                  <span
                    id="criterion-description-error"
                    className="checklist-error"
                  >
                    {errors.description}
                  </span>
                )}
              </label>
              <label className="criterion-editor-field">
                <span className="field-label">Раздел</span>
                <input
                  list="checklist-sections"
                  value={selected.section}
                  onChange={(event) =>
                    updateCriterion({ section: event.target.value })
                  }
                  placeholder="Например, работа с клиентом"
                />
                <datalist id="checklist-sections">
                  {[
                    ...new Set(
                      draft.criteria
                        .map((item) => item.section)
                        .filter(Boolean),
                    ),
                  ].map((section) => (
                    <option key={section} value={section} />
                  ))}
                </datalist>
              </label>

              <div className="criterion-editor-divider" />
              <h3 className="criterion-editor-subheading">
                <SlidersHorizontal size={16} />
                Параметры оценки
              </h3>
              <div className="criterion-editor-row">
                <label className="criterion-editor-field">
                  <span className="field-label">Тип оценки</span>
                  <select
                    aria-label="Тип оценки"
                    value={selected.type}
                    onChange={(event) => {
                      const type = event.target.value as Criterion["type"];
                      setAllowedValueInputs((current) => {
                        const next = { ...current };
                        delete next[selected.id];
                        return next;
                      });
                      updateCriterion(
                        type === "flag"
                          ? {
                              type,
                              max: 1,
                              weight: 0,
                              allowNA: false,
                              naCondition: undefined,
                              allowedValues: [0, 1],
                            }
                          : {
                              type,
                              max: type === "binary" ? 1 : 5,
                              allowedValues:
                                type === "binary" ? [0, 1] : [0, 1, 2, 3, 4, 5],
                              weight:
                                selected.type === "flag" ? 1 : selected.weight,
                            },
                      );
                    }}
                  >
                    <option value="binary">Да / нет</option>
                    <option value="scale">Числовая шкала</option>
                    <option value="flag">Критический флаг</option>
                  </select>
                </label>
                <label className="criterion-editor-field">
                  <span className="field-label">Максимальный балл</span>
                  <input
                    type="number"
                    min="1"
                    max="100"
                    step="1"
                    disabled={selected.type !== "scale"}
                    value={Number.isFinite(selected.max) ? selected.max : ""}
                    onChange={(event) =>
                      updateCriterion({
                        max:
                          event.target.value === ""
                            ? NaN
                            : Number(event.target.value),
                      })
                    }
                    aria-invalid={Boolean(errors.max)}
                    aria-describedby={
                      errors.max ? "criterion-max-error" : undefined
                    }
                  />
                  {errors.max && (
                    <span id="criterion-max-error" className="checklist-error">
                      {errors.max}
                    </span>
                  )}
                </label>
              </div>
              {selected.type === "scale" && (
                <label className="criterion-editor-field">
                  <span className="field-label">
                    Допустимые баллы <span aria-hidden="true">*</span>
                  </span>
                  <input
                    value={
                      allowedValueInputs[selected.id] ??
                      selected.allowedValues?.join(", ") ??
                      ""
                    }
                    onChange={(event) => {
                      const text = event.target.value;
                      setAllowedValueInputs((current) => ({
                        ...current,
                        [selected.id]: text,
                      }));
                      updateCriterion({
                        allowedValues: text.trim()
                          ? text
                              .trim()
                              .split(/[,;\s]+/)
                              .filter(Boolean)
                              .map(Number)
                          : [],
                      });
                    }}
                    placeholder="Например: 0, 5, 10, 15, 20"
                    aria-invalid={Boolean(errors.allowedValues)}
                    aria-describedby="criterion-values-hint criterion-values-error"
                  />
                  <span
                    id="criterion-values-hint"
                    className="criterion-editor-field-hint"
                  >
                    Только эти значения будут доступны при оценке и рассмотрении
                    апелляций. Разделяйте баллы запятой.
                  </span>
                  <span id="criterion-values-error" className="checklist-error">
                    {errors.allowedValues}
                  </span>
                </label>
              )}
              {selected.type === "flag" && (
                <div className="criterion-editor-flag-note">
                  <strong>Да = нарушение, нет = нарушения нет</strong>
                  <span>
                    Флаг отображается отдельно. Его вес всегда 0; значение «Не
                    применимо» недоступно. Числовой итог не обнуляется.
                  </span>
                </div>
              )}
              <div className="criterion-editor-weight-row">
                <label className="criterion-editor-field">
                  <span className="field-label">Вес критерия</span>
                  <input
                    type="number"
                    min="0"
                    step="any"
                    disabled={selected.type === "flag"}
                    value={
                      Number.isFinite(selected.weight) ? selected.weight : ""
                    }
                    onChange={(event) =>
                      updateCriterion({
                        weight:
                          event.target.value === ""
                            ? NaN
                            : Number(event.target.value),
                      })
                    }
                    aria-invalid={Boolean(errors.weight)}
                    aria-describedby={
                      errors.weight
                        ? "criterion-weight-error"
                        : "criterion-weight-hint"
                    }
                  />
                  {errors.weight && (
                    <span
                      id="criterion-weight-error"
                      className="checklist-error"
                    >
                      {errors.weight}
                    </span>
                  )}
                </label>
                <div
                  id="criterion-weight-hint"
                  className="criterion-editor-weight-hint"
                >
                  <strong>
                    {totalWeight > 0 &&
                    Number.isFinite(selected.weight) &&
                    selected.weight >= 0
                      ? `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format((selected.weight / totalWeight) * 100)}%`
                      : "—"}
                  </strong>
                  <span>от общего веса чек-листа</span>
                </div>
              </div>
              <label className="criterion-editor-checkbox">
                <input
                  type="checkbox"
                  disabled={selected.type === "flag"}
                  checked={selected.allowNA}
                  onChange={(event) =>
                    updateCriterion({ allowNA: event.target.checked })
                  }
                />
                <span>
                  <strong>Разрешить «Не применимо»</strong>
                  <span>
                    Критерий можно исключить из итоговой оценки, если он не
                    относится к диалогу.
                  </span>
                </span>
              </label>
              {selected.allowNA && (
                <label className="criterion-editor-field">
                  <span className="field-label">
                    Когда «Не применимо» допустимо{" "}
                    <span aria-hidden="true">*</span>
                  </span>
                  <textarea
                    rows={2}
                    value={selected.naCondition ?? ""}
                    onChange={(event) =>
                      updateCriterion({ naCondition: event.target.value })
                    }
                    placeholder="Укажите условие из чек-листа или уточнений организаторов"
                    aria-invalid={Boolean(errors.naCondition)}
                    aria-describedby={
                      errors.naCondition ? "criterion-na-error" : undefined
                    }
                  />
                  {errors.naCondition && (
                    <span id="criterion-na-error" className="checklist-error">
                      {errors.naCondition}
                    </span>
                  )}
                </label>
              )}
            </div>
            <div className="criterion-editor-footer">
              <span className="criterion-editor-footer-icon">
                <Check size={15} />
              </span>
              Новая версия применяется к следующим оценкам. Сохранённые оценки
              сохраняют свой чек-лист.
            </div>
          </section>
        ) : (
          <section className="panel checklist-empty">
            <ClipboardCheck size={36} />
            <h2>Добавьте первый критерий</h2>
            <p>
              Опишите стандарт обслуживания и задайте его вес в итоговой оценке.
            </p>
            <Button variant="primary" onClick={addCriterion}>
              <Plus size={17} />
              Добавить критерий
            </Button>
          </section>
        )}
      </div>
      <div className="checklist-bottom-note" role="status">
        {dirty
          ? valid
            ? "Изменения готовы к сохранению. Сохраните версию перед переходом в другой раздел."
            : "Заполните обязательные поля и исправьте параметры, чтобы сохранить версию."
          : "Изменения сохраняются локально в этом браузере."}
      </div>

      {deleteId && (
        <Modal title="Удалить критерий?" onClose={() => setDeleteId(null)}>
          <p>
            «
            {draft.criteria.find((item) => item.id === deleteId)?.title ||
              "Новый критерий"}
            » будет удалён из черновика. Сохранённые оценки не изменятся.
          </p>
          <div className="checklist-dialog-actions">
            <Button onClick={() => setDeleteId(null)}>Оставить</Button>
            <Button variant="danger" onClick={removeCriterion}>
              <Trash2 size={16} />
              Удалить критерий
            </Button>
          </div>
        </Modal>
      )}
      {confirmReset && (
        <Modal
          title="Отменить изменения?"
          onClose={() => setConfirmReset(false)}
        >
          <p>Редактор вернётся к последней сохранённой версии чек-листа.</p>
          <div className="checklist-dialog-actions">
            <Button onClick={() => setConfirmReset(false)}>
              Продолжить редактирование
            </Button>
            <Button variant="danger" onClick={resetDraft}>
              <X size={16} />
              Отменить изменения
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
