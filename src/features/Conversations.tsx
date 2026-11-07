import { useMemo, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  Phone,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import type { Conversation } from "../domain/types";
import { downloadExport, downloadSubmission } from "../services/api";
import { evaluationLabel, evaluationTone } from "./evaluationStatus";
import { originalCriticalStatus } from "./criticalStatus";
import { useStore } from "../store";
import {
  Avatar,
  Badge,
  Button,
  EmptyState,
  Notice,
  Score,
} from "../components/ui";

export function ConversationTable({
  onOpen,
  compact = false,
  query = "",
  onSearchChange,
}: {
  onOpen: (id: string) => void;
  compact?: boolean;
  query?: string;
  onSearchChange?: (query: string) => void;
}) {
  const { data, datasets, selectedGroup, filters, setFilters } = useStore();
  const [exporting, setExporting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [exportError, setExportError] = useState("");
  async function exportWorkspace() {
    if (exporting) return;
    setExporting(true);
    setExportError("");
    try {
      await downloadExport();
    } catch (error) {
      setExportError(
        error instanceof Error ? error.message : "Не удалось выгрузить данные.",
      );
    } finally {
      setExporting(false);
    }
  }
  async function exportSubmission() {
    if (!group || submitting) return;
    setSubmitting(true);
    setExportError("");
    try {
      await downloadSubmission(group.group);
    } catch (error) {
      setExportError(
        error instanceof Error
          ? error.message
          : "Не удалось скачать файл для сдачи.",
      );
    } finally {
      setSubmitting(false);
    }
  }
  const [localSearch, setLocalSearch] = useState("");
  const [compactAscending, setCompactAscending] = useState(false);
  const search = onSearchChange ? query : localSearch;
  const setSearch = onSearchChange ?? setLocalSearch;
  const { language, channel, status, critical, page, filtersOpen, ascending } =
    compact
      ? {
          language: "all",
          channel: "all",
          status: "all",
          critical: "all",
          page: 0,
          filtersOpen: false,
          ascending: compactAscending,
        }
      : filters;
  const setLanguage = (language: string) => setFilters({ language });
  const setChannel = (channel: string) => setFilters({ channel });
  const setStatus = (status: string) => setFilters({ status });
  const setCritical = (critical: string) => setFilters({ critical });
  const setPage = (page: number) => setFilters({ page });
  const setFiltersOpen = (filtersOpen: boolean) => setFilters({ filtersOpen });
  const setAscending = (ascending: boolean) => {
    if (compact) setCompactAscending(ascending);
    else setFilters({ ascending });
  };
  const group = datasets.find(
    (item) => `group:${item.group}` === selectedGroup,
  );
  const filtered = useMemo(
    () =>
      data.conversations
        .filter((item) => {
          const matches = `${item.id} ${item.title} ${item.operator}`
            .toLowerCase()
            .includes(search.toLowerCase());
          const hasAppeal = data.appeals.some(
            (a) =>
              a.conversationId === item.id &&
              a.status === "pending" &&
              a.evaluationId === item.evaluation?.id,
          );
          return (
            matches &&
            (compact || !group || group.conversationIds.includes(item.id)) &&
            (language === "all" || item.language === language) &&
            (channel === "all" || item.channel === channel) &&
            (critical === "all" || originalCriticalStatus(item) === critical) &&
            (status === "all" ||
              (status === "done" && item.evaluation?.status === "completed") ||
              (status === "pending" && !item.evaluation) ||
              (status === "active" &&
                ["queued", "running"].includes(
                  item.evaluation?.status ?? "",
                )) ||
              (status === "review" &&
                item.evaluation?.status === "needs_review") ||
              (status === "failed" &&
                ["failed", "failed_validation", "retryable"].includes(
                  item.evaluation?.status ?? "",
                )) ||
              (status === "appeal" && hasAppeal))
          );
        })
        .sort((a, b) => {
          if (!a.date) return b.date ? 1 : 0;
          if (!b.date) return -1;
          return ascending
            ? Date.parse(a.date) - Date.parse(b.date)
            : Date.parse(b.date) - Date.parse(a.date);
        }),
    [
      data,
      search,
      query,
      language,
      channel,
      status,
      critical,
      ascending,
      group,
      compact,
    ],
  );
  const pageSize = compact ? 5 : 8;
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(filtered.length / pageSize) - 1),
  );
  const rows = filtered.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize,
  );
  function resetFilters() {
    setLanguage("all");
    setChannel("all");
    setStatus("all");
    setCritical("all");
    setSearch("");
    setPage(0);
  }
  function rowStatus(item: Conversation) {
    if (
      data.appeals.some(
        (a) =>
          a.conversationId === item.id &&
          a.status === "pending" &&
          a.evaluationId === item.evaluation?.id,
      )
    )
      return (
        <Badge tone="amber">
          <span className="status-dot" />
          Апелляция
        </Badge>
      );
    return (
      <Badge tone={evaluationTone(item.evaluation)}>
        <span className="status-dot" />
        {evaluationLabel(item.evaluation)}
      </Badge>
    );
  }
  return (
    <section className="panel conversation-panel">
      <div className="panel-heading">
        <div>
          <h2>
            {compact ? "Последние диалоги" : "Все диалоги"}{" "}
            <span className="count">{filtered.length}</span>
          </h2>
          {compact && <p>От деталей разговора — к качеству сервиса</p>}
        </div>
        {compact ? (
          <span className="subtle-label">Рабочий набор</span>
        ) : (
          <div className="table-export-actions">
            <Button
              disabled={submitting || !group}
              onClick={() => void exportSubmission()}
            >
              <ArrowDownToLine size={16} />
              {submitting ? "Готовим файл…" : "Файл для сдачи"}
            </Button>
            <Button
              disabled={exporting}
              title="Все диалоги, оценки и апелляции рабочего пространства"
              onClick={() => void exportWorkspace()}
            >
              <ArrowDownToLine size={16} />
              {exporting ? "Выгружаем…" : "Резервная копия"}
            </Button>
          </div>
        )}
      </div>
      {exportError && <Notice kind="error">{exportError}</Notice>}
      {!compact && (
        <p className="submission-note">
          Файл для сдачи:{" "}
          {group
            ? `набор ${group.group}`
            : "сначала выберите загруженный набор"}
          . Используются исходные оценки ИИ по 8 критериям; апелляции их не
          меняют. Неоценённые, неподтверждённые результаты и изменённая
          конфигурация чек-листа выгружаются со статусом error. Резервная копия
          содержит всё рабочее пространство.
        </p>
      )}
      {!compact && (
        <>
          <div className="table-toolbar">
            <label className="search-field">
              <Search size={18} />
              <input
                aria-label="Поиск диалогов"
                placeholder="Поиск по теме, оператору или ID"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(0);
                }}
              />
              {search && (
                <button
                  onClick={() => setSearch("")}
                  aria-label="Очистить поиск"
                >
                  <X size={15} />
                </button>
              )}
            </label>
            <select
              aria-label="Язык диалога"
              value={language}
              onChange={(e) => {
                setLanguage(e.target.value);
                setPage(0);
              }}
            >
              <option value="all">Все языки</option>
              <option>RU</option>
              <option>KZ</option>
              <option>EN</option>
            </select>
            <select
              aria-label="Грубые ошибки"
              value={critical}
              onChange={(event) => {
                setCritical(event.target.value);
                setPage(0);
              }}
            >
              <option value="all">Грубые ошибки: все</option>
              <option value="present">Есть грубые ошибки (ИИ)</option>
              <option value="absent">Не выявлены (ИИ)</option>
              <option value="unknown">Нет подтверждённой оценки</option>
            </select>
            <Button
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen(!filtersOpen)}
            >
              <SlidersHorizontal size={16} />
              Фильтры
              {(channel !== "all" || status !== "all") && (
                <span className="filter-dot" />
              )}
            </Button>
          </div>
          {filtersOpen && (
            <div className="filter-panel">
              <label>
                Канал
                <select
                  aria-label="Канал"
                  value={channel}
                  onChange={(e) => {
                    setChannel(e.target.value);
                    setPage(0);
                  }}
                >
                  <option value="all">Все каналы</option>
                  <option value="agent chat">Чаты</option>
                  <option value="voice call">Звонки</option>
                </select>
              </label>
              <label>
                Статус
                <select
                  aria-label="Статус"
                  value={status}
                  onChange={(e) => {
                    setStatus(e.target.value);
                    setPage(0);
                  }}
                >
                  <option value="all">Все статусы</option>
                  <option value="done">Оценён</option>
                  <option value="pending">Не оценён</option>
                  <option value="active">В очереди / оценивается</option>
                  <option value="review">Требует проверки</option>
                  <option value="failed">Ошибка оценки</option>
                  <option value="appeal">Апелляция</option>
                </select>
              </label>
              <Button variant="ghost" onClick={resetFilters}>
                Сбросить
              </Button>
            </div>
          )}
        </>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Диалог</th>
              <th>Оператор</th>
              <th>Канал / язык</th>
              <th>
                <button
                  className="sort-button"
                  onClick={() => setAscending(!ascending)}
                >
                  Дата {ascending ? "↑" : "↓"}
                </button>
              </th>
              <th>Оценка</th>
              <th>Статус</th>
              <th>
                <span className="sr-only">Открыть</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.id}>
                <td>
                  <button
                    className="dialog-link"
                    onClick={() => onOpen(item.id)}
                  >
                    {item.title}
                  </button>
                  <span className="table-caption">{item.id}</span>
                  {originalCriticalStatus(item) === "present" && (
                    <span className="table-caption">
                      <Badge tone="red">ИИ: грубая ошибка</Badge>
                      {item.scores?.final.critical === false &&
                        " · снята после апелляции"}
                    </span>
                  )}
                  {originalCriticalStatus(item) === "absent" &&
                    item.scores?.final.critical && (
                      <span className="table-caption">
                        Грубая ошибка добавлена после апелляции
                      </span>
                    )}
                </td>
                <td>
                  <div className="operator-cell">
                    <Avatar name={item.initials} small />
                    <span>{item.operator}</span>
                  </div>
                </td>
                <td>
                  <div className="channel-cell">
                    {item.channel === "agent chat" ? (
                      <MessageSquare size={16} />
                    ) : (
                      <Phone size={16} />
                    )}
                    <span>
                      {item.channel === "agent chat" ? "Чат" : "Звонок"}
                    </span>
                    <span className="language-tag">{item.language}</span>
                  </div>
                </td>
                <td>
                  <span className="table-date">
                    {item.date
                      ? new Intl.DateTimeFormat("ru-RU", {
                          day: "2-digit",
                          month: "short",
                          timeZone: "Asia/Qyzylorda",
                        }).format(new Date(item.date))
                      : "Дата не указана"}
                  </span>
                  {item.date && (
                    <span className="table-caption">
                      {new Intl.DateTimeFormat("ru-RU", {
                        hour: "2-digit",
                        minute: "2-digit",
                        timeZone: "Asia/Qyzylorda",
                      }).format(new Date(item.date))}
                    </span>
                  )}
                </td>
                <td>
                  <Score value={item.scores?.final.total ?? null} />
                </td>
                <td>{rowStatus(item)}</td>
                <td>
                  <button
                    className="icon-button row-open"
                    aria-label={`Открыть диалог ${item.id}`}
                    onClick={() => onOpen(item.id)}
                  >
                    <ArrowUpRight size={18} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && (
        <EmptyState
          title="Диалоги не найдены"
          description="Попробуйте другой запрос или сбросьте фильтры."
        >
          <Button onClick={resetFilters}>Сбросить фильтры</Button>
        </EmptyState>
      )}
      {!compact && (
        <div className="table-footer">
          <span>
            {filtered.length
              ? `${currentPage * pageSize + 1}–${Math.min((currentPage + 1) * pageSize, filtered.length)} из ${filtered.length} диалогов`
              : "0 диалогов"}
          </span>
          <div className="pagination">
            <button
              className="icon-button"
              aria-label="Предыдущая страница"
              disabled={currentPage === 0}
              onClick={() => setPage(currentPage - 1)}
            >
              <ChevronLeft size={17} />
            </button>
            <span>{currentPage + 1}</span>
            <button
              className="icon-button"
              aria-label="Следующая страница"
              disabled={(currentPage + 1) * pageSize >= filtered.length}
              onClick={() => setPage(currentPage + 1)}
            >
              <ChevronRight size={17} />
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
