import { parseImport } from "../domain/import";
import type { Conversation } from "../domain/types";

export type DatasetInfo = {
  group: "DEV";
  fetchedAt: string;
  source: "live" | "cache";
  total: number;
  conversationIds: string[];
};
export async function fetchOrganizerDataset(
  refresh = false,
  signal?: AbortSignal,
): Promise<{ conversations: Conversation[]; info: DatasetInfo }> {
  const response = await fetch("/api/organizer/dataset", {
    method: refresh ? "POST" : "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(
      "Сервис данных недоступен. Запустите приложение через npm run dev или npm run preview.",
    );
  }
  if (!response.ok)
    throw new Error(
      typeof body?.error === "string"
        ? body.error
        : "Не удалось загрузить диалоги заказчика.",
    );
  if (
    body?.payload?.data?.dataset_group_id !== "DEV" ||
    body.payload.data.total_records !== 65 ||
    !["live", "cache"].includes(body.source) ||
    !Number.isFinite(Date.parse(body.fetchedAt))
  )
    throw new Error("Ответ не соответствует набору DEV заказчика.");
  const conversations = parseImport(JSON.stringify(body.payload), []);
  if (
    conversations.length !== 65 ||
    conversations.some((item) => item.id.startsWith("demo-"))
  )
    throw new Error(
      "В наборе DEV должны быть 65 уникальных диалогов заказчика.",
    );
  return {
    conversations,
    info: {
      group: "DEV",
      fetchedAt: body.fetchedAt,
      source: body.source,
      total: conversations.length,
      conversationIds: conversations.map((item) => item.id),
    },
  };
}
