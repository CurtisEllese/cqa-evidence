import type {
  Channel,
  Conversation,
  Language,
  Message,
  Speaker,
} from "./types";

const CHANNELS: Channel[] = ["agent chat", "voice call"];
const LANGUAGES: Language[] = ["RU", "KZ", "EN"];
const SPEAKERS: Speaker[] = ["operator", "client", "bot", "system"];
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const nonempty = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;
const CHAT_TIMESTAMP = /^\d{2,}:[0-5]\d$/;
const ISO_TIMESTAMP =
  /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):?[0-5]\d)$/;

function validDate(value: unknown, allowDateOnly = false): value is string {
  if (
    typeof value !== "string" ||
    (!ISO_TIMESTAMP.test(value) &&
      !(allowDateOnly && /^\d{4}-\d{2}-\d{2}$/.test(value)))
  )
    return false;
  if (!Number.isFinite(Date.parse(value))) return false;
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [
    31,
    leapYear ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ];
  return month >= 1 && month <= 12 && day >= 1 && day <= daysInMonth[month - 1];
}

function chatSeconds(timestamp: string): number {
  const [minutes, seconds] = timestamp.split(":").map(Number);
  return minutes * 60 + seconds;
}

function formatDuration(seconds: number): string {
  const elapsed = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
}

/** Import is atomic for malformed data. Existing and repeated IDs are skipped. */
export function parseImport(
  text: string,
  existingIds: string[],
): Conversation[] {
  if (new TextEncoder().encode(text).byteLength > 5 * 1024 * 1024)
    throw new Error("Файл слишком большой. Максимальный размер — 5 МБ.");
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("Не удалось прочитать JSON. Проверьте синтаксис файла.");
  }
  let records: unknown[];
  if (Array.isArray(payload)) records = payload;
  else {
    if (!object(payload))
      throw new Error(
        "Ожидается массив записей или объект ответа организаторов.",
      );
    if (payload.isSuccess === false)
      throw new Error(
        "Источник вернул isSuccess: false. Такой ответ нельзя импортировать.",
      );
    if (
      payload.isSuccess !== true ||
      !object(payload.data) ||
      !Array.isArray(payload.data.records)
    )
      throw new Error("Ожидается isSuccess: true и массив data.records.");
    records = payload.data.records;
  }
  if (!records.length) throw new Error("В файле нет диалогов для импорта.");
  if (records.length > 1000)
    throw new Error("За один раз можно импортировать не более 1000 диалогов.");
  const seen = new Set(existingIds);
  return records
    .map((record, index): Conversation => {
      const fail = (reason: string): never => {
        throw new Error(`Запись ${index + 1}: ${reason}`);
      };
      if (!object(record)) return fail("ожидается объект диалога.");
      const id = record.conversation_id ?? record.id;
      if (!nonempty(id)) return fail("не указан conversation_id.");
      if (!CHANNELS.includes(record.channel as Channel))
        return fail("channel должен быть «agent chat» или «voice call».");
      if (!LANGUAGES.includes(record.language as Language))
        return fail("language должен быть RU, KZ или EN.");
      if (!Array.isArray(record.transcript) || !record.transcript.length)
        return fail("transcript должен содержать хотя бы одну реплику.");
      const transcript: Message[] = record.transcript.map(
        (message, messageIndex) => {
          if (!object(message))
            return fail(`реплика ${messageIndex + 1} должна быть объектом.`);
          if (!SPEAKERS.includes(message.speaker as Speaker))
            return fail(
              `в реплике ${messageIndex + 1} указан неизвестный speaker.`,
            );
          if (!nonempty(message.text))
            return fail(`в реплике ${messageIndex + 1} отсутствует текст.`);
          if (!nonempty(message.timestamp))
            return fail(`в реплике ${messageIndex + 1} отсутствует timestamp.`);
          if (
            record.channel === "voice call"
              ? !validDate(message.timestamp)
              : !CHAT_TIMESTAMP.test(message.timestamp) ||
                !Number.isSafeInteger(chatSeconds(message.timestamp))
          ) {
            return fail(
              `timestamp реплики ${messageIndex + 1} должен быть ${record.channel === "voice call" ? "корректной датой ISO 8601 с часовым поясом, например 2026-11-07T10:00:00+05:00" : "в формате MM:SS, секунды от 00 до 59"}.`,
            );
          }
          return {
            id: `${id.trim()}-m${messageIndex + 1}`,
            timestamp: message.timestamp,
            speaker: message.speaker as Speaker,
            text: message.text,
          };
        },
      );
      let audioUrl: string | null = null;
      const sourceAudio = record.audio_url ?? record.audioUrl;
      if (
        sourceAudio !== null &&
        sourceAudio !== undefined &&
        !(typeof sourceAudio === "string" && sourceAudio.trim() === "")
      ) {
        if (!nonempty(sourceAudio))
          return fail("audio_url должен быть HTTP(S)-ссылкой или null.");
        let parsed: URL;
        try {
          parsed = new URL(sourceAudio);
        } catch {
          return fail("audio_url содержит некорректную ссылку.");
        }
        if (
          !["http:", "https:"].includes(parsed.protocol) ||
          parsed.username ||
          parsed.password
        )
          return fail(
            "audio_url должен быть безопасной HTTP(S)-ссылкой без логина и пароля.",
          );
        audioUrl = sourceAudio;
      }
      const title = (
        transcript.find((message) => message.speaker === "client") ??
        transcript[0]
      ).text;
      const operator = nonempty(record.operator)
        ? record.operator.trim()
        : "Оператор не указан";
      if (record.date !== undefined && !validDate(record.date, true))
        return fail(
          "date должна быть корректной датой ISO 8601, например 2026-11-07 или 2026-11-07T10:00:00+05:00.",
        );
      const times = transcript.map((message) =>
        record.channel === "voice call"
          ? Date.parse(message.timestamp) / 1000
          : chatSeconds(message.timestamp),
      );
      const earliestIndex = times.reduce(
        (earliest, time, position) =>
          time < times[earliest] ? position : earliest,
        0,
      );
      const earliestMessage =
        record.channel === "voice call" ? transcript[earliestIndex] : null;
      const date =
        (record.date as string | undefined) ??
        earliestMessage?.timestamp ??
        null;
      return {
        id: id.trim(),
        title: title.length > 78 ? `${title.slice(0, 75)}…` : title,
        operator,
        initials: nonempty(record.operator)
          ? operator
              .split(/\s+/)
              .slice(0, 2)
              .map((part) => part[0])
              .join("")
              .toUpperCase()
          : "—",
        language: record.language as Language,
        channel: record.channel as Channel,
        date,
        dateSource: "conversation",
        duration: formatDuration(times[times.length - 1] - times[0]),
        transcript,
        audioUrl,
        evaluation: null,
      };
    })
    .filter((conversation) => {
      if (seen.has(conversation.id)) return false;
      seen.add(conversation.id);
      return true;
    });
}
