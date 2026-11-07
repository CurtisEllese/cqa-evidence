import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { AppData, Checklist, Evaluation } from "./domain/types";
import {
  fetchWorkspace,
  migrateWorkspace,
  refreshWorkspace,
  importWorkspace,
  saveChecklistVersion,
  submitAppeal,
  decideAppealRequest,
  startEvaluation,
  fetchEvaluation,
} from "./services/api";
import type { ServerState, DatasetInfo } from "./services/api";
import { isBatchBlockingFailure } from "./features/batchPlan";

const LEGACY_KEY = "freedom-quality-organizer-v2";
export type ConversationFilters = {
  language: string;
  channel: string;
  status: string;
  critical: string;
  page: number;
  filtersOpen: boolean;
  ascending: boolean;
};
export type BatchItem = {
  id: string;
  status:
    | "queued"
    | "running"
    | "completed"
    | "needs_review"
    | "failed"
    | "uncertain";
  error?: string;
};
export type EvaluationBatch = {
  group: string;
  version: number;
  items: BatchItem[];
  running: boolean;
  stopping: boolean;
  blockedReason?: string;
};
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
  const state = await fetchWorkspace();
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
    const migrated = await migrateWorkspace(saved.data);
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
  selectedGroup: string;
  setSelectedGroup: (group: string) => void;
  filters: ConversationFilters;
  setFilters: (patch: Partial<ConversationFilters>) => void;
  batch: EvaluationBatch | null;
  startBatch: (group: string, ids: string[], version: number) => Promise<void>;
  stopBatch: () => void;
  data: AppData;
  dataset: DatasetInfo | null;
  datasets: DatasetInfo[];
  ready: boolean;
  loading: boolean;
  loadError: string;
  reloadState: () => Promise<void>;
  refreshDataset: (groupId?: string) => Promise<boolean>;
  evaluationAvailable: boolean;
  evaluatingIds: string[];
  evaluationErrors: Record<string, string>;
  toast: string;
  notify: (message: string) => void;
  checklistDirty: boolean;
  setChecklistDirty: (dirty: boolean) => void;
  importConversations: (text: string) => Promise<number>;
  saveChecklist: (
    list: Checklist,
    expectedVersion: number,
  ) => Promise<Checklist>;
  evaluate: (id: string, checklistVersion?: number) => Promise<void>;
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
  const [selectedGroup, setSelectedGroup] = useState("");
  const [filters, updateFilters] = useState<ConversationFilters>({
    language: "all",
    channel: "all",
    status: "all",
    critical: "all",
    page: 0,
    filtersOpen: false,
    ascending: false,
  });
  const [batch, setBatch] = useState<EvaluationBatch | null>(null);
  const batchRunning = useRef(false);
  const batchStop = useRef(false);
  const currentSession = useRef<ServerState | null>(null);
  const [session, setSession] = useState<ServerState | null>(null);
  const [toast, setToast] = useState("");
  const [checklistDirty, setChecklistDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const datasetRequestPending = useRef(false);
  const failedDatasetGroup = useRef<string | null>(null);
  const [evaluatingIds, setEvaluatingIds] = useState<string[]>([]);
  const [evaluationErrors, setEvaluationErrors] = useState<
    Record<string, string>
  >({});
  const evaluationRequests = useRef(
    new Map<string, Promise<Evaluation | undefined>>(),
  );
  const controllers = useRef(new Set<AbortController>());
  const acceptState = useCallback((incoming: ServerState) => {
    if (
      !currentSession.current ||
      incoming.revision >= currentSession.current.revision
    )
      currentSession.current = incoming;
    if (incoming.dataset)
      setSelectedGroup(
        (current) => current || `group:${incoming.dataset!.group}`,
      );
    setSession((current) =>
      !current || incoming.revision >= current.revision ? incoming : current,
    );
  }, []);
  const load = useCallback(
    async (retry = false) => {
      failedDatasetGroup.current = null;
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
  function trackEvaluation(
    conversationId: string,
    existing?: Evaluation,
    checklistVersion?: number,
  ): Promise<Evaluation | undefined> {
    const active = evaluationRequests.current.get(conversationId);
    if (active) return active;
    const controller = new AbortController();
    controllers.current.add(controller);
    setEvaluatingIds((current) => [...new Set([...current, conversationId])]);
    setEvaluationErrors((current) => {
      const next = { ...current };
      delete next[conversationId];
      return next;
    });
    const task = (async () => {
      try {
        let job =
          existing ?? (await startEvaluation(conversationId, checklistVersion));
        acceptState(await fetchWorkspace());
        while (job.status === "queued" || job.status === "running") {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          if (controller.signal.aborted) return;
          job = await fetchEvaluation(job.id, controller.signal);
        }
        if (controller.signal.aborted) return;
        acceptState(await fetchWorkspace());
        if (job.status === "completed") setToast("Оценка готова");
        else if (job.status === "needs_review")
          setToast("Результат требует проверки доказательств");
        else
          throw new Error(
            job.error ??
              "Оценка не завершена. Проверьте сообщение сервера и повторите попытку.",
          );
        return job;
      } catch (error) {
        if (controller.signal.aborted) return;
        const message =
          error instanceof Error
            ? error.message
            : "Не удалось получить оценку.";
        setEvaluationErrors((current) => ({
          ...current,
          [conversationId]: message,
        }));
        throw error;
      } finally {
        evaluationRequests.current.delete(conversationId);
        controllers.current.delete(controller);
        setEvaluatingIds((current) =>
          current.filter((id) => id !== conversationId),
        );
      }
    })();
    evaluationRequests.current.set(conversationId, task);
    return task;
  }
  useEffect(() => {
    for (const conversation of session?.data.conversations ?? []) {
      if (
        conversation.evaluation?.status === "queued" ||
        conversation.evaluation?.status === "running"
      ) {
        void trackEvaluation(conversation.id, conversation.evaluation).catch(
          () => {
            /* Error is exposed by conversation ID. */
          },
        );
      }
    }
  }, [session]);
  useEffect(
    () => () => {
      batchStop.current = true;
      for (const controller of controllers.current) controller.abort();
    },
    [],
  );
  async function startBatch(group: string, ids: string[], version: number) {
    if (
      batchRunning.current ||
      evaluationRequests.current.size ||
      !currentSession.current?.evaluationAvailable
    )
      return;
    const allowed = [...new Set(ids)].filter(
      (id) =>
        currentSession.current?.data.conversations.some((c) => c.id === id) &&
        !currentSession.current?.data.appeals.some(
          (a) => a.conversationId === id,
        ),
    );
    if (!allowed.length) return;
    batchRunning.current = true;
    batchStop.current = false;
    setBatch({
      group,
      version,
      items: allowed.map((id) => ({ id, status: "queued" })),
      running: true,
      stopping: false,
    });
    const updateItem = (id: string, patch: Partial<BatchItem>) =>
      setBatch((current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.id === id ? { ...item, ...patch } : item,
              ),
            }
          : current,
      );
    try {
      for (const id of allowed) {
        if (batchStop.current) break;
        updateItem(id, { status: "running" });
        const previousId = currentSession.current?.data.conversations.find(
          (c) => c.id === id,
        )?.evaluation?.id;
        try {
          const existing = currentSession.current?.data.conversations.find(
            (c) => c.id === id,
          )?.evaluation;
          const result = await trackEvaluation(
            id,
            existing && ["queued", "running"].includes(existing.status)
              ? existing
              : undefined,
            version,
          );
          if (!result) {
            batchStop.current = true;
            updateItem(id, {
              status: "uncertain",
              error:
                "Проверка прервана. Обновите данные перед повторным запуском.",
            });
            break;
          }
          updateItem(id, {
            status:
              result.status === "needs_review" ? "needs_review" : "completed",
          });
        } catch (error) {
          const evaluation = currentSession.current?.data.conversations.find(
            (c) => c.id === id,
          )?.evaluation;
          const knownFailure =
            evaluation &&
            evaluation.id !== previousId &&
            ["failed", "failed_validation", "retryable"].includes(
              evaluation.status,
            );
          updateItem(id, {
            status: knownFailure ? "failed" : "uncertain",
            error:
              error instanceof Error
                ? error.message
                : "Не удалось получить результат",
          });
          if (knownFailure && isBatchBlockingFailure(evaluation)) {
            setBatch((current) =>
              current
                ? {
                    ...current,
                    blockedReason:
                      evaluation.error ??
                      "Проверьте доступность сервиса модели перед продолжением.",
                  }
                : current,
            );
            batchStop.current = true;
            break;
          }
          if (!knownFailure) {
            batchStop.current = true;
            break;
          }
        }
      }
    } finally {
      batchRunning.current = false;
      setBatch((current) =>
        current ? { ...current, running: false, stopping: false } : current,
      );
    }
  }
  async function refreshDataset(groupId = "DEV") {
    if (datasetRequestPending.current) return false;
    datasetRequestPending.current = true;
    setLoading(true);
    setLoadError("");
    try {
      const next = await refreshWorkspace(groupId.trim());
      acceptState(next);
      failedDatasetGroup.current = null;
      setToast("Набор загружен");
      return true;
    } catch (error) {
      failedDatasetGroup.current = groupId.trim();
      setLoadError(
        error instanceof Error ? error.message : "Не удалось обновить набор.",
      );
      return false;
    } finally {
      datasetRequestPending.current = false;
      setLoading(false);
    }
  }
  return (
    <Context.Provider
      value={{
        selectedGroup,
        setSelectedGroup,
        filters,
        setFilters: (patch) =>
          updateFilters((current) => ({ ...current, ...patch })),
        batch,
        startBatch,
        stopBatch: () => {
          batchStop.current = true;
          setBatch((current) =>
            current ? { ...current, stopping: true } : current,
          );
        },
        data: session?.data ?? EMPTY_DATA,
        dataset: session?.dataset ?? null,
        datasets: session?.datasets ?? [],
        ready: session !== null,
        loading,
        loadError,
        reloadState: () => {
          const group = failedDatasetGroup.current;
          return group === null
            ? load(true)
            : refreshDataset(group).then(() => {});
        },
        refreshDataset,
        evaluationAvailable: session?.evaluationAvailable ?? false,
        evaluatingIds,
        evaluationErrors,
        toast,
        notify: setToast,
        checklistDirty,
        setChecklistDirty,
        importConversations: async (text) => {
          const next = await importWorkspace(text);
          acceptState(next);
          setToast(`Добавлено диалогов: ${next.imported ?? 0}`);
          return next.imported ?? 0;
        },
        saveChecklist: async (checklist, expectedVersion) => {
          const next = await saveChecklistVersion(checklist, expectedVersion);
          acceptState(next);
          setToast("Новая версия чек-листа сохранена на сервере");
          return next.data.checklist;
        },
        evaluate: (conversationId, checklistVersion) => {
          const existing = session?.data.conversations.find(
            (conversation) => conversation.id === conversationId,
          )?.evaluation;
          if (
            batchRunning.current &&
            !evaluationRequests.current.has(conversationId)
          )
            return Promise.reject(
              new Error(
                "Дождитесь окончания очереди набора или остановите её.",
              ),
            );
          return trackEvaluation(
            conversationId,
            existing &&
              (existing.status === "queued" || existing.status === "running")
              ? existing
              : undefined,
            checklistVersion,
          ).then(() => {});
        },
        createAppeal: async (appeal) => {
          acceptState(await submitAppeal(appeal));
          setToast("Апелляция отправлена на рассмотрение");
        },
        decideAppeal: async (id, status, decision, newValue) => {
          acceptState(
            await decideAppealRequest(id, {
              status,
              decision,
              newValue,
            }),
          );
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
