import { useEffect, useState } from "react";
import {
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
import { Avatar, Button, Modal, Notice, PageHeading } from "./components/ui";
import { Dashboard } from "./features/Dashboard";
import { ConversationTable } from "./features/Conversations";
import { ConversationDetail } from "./features/ConversationDetail";
import { ChecklistEditor } from "./features/ChecklistEditor";
import { Appeals } from "./features/Appeals";
import { ImportDialog } from "./features/ImportDialog";
import { BatchEvaluation } from "./features/BatchEvaluation";
import { AuthGate, SignOutButton } from "./components/AuthGate";

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
    <AuthGate>
      <StoreProvider>
        <Workspace />
      </StoreProvider>
    </AuthGate>
  );
}
function Workspace() {
  const {
    data,
    toast,
    ready,
    loading,
    loadError,
    reloadState,
    checklistDirty,
    setChecklistDirty,
    batch,
    stopBatch,
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
        <button className="sidebar-help" onClick={() => setHelpOpen(true)}>
          <CircleHelp size={18} />
          <span>Помощь и информация</span>
        </button>
        <div className="sidebar-user">
          <Avatar name="КК" />
          <div>
            <strong>Команда качества</strong>
            <span>Рабочее пространство</span>
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
            <Avatar name="КК" small />
            <SignOutButton />
          </div>
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className={`main-content ${route.startsWith("dialog/") ? "main-content-detail" : ""}`}
        >
          {loading && <Notice>Загружаем диалоги…</Notice>}
          {loadError && (
            <Notice kind="warning">
              {loadError}{" "}
              <Button disabled={loading} onClick={() => void reloadState()}>
                Повторить загрузку
              </Button>
            </Notice>
          )}
          {batch?.running && route !== "conversations" && (
            <div className="batch-global" role="status">
              <span>
                Оцениваем {batch.group} ·{" "}
                {
                  batch.items.filter(
                    (item) => !["queued", "running"].includes(item.status),
                  ).length
                }{" "}
                из {batch.items.length}
              </span>
              <Button onClick={() => navigate("conversations")}>
                К очереди
              </Button>
              <Button disabled={batch.stopping} onClick={stopBatch}>
                {batch.stopping ? "Останавливаем…" : "Остановить очередь"}
              </Button>
            </div>
          )}
          {!ready ? null : route.startsWith("dialog/") ? (
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
                title="Диалоги"
                description="Оценки, нарушения и результаты проверки."
                actions={
                  <Button variant="primary" onClick={() => setImportOpen(true)}>
                    <Plus size={18} />
                    Загрузить диалоги
                  </Button>
                }
              />
              <BatchEvaluation onOpen={openDialog} />
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
          title="Помощь"
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
          <div className="modal-actions">
            <Button
              onClick={() => {
                setHelpOpen(false);
                setImportOpen(true);
              }}
            >
              Загрузить диалоги
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
