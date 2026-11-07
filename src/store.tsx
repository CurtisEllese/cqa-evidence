import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { AppData, Checklist, Conversation, Appeal } from "./domain/types";
import { initialData } from "./domain/data";
import { fetchOrganizerDataset } from "./services/organizer";
import type { DatasetInfo } from "./services/organizer";

const STORAGE_KEY = "freedom-quality-organizer-v2";
type Persisted = { schema: 2; data: AppData; dataset: DatasetInfo | null };
function readData(): Persisted {
  try {
    localStorage.removeItem("freedom-quality-v1");
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const saved = JSON.parse(raw);
      if (
        saved.schema === 2 &&
        Array.isArray(saved.data?.conversations) &&
        Array.isArray(saved.data?.checklist?.criteria) &&
        Array.isArray(saved.data?.appeals) &&
        !saved.data.conversations.some((item: Conversation) =>
          item.id.startsWith("demo-"),
        )
      )
        return saved;
    }
  } catch {
    /* Storage can be unavailable; use a fresh, empty session. */
  }
  return { schema: 2, data: structuredClone(initialData), dataset: null };
}
type Store = {
  data: AppData;
  dataset: DatasetInfo | null;
  loading: boolean;
  loadError: string;
  refreshDataset: () => Promise<void>;
  evaluationAvailable: boolean;
  storageError: boolean;
  toast: string;
  notify: (message: string) => void;
  checklistDirty: boolean;
  setChecklistDirty: (dirty: boolean) => void;
  importConversations: (items: Conversation[]) => void;
  saveChecklist: (list: Checklist) => void;
  evaluate: (id: string) => void;
  createAppeal: (appeal: Appeal) => void;
  decideAppeal: (
    id: string,
    status: "accepted" | "rejected",
    decision: string,
    newValue: number | null,
  ) => void;
};
const Context = createContext<Store | null>(null);
export function StoreProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Persisted>(readData);
  const { data, dataset } = session;
  const [toast, setToast] = useState("");
  const [storageError, setStorageError] = useState(false);
  const [checklistDirty, setChecklistDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const activeRequest = useRef<AbortController | null>(null);
  const setData = (update: (current: AppData) => AppData) =>
    setSession((current) => ({ ...current, data: update(current.data) }));
  const syncDataset = useCallback(async (refresh: boolean) => {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setLoading(true);
    setLoadError("");
    try {
      const result = await fetchOrganizerDataset(refresh, controller.signal);
      if (controller.signal.aborted) return;
      setSession((current) => {
        const incoming = new Set(result.conversations.map((item) => item.id));
        const sourceIds = new Set(current.dataset?.conversationIds ?? []);
        const conversations = result.conversations.map((item) => {
          const saved = current.data.conversations.find(
            (old) => old.id === item.id,
          );
          return saved &&
            JSON.stringify(saved.transcript) === JSON.stringify(item.transcript)
            ? { ...item, evaluation: saved.evaluation }
            : item;
        });
        // DEV refresh is authoritative: old source rows and obsolete evaluations never survive it.
        const manual = current.data.conversations.filter(
          (item) =>
            !incoming.has(item.id) &&
            !sourceIds.has(item.id) &&
            !item.id.startsWith("demo-"),
        );
        const combined = [...conversations, ...manual];
        const evaluations = new Set(
          combined.map((item) => item.evaluation?.id).filter(Boolean),
        );
        return {
          schema: 2,
          dataset: result.info,
          data: {
            ...current.data,
            conversations: combined,
            appeals: current.data.appeals.filter((item) =>
              evaluations.has(item.evaluationId),
            ),
          },
        };
      });
      if (refresh) setToast("DEV обновлён: 65 диалогов заказчика");
    } catch (error) {
      if (!controller.signal.aborted)
        setLoadError(
          error instanceof Error
            ? error.message
            : "Не удалось загрузить набор заказчика.",
        );
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void syncDataset(false);
    return () => activeRequest.current?.abort();
  }, [syncDataset]);
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  }, [session]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  return (
    <Context.Provider
      value={{
        data,
        dataset,
        loading,
        loadError,
        refreshDataset: () => syncDataset(true),
        evaluationAvailable: false,
        toast,
        storageError,
        notify: setToast,
        checklistDirty,
        setChecklistDirty,
        importConversations: (items) => {
          setData((current) => ({
            ...current,
            conversations: [...items, ...current.conversations],
          }));
          setToast(`Добавлено диалогов: ${items.length}`);
        },
        saveChecklist: (checklist) => {
          setData((current) => ({
            ...current,
            checklist: { ...checklist, version: current.checklist.version + 1 },
          }));
          setToast("Новая версия чек-листа сохранена");
        },
        evaluate: () => {
          throw new Error(
            "Сервис автоматической оценки ещё не подключён. Загруженные диалоги доступны для просмотра.",
          );
        },
        createAppeal: (appeal) => {
          setData((current) => ({
            ...current,
            appeals: [appeal, ...current.appeals],
          }));
          setToast("Апелляция отправлена на рассмотрение");
        },
        decideAppeal: (id, status, decision, newValue) => {
          setData((current) => ({
            ...current,
            appeals: current.appeals.map((item) =>
              item.id === id ? { ...item, status, decision, newValue } : item,
            ),
          }));
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
