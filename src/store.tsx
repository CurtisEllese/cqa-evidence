import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { AppData, Checklist } from "./domain/types";
import { apiRequest } from "./services/api";
import type { ServerState, DatasetInfo } from "./services/api";

const LEGACY_KEY = "freedom-quality-organizer-v2";
const EMPTY_DATA: AppData = {
  conversations: [],
  appeals: [],
  checklist: { id: "", name: "", version: 0, criteria: [] },
};
// Share initial loading and migration across StrictMode effect replays.
let initialization: Promise<{
  state: ServerState;
  migrationError: string;
}> | null = null;
async function initialize() {
  const state = await apiRequest<ServerState>("/api/state");
  let legacy: string | null = null;
  try {
    localStorage.removeItem("freedom-quality-v1");
    legacy = localStorage.getItem(LEGACY_KEY);
  } catch {
    /* Server state does not require browser storage. */
  }
  if (!legacy) return { state, migrationError: "" };
  try {
    const saved = JSON.parse(legacy);
    if (saved.schema !== 2 || !saved.data)
      throw new Error("Неподдерживаемый формат прежних данных.");
    const migrated = await apiRequest<ServerState>("/api/migrate", {
      data: saved.data,
    });
    try {
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      /* Server migration is idempotent. */
    }
    return { state: migrated, migrationError: "" };
  } catch (error) {
    return {
      state,
      migrationError: `Прежние данные браузера пока не перенесены: ${error instanceof Error ? error.message : "ошибка переноса"} Они сохранены в браузере; повторите загрузку.`,
    };
  }
}

type Store = {
  data: AppData;
  dataset: DatasetInfo | null;
  ready: boolean;
  loading: boolean;
  loadError: string;
  reloadState: () => Promise<void>;
  refreshDataset: () => Promise<void>;
  evaluationAvailable: boolean;
  toast: string;
  notify: (message: string) => void;
  checklistDirty: boolean;
  setChecklistDirty: (dirty: boolean) => void;
  importConversations: (text: string) => Promise<number>;
  saveChecklist: (
    list: Checklist,
    expectedVersion: number,
  ) => Promise<Checklist>;
  evaluate: (id: string) => Promise<void>;
  createAppeal: (appeal: {
    conversationId: string;
    evaluationId: string;
    criterionId: string;
    comment: string;
  }) => Promise<void>;
  decideAppeal: (
    id: string,
    status: "accepted" | "rejected",
    decision: string,
    newValue: number | null,
  ) => Promise<void>;
};
const Context = createContext<Store | null>(null);
export function StoreProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<ServerState | null>(null);
  const [toast, setToast] = useState("");
  const [checklistDirty, setChecklistDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const acceptState = useCallback((incoming: ServerState) => {
    setSession((current) =>
      !current || incoming.revision >= current.revision ? incoming : current,
    );
  }, []);
  const load = useCallback(
    async (retry = false) => {
      setLoading(true);
      setLoadError("");
      if (retry || !initialization) initialization = initialize();
      try {
        const result = await initialization;
        acceptState(result.state);
        setLoadError(result.migrationError);
      } catch (error) {
        initialization = null;
        setLoadError(
          error instanceof Error
            ? error.message
            : "Не удалось загрузить рабочее пространство.",
        );
      } finally {
        setLoading(false);
      }
    },
    [acceptState],
  );
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  async function mutate(path: string, body: unknown) {
    const next = await apiRequest<ServerState>(path, body);
    acceptState(next);
    return next;
  }
  async function refreshDataset() {
    setLoading(true);
    setLoadError("");
    try {
      await mutate("/api/organizer/refresh", {});
      setToast("Набор заказчика обновлён");
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "Не удалось обновить набор.",
      );
    } finally {
      setLoading(false);
    }
  }
  return (
    <Context.Provider
      value={{
        data: session?.data ?? EMPTY_DATA,
        dataset: session?.dataset ?? null,
        ready: session !== null,
        loading,
        loadError,
        reloadState: () => load(true),
        refreshDataset,
        evaluationAvailable: session?.evaluationAvailable ?? false,
        toast,
        notify: setToast,
        checklistDirty,
        setChecklistDirty,
        importConversations: async (text) => {
          const next = await mutate("/api/import", { text });
          setToast(`Добавлено диалогов: ${next.imported ?? 0}`);
          return next.imported ?? 0;
        },
        saveChecklist: async (checklist, expectedVersion) => {
          const next = await mutate(
            `/api/checklists/${encodeURIComponent(checklist.id)}/versions`,
            { checklist, expectedVersion },
          );
          setToast("Новая версия чек-листа сохранена на сервере");
          return next.data.checklist;
        },
        evaluate: async (conversationId) => {
          await mutate("/api/evaluations", { conversationId });
          setToast("Оценка готова");
        },
        createAppeal: async (appeal) => {
          await mutate("/api/appeals", appeal);
          setToast("Апелляция отправлена на рассмотрение");
        },
        decideAppeal: async (id, status, decision, newValue) => {
          await mutate(`/api/appeals/${encodeURIComponent(id)}/decision`, {
            status,
            decision,
            newValue,
          });
          setToast(
            status === "accepted"
              ? "Апелляция принята. Итог пересчитан"
              : "Исходная оценка подтверждена",
          );
        },
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function useStore() {
  const context = useContext(Context);
  if (!context) throw new Error("StoreProvider required");
  return context;
}
