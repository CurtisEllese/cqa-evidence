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
import { downloadExport } from "../services/api";
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
  const { data } = useStore();
  const [exporting, setExporting] = useState(false);
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
  const [localSearch, setLocalSearch] = useState("");
  const search = onSearchChange ? query : localSearch;
  const setSearch = onSearchChange ?? setLocalSearch;
  const [language, setLanguage] = useState("all");
  const [channel, setChannel] = useState("all");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [ascending, setAscending] = useState(false);
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
            (language === "all" || item.language === language) &&
            (channel === "all" || item.channel === channel) &&
            (status === "all" ||
              (status === "done" && item.evaluation) ||
              (status === "pending" && !item.evaluation) ||
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
    [data, search, query, language, channel, status, ascending],
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
    return item.evaluation ? (
      <Badge tone="green">
        <span className="status-dot" />
        Оценён
      </Badge>
    ) : (
      <Badge>
        <span className="status-dot" />
        Ожидает оценки
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
          <Button disabled={exporting} onClick={() => void exportWorkspace()}>
            <ArrowDownToLine size={16} />
            {exporting ? "Выгружаем…" : "Экспорт набора"}
          </Button>
        )}
      </div>
      {exportError && <Notice kind="error">{exportError}</Notice>}
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
                  <option value="pending">Ожидает оценки</option>
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
