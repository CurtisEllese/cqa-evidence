import type { AppData, EvaluationScore } from "../domain/types";
import { mapServerState } from "./evaluation";

export type DatasetInfo = {
  group: string;
  fetchedAt: string;
  source: string;
  total: number;
  conversationIds: string[];
};
export type ServerState = {
  data: AppData;
  dataset: DatasetInfo | null;
  evaluationAvailable: boolean;
  revision: number;
  imported?: number;
};
export type AppealDecision = {
  status: "accepted" | "rejected";
  decision: string;
  newValue: number | null;
};

export async function apiRequest<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal,
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(
      "Сервер вернул непонятный ответ. Проверьте соединение и повторите попытку.",
    );
  }
  if (!response.ok)
    throw new Error(
      typeof payload?.error === "string"
        ? payload.error
        : typeof payload?.error?.message === "string"
          ? payload.error.message
        : typeof payload?.message === "string"
          ? payload.message
          : "Не удалось выполнить запрос. Повторите попытку.",
    );
  return (payload?.data?.conversations && payload?.data?.checklist ? mapServerState(payload) : payload) as T;
}

export const previewAppeal = (
  id: string,
  decision: AppealDecision,
  signal?: AbortSignal,
) =>
  apiRequest<EvaluationScore>(
    `/api/appeals/${encodeURIComponent(id)}/preview`,
    decision,
    signal,
  );

export async function downloadExport(conversationId?: string): Promise<void> {
  const response = await fetch(
    conversationId
      ? `/api/conversations/${encodeURIComponent(conversationId)}/export`
      : "/api/export",
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.error === "string"
        ? body.error
        : "Не удалось выгрузить данные.",
    );
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = conversationId
    ? "freedom-conversation.json"
    : "freedom-workspace.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
