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
  fetchCurrentBatch,
  startBatchRequest,
  commandBatch,
  ApiRequestError,
} from "./services/api";
import type { ServerState, DatasetInfo } from "./services/api";
import type { EvaluationBatch, StartBatchRequest } from "./services/api";
export type { EvaluationBatch, BatchItem } from "./services/api";

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
  resumeBatch: () => void;
  cancelBatch: () => void;
  batchReady: boolean;
  batchError: string;
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
  const currentBatch = useRef<EvaluationBatch | null>(null);
  const [batchReady, setBatchReady] = useState(false);
  const [batchError, setBatchError] = useState("");
  const batchCommandPending = useRef(false);
  const pendingBatchStart = useRef<StartBatchRequest | null>(null);
  const lastBatchWorkspace = useRef("");
  const acceptBatch = useCallback((incoming: EvaluationBatch | null) => {
    const current = currentBatch.current;
    if (
      current &&
      (!incoming ||
        incoming.createdAt < current.createdAt ||
        (incoming.id === current.id && incoming.revision < current.revision))
    )
      return;
    currentBatch.current = incoming;
    setBatch(incoming);
    setBatchReady(true);
    setBatchError("");
  }, []);
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
        const [result, restoredBatch] = await Promise.all([
          initialization,
          fetchCurrentBatch(),
        ]);
        acceptBatch(restoredBatch);
        if (restoredBatch && restoredBatch.status !== "completed")
          setSelectedGroup(restoredBatch.group);
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
    [acceptState, acceptBatch],
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
              "Оценка не завершена. Проверьте причину ошибки и повторите попытку.",
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
    if (!batchReady) return;
    for (const conversation of session?.data.conversations ?? []) {
      if (
        !currentBatch.current?.items.some(
          (item) => item.evaluationId === conversation.evaluation?.id,
        ) &&
        (conversation.evaluation?.status === "queued" ||
          conversation.evaluation?.status === "running")
      ) {
        void trackEvaluation(conversation.id, conversation.evaluation).catch(
          () => {
            /* Error is exposed by conversation ID. */
          },
        );
      }
    }
  }, [session, batchReady]);
  useEffect(
    () => () => {
      for (const controller of controllers.current) controller.abort();
    },
    [],
  );
  // Observation only: mounting, reloading or opening a second tab never submits work.
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function observe() {
      try {
        const incoming = await fetchCurrentBatch(controller.signal);
        if (controller.signal.aborted) return;
        acceptBatch(incoming);
        const snapshotKey = incoming
          ? `${incoming.id}:${incoming.revision}`
          : "";
        if (incoming && lastBatchWorkspace.current !== snapshotKey) {
          const workspace = await fetchWorkspace();
          if (!controller.signal.aborted) {
            acceptState(workspace);
            lastBatchWorkspace.current = snapshotKey;
          }
        }
      } catch (error) {
        if (!controller.signal.aborted)
          setBatchError(
            error instanceof Error
              ? error.message
              : "Не удалось получить очередь.",
          );
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(observe, 1000);
      }
    }
    void observe();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [acceptBatch, acceptState]);
  async function startBatch(group: string, ids: string[], version: number) {
    if (
      batchCommandPending.current ||
      !batchReady ||
      batchError ||
      !currentSession.current?.evaluationAvailable
    )
      return;
    batchCommandPending.current = true;
    try {
      // Retain an unresolved request's exact identity for a safe explicit retry.
      const body = pendingBatchStart.current ?? {
        schema_version: "2.0" as const,
        requestId: crypto.randomUUID(),
        group,
        conversationIds: [...new Set(ids)],
        checklistVersion: version,
        expectedRevision: currentSession.current.revision,
      };
      pendingBatchStart.current = body;
      const incoming = await startBatchRequest(body);
      pendingBatchStart.current = null;
      acceptBatch(incoming);
      acceptState(await fetchWorkspace());
    } catch (error) {
      setToast(
        error instanceof Error
          ? error.message
          : "Не удалось запустить очередь.",
      );
      // A confirmed rejection allows the user to review a fresh composition.
      if (error instanceof ApiRequestError) pendingBatchStart.current = null;
      try {
        acceptBatch(await fetchCurrentBatch());
        acceptState(await fetchWorkspace());
      } catch {
        /* polling reconnects */
      }
    } finally {
      batchCommandPending.current = false;
    }
  }
  async function changeBatch(command: "stop" | "resume" | "cancel") {
    const current = currentBatch.current;
    if (!current || batchCommandPending.current) return;
    batchCommandPending.current = true;
    try {
      acceptBatch(await commandBatch(current.id, command, current.revision));
    } catch (error) {
      setToast(
        error instanceof Error ? error.message : "Не удалось изменить очередь.",
      );
    } finally {
      batchCommandPending.current = false;
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
          void changeBatch("stop");
        },
        resumeBatch: () => {
          void changeBatch("resume");
        },
        cancelBatch: () => {
          void changeBatch("cancel");
        },
        batchReady,
        batchError,
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
          setToast("Новая версия чек-листа сохранена");
          return next.data.checklist;
        },
        evaluate: (conversationId, checklistVersion) => {
          const existing = session?.data.conversations.find(
            (conversation) => conversation.id === conversationId,
          )?.evaluation;
          if (
            currentBatch.current?.status !== undefined &&
            currentBatch.current.status !== "completed" &&
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
