import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleHelp,
  ClipboardList,
  LayoutDashboard,
  Menu,
  MessageSquare,
  Plus,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { StoreProvider, useStore } from "./store";
import {
  Avatar,
  Badge,
  Button,
  Modal,
  Notice,
  PageHeading,
} from "./components/ui";
import { Dashboard } from "./features/Dashboard";
import { ConversationTable } from "./features/Conversations";
import { ConversationDetail } from "./features/ConversationDetail";
import { ChecklistEditor } from "./features/ChecklistEditor";
import { Appeals } from "./features/Appeals";
import { ImportDialog } from "./features/ImportDialog";

const navigation = [
  { id: "overview", label: "Обзор", icon: LayoutDashboard },
  { id: "conversations", label: "Диалоги", icon: MessageSquare },
  { id: "checklist", label: "Чек-лист", icon: ClipboardList },
  { id: "appeals", label: "Апелляции", icon: ShieldCheck },
];
function getRoute() {
  try {
    return decodeURIComponent(location.hash.slice(1)) || "overview";
  } catch {
    return "overview";
  }
}
export default function App() {
  return (
    <StoreProvider>
      <Workspace />
    </StoreProvider>
  );
}
function Workspace() {
  const {
    data,
    toast,
    storageError,
    loading,
    loadError,
    refreshDataset,
    checklistDirty,
    setChecklistDirty,
  } = useStore();
  const [route, setRoute] = useState(getRoute);
  const [importOpen, setImportOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [pendingRoute, setPendingRoute] = useState<string | null>(null);
  useEffect(() => {
    const handle = () => {
      const next = getRoute();
      if (next === route) return;
      if (checklistDirty) {
        setPendingRoute(next);
        history.replaceState(null, "", `#${encodeURIComponent(route)}`);
        return;
      }
      setRoute(next);
      setSidebarOpen(false);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", handle);
    return () => window.removeEventListener("hashchange", handle);
  }, [route, checklistDirty]);
  useEffect(() => {
    if (!sidebarOpen) return;
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSidebarOpen(false);
        document.querySelector<HTMLButtonElement>(".mobile-menu")?.focus();
      }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [sidebarOpen]);
  const page = route.startsWith("dialog/")
    ? "conversations"
    : navigation.some((n) => n.id === route)
      ? route
      : "overview";
  function navigate(id: string) {
    if (id === "help") {
      setHelpOpen(true);
      return;
    }
    if (id === route) return;
    if (checklistDirty) {
      setPendingRoute(id);
      return;
    }
    location.hash = encodeURIComponent(id);
    setSidebarOpen(false);
  }
  function openDialog(id: string) {
    navigate(`dialog/${id}`);
  }
  const pending = data.appeals.filter((a) => a.status === "pending").length;
  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        Перейти к содержимому
      </a>
      {sidebarOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="Закрыть меню"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
        <button
          className="brand"
          onClick={() => navigate("overview")}
          aria-label="Freedom Quality — главная"
        >
          <img src="./brand/fbroker-logo.svg" alt="Freedom" />
          <span>QUALITY</span>
        </button>
        <div className="workspace-label">
          <div className="workspace-symbol">
            <ShieldCheck size={18} />
          </div>
          <div>
            <strong>Контроль качества</strong>
            <span>Рабочее пространство</span>
          </div>
        </div>
        <div className="nav-label">ПЛАТФОРМА</div>
        <nav aria-label="Основная навигация">
          {navigation.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? "active" : ""}`}
              onClick={() => navigate(item.id)}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={19} />
              <span>{item.label}</span>
              {item.id === "appeals" && pending > 0 ? (
                <span className="nav-count">{pending}</span>
              ) : item.id === page ? (
                <span className="nav-active-dot" />
              ) : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-promo">
          <div className="promo-art" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
          <div className="promo-eyebrow">FREEDOM QUALITY</div>
          <h3>
            От разговора
            <br />к доверию.
          </h3>
          <p>
            Больше ясности.
            <br />
            Выше качество.
          </p>
          <button onClick={() => setHelpOpen(true)}>
            О платформе
            <ArrowUpRight size={17} />
          </button>
        </div>
        <button className="sidebar-help" onClick={() => setHelpOpen(true)}>
          <CircleHelp size={18} />
          <span>Помощь и информация</span>
        </button>
        <div className="sidebar-user">
          <Avatar name="КК" />
          <div>
            <strong>Команда качества</strong>
            <span>Набор заказчика · DEV</span>
          </div>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-menu"
              aria-label="Открыть меню"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={21} />
            </button>
            <span>Рабочее пространство</span>
            <ChevronRight size={14} />
            <strong>{navigation.find((n) => n.id === page)?.label}</strong>
            {route.startsWith("dialog/") && (
              <>
                <ChevronRight size={14} />
                <strong>Диалог</strong>
              </>
            )}
          </div>
          <div className="topbar-right">
            <form
              className="global-search"
              onSubmit={(e) => {
                e.preventDefault();
                navigate("conversations");
              }}
            >
              <Search size={17} />
              <input
                placeholder="Найти диалог"
                aria-label="Глобальный поиск диалога"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  if (e.target.value) navigate("conversations");
                }}
              />
              {search && (
                <button
                  type="button"
                  aria-label="Очистить глобальный поиск"
                  onClick={() => setSearch("")}
                >
                  <X size={14} />
                </button>
              )}
            </form>
            <span className="topbar-divider" />
            <Badge tone="green">
              <span className="status-dot" />
              DEV
            </Badge>
            <Avatar name="КК" small />
          </div>
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className={`main-content ${route.startsWith("dialog/") ? "main-content-detail" : ""}`}
        >
          {loading && <Notice>Загружаем диалоги заказчика…</Notice>}
          {loadError && (
            <Notice kind="warning">
              {loadError}{" "}
              <Button disabled={loading} onClick={() => void refreshDataset()}>
                Повторить загрузку
              </Button>
            </Notice>
          )}
          {storageError && (
            <Notice kind="warning">
              Браузер не разрешил сохранить данные. Изменения доступны до
              закрытия страницы.
            </Notice>
          )}
          {route.startsWith("dialog/") ? (
            <ConversationDetail
              key={route}
              id={route.slice(7)}
              onBack={() => navigate("conversations")}
              onAppeals={() => navigate("appeals")}
            />
          ) : page === "overview" ? (
            <Dashboard
              onOpen={openDialog}
              onNavigate={navigate}
              onImport={() => setImportOpen(true)}
            />
          ) : page === "conversations" ? (
            <>
              <PageHeading
                eyebrow="РАБОТА С ДИАЛОГАМИ"
                title="За каждым диалогом — клиент"
                description="Находите важное, изучайте оценки и делайте сервис лучше."
                actions={
                  <Button variant="primary" onClick={() => setImportOpen(true)}>
                    <Plus size={18} />
                    Загрузить диалоги
                  </Button>
                }
              />
              <ConversationTable
                onOpen={openDialog}
                query={search}
                onSearchChange={setSearch}
              />
              <div className="page-footnote">
                <span>
                  {data.conversations.length} диалогов в рабочем пространстве
                </span>
                <span>RU · KZ · EN</span>
              </div>
            </>
          ) : page === "checklist" ? (
            <ChecklistEditor />
          ) : (
            <Appeals onOpen={openDialog} />
          )}
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <span>
            <Check size={16} />
          </span>
          {toast}
        </div>
      )}
      {importOpen && (
        <ImportDialog
          onClose={() => setImportOpen(false)}
          onImported={() => navigate("conversations")}
        />
      )}
      {pendingRoute && (
        <Modal
          title="Сохранить изменения перед выходом?"
          onClose={() => setPendingRoute(null)}
        >
          <p className="modal-intro">
            В чек-листе есть несохранённые изменения. Вернитесь в редактор,
            чтобы сохранить новую версию, или продолжите без сохранения.
          </p>
          <div className="modal-actions">
            <Button onClick={() => setPendingRoute(null)}>
              Вернуться к редактированию
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                const next = pendingRoute;
                setChecklistDirty(false);
                setRoute(next);
                location.hash = encodeURIComponent(next);
                setPendingRoute(null);
                setSidebarOpen(false);
                window.scrollTo(0, 0);
              }}
            >
              Уйти без сохранения
            </Button>
          </div>
        </Modal>
      )}
      {helpOpen && (
        <Modal
          title="Знакомьтесь: Freedom Quality"
          onClose={() => {
            setHelpOpen(false);
          }}
        >
          <p className="modal-intro">
            Пространство для оценки клиентских диалогов — от первого сообщения
            до обоснованного решения.
          </p>
          <div className="help-steps">
            {[
              [
                "01",
                "Настройте стандарт",
                "Задайте критерии, шкалы и веса в чек-листе.",
              ],
              [
                "02",
                "Изучите диалог",
                "Откройте оценку и перейдите к реплике, на которой основан вывод.",
              ],
              [
                "03",
                "Пересмотрите оценку",
                "Подайте апелляцию, рассмотрите её и сохраните новый итог.",
              ],
            ].map(([n, title, description]) => (
              <div key={n}>
                <span>{n}</span>
                <div>
                  <h3>{title}</h3>
                  <p>{description}</p>
                </div>
              </div>
            ))}
          </div>
          <Notice>
            Подключён набор DEV заказчика: 65 реальных диалогов. Чек-лист
            соответствует документации проекта. Сервис ИИ-оценки ещё не
            подключён. Изменения чек-листа сохраняются в этом браузере.
          </Notice>
          <div className="help-links">
            <a href="https://fbroker.kz/" target="_blank" rel="noreferrer">
              Визуальный ориентир — Freedom Инвестиции
              <ArrowUpRight size={15} />
            </a>
          </div>
          <div className="modal-actions">
            <Button disabled={loading} onClick={() => void refreshDataset()}>
              Обновить DEV
            </Button>
            <Button variant="primary" onClick={() => setHelpOpen(false)}>
              Всё понятно <Check size={16} />
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
