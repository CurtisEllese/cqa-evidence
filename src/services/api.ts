import type * as Wire from "../generated/contracts";
import type { Checklist, Evaluation } from "../domain/types";
import {
  checklistFromWire,
  checklistToWire,
  evaluationFromWire,
  scoreFromWire,
  stateFromWire,
} from "./viewmodels";
import { toDecimal } from "./decimal";
export type { WorkspaceViewModel as ServerState } from "./viewmodels";
export type DatasetInfo = NonNullable<Wire.WorkspaceState["dataset"]>;
export type AppealDecision = {
  status: "accepted" | "rejected";
  decision: string;
  newValue: number | null;
};
export type NewAppeal = {
  conversationId: string;
  evaluationId: string;
  criterionId: string;
  comment: string;
};

export class ApiRequestError extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly retryable: boolean;
  readonly details: unknown;
  constructor(error: Wire.ApiError) {
    super(error.message);
    this.name = "ApiRequestError";
    this.code = error.code;
    this.requestId = error.request_id;
    this.retryable = error.retryable;
    this.details = error.details;
  }
}
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
function responseError(payload: unknown): Error {
  if (
    isObject(payload) &&
    payload.schema_version === "2.0" &&
    typeof payload.message === "string" &&
    typeof payload.code === "string" &&
    typeof payload.request_id === "string" &&
    typeof payload.retryable === "boolean"
  )
    return new ApiRequestError(payload as Wire.ApiError);
  return new Error(
    "Сервер вернул ошибку в неподдерживаемом формате. Повторите попытку.",
  );
}
async function requestJson(
  path: string,
  body?: { schema_version: "2.0" },
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal,
  });
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(
      "Сервер вернул непонятный ответ. Проверьте соединение и повторите попытку.",
    );
  }
  if (!response.ok) throw responseError(payload);
  return payload;
}
async function request<T extends { schema_version: "2.0" }>(
  path: string,
  body?: { schema_version: "2.0" },
  signal?: AbortSignal,
): Promise<T> {
  const payload = await requestJson(path, body, signal);
  if (!isObject(payload) || payload.schema_version !== "2.0")
    throw new Error(
      "Версия ответа сервера не поддерживается. Требуется контракт v2.0.",
    );
  return payload as T;
}
export async function fetchChecklists(
  signal?: AbortSignal,
): Promise<Checklist[]> {
  const payload = await requestJson("/api/checklists", undefined, signal);
  if (
    !Array.isArray(payload) ||
    payload.some((item) => !isObject(item) || item.schema_version !== "2.0")
  )
    throw new Error("Список версий чек-листа не соответствует контракту v2.0.");
  return (payload as Wire.Checklist[]).map(checklistFromWire);
}
export const fetchWorkspace = async () =>
  stateFromWire(await request<Wire.WorkspaceState>("/api/state"));
export const refreshWorkspace = async (groupId = "DEV") => {
  const body: Wire.RefreshRequest = { schema_version: "2.0", groupId };
  return stateFromWire(
    await request<Wire.WorkspaceState>("/api/organizer/refresh", body),
  );
};
export async function migrateWorkspace(data: Record<string, unknown>) {
  const body: Wire.MigrationRequest = { schema_version: "2.0", data };
  return stateFromWire(
    await request<Wire.WorkspaceState>("/api/migrate", body),
  );
}
export async function importWorkspace(text: string) {
  const body: Wire.ImportRequest = { schema_version: "2.0", text };
  return stateFromWire(await request<Wire.WorkspaceState>("/api/import", body));
}
export async function saveChecklistVersion(
  checklist: Checklist,
  expectedVersion: number,
) {
  const body: Wire.VersionRequest = {
    schema_version: "2.0",
    checklist: checklistToWire(checklist),
    expectedVersion,
  };
  return stateFromWire(
    await request<Wire.WorkspaceState>(
      `/api/checklists/${encodeURIComponent(checklist.id)}/versions`,
      body,
    ),
  );
}
export async function submitAppeal(appeal: NewAppeal) {
  const body: Wire.CreateAppealRequest = { schema_version: "2.0", ...appeal };
  return stateFromWire(
    await request<Wire.WorkspaceState>("/api/appeals", body),
  );
}
function decisionToWire(decision: AppealDecision): Wire.DecideAppealRequest {
  return {
    schema_version: "2.0",
    ...decision,
    newValue: decision.newValue === null ? null : toDecimal(decision.newValue),
  };
}
export async function decideAppealRequest(
  id: string,
  decision: AppealDecision,
) {
  return stateFromWire(
    await request<Wire.WorkspaceState>(
      `/api/appeals/${encodeURIComponent(id)}/decision`,
      decisionToWire(decision),
    ),
  );
}
export async function previewAppeal(
  id: string,
  decision: AppealDecision,
  signal?: AbortSignal,
) {
  return scoreFromWire(
    await request<Wire.Score>(
      `/api/appeals/${encodeURIComponent(id)}/preview`,
      decisionToWire(decision),
      signal,
    ),
  );
}
export async function startEvaluation(
  conversationId: string,
  checklistVersion?: number,
): Promise<Evaluation> {
  const body: Wire.StartEvaluationRequest = {
    schema_version: "2.0",
    conversationId,
    ...(checklistVersion === undefined ? {} : { checklistVersion }),
  };
  return evaluationFromWire(
    await request<Wire.EvaluationResult>("/api/evaluations", body),
  );
}
export async function fetchEvaluation(
  id: string,
  signal?: AbortSignal,
): Promise<Evaluation> {
  return evaluationFromWire(
    await request<Wire.EvaluationResult>(
      `/api/evaluations/${encodeURIComponent(id)}`,
      undefined,
      signal,
    ),
  );
}
export async function downloadExport(conversationId?: string): Promise<void> {
  const response = await fetch(
    conversationId
      ? `/api/conversations/${encodeURIComponent(conversationId)}/export`
      : "/api/export",
  );
  if (!response.ok)
    throw responseError(await response.json().catch(() => null));
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = conversationId
    ? "freedom-conversation.json"
    : "freedom-workspace.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
