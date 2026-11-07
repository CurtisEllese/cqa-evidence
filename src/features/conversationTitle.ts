import type { Conversation } from "../domain/types";

const callDate = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  timeZone: "Asia/Qyzylorda",
});
const fullCallDate = new Intl.DateTimeFormat("ru-RU", {
  dateStyle: "long",
  timeZone: "Asia/Qyzylorda",
});
const callTime = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Qyzylorda",
});

export function callDateLabel(
  conversation: Pick<Conversation, "date" | "dateSource">,
  full = false,
): string | null {
  // Import time is not evidence of when the call took place.
  if (conversation.dateSource === "import" || !conversation.date) return null;
  const date = new Date(conversation.date);
  if (!Number.isFinite(date.getTime())) return null;
  return (full ? fullCallDate : callDate).format(date);
}

export function callTimeLabel(
  conversation: Pick<Conversation, "date" | "dateSource">,
): string | null {
  if (!callDateLabel(conversation) || !conversation.date?.includes("T"))
    return null;
  return callTime.format(new Date(conversation.date));
}

/** Presentation only: never replace the source title or use this as an ID. */
export function conversationTitle(
  conversation: Pick<Conversation, "title" | "channel" | "date" | "dateSource">,
): string {
  if (conversation.channel !== "voice call") return conversation.title;
  const title = conversation.title.trim();
  const meaningful = /\p{L}/u.test(title);
  const dateLabel = callDateLabel(conversation);
  if (!dateLabel) return meaningful ? title : "Звонок · дата не указана";
  return meaningful ? `${title} · ${dateLabel}` : `Звонок от ${dateLabel}`;
}
