/** Hook for the audio owner: authenticated, bounded blob; always call dispose. */
import { getEnterpriseSession } from "./auth";

const MAX_AUDIO_BYTES = 32 * 1024 * 1024;
export async function loadAuthorizedAudio(src: string, signal?: AbortSignal): Promise<{ url: string; dispose: () => void }> {
  const session = await getEnterpriseSession();
  if (session.mode === "local") return { url: src, dispose() {} };
  const target = new URL(src, location.origin);
  if (target.origin !== location.origin || !/^\/api\/conversations\/[^/]+\/audio$/.test(target.pathname)) {
    throw new Error("Эта запись пока недоступна в корпоративном режиме.");
  }
  const response = await session.fetch(target.pathname, { signal });
  if (!response.ok || !response.body) throw new Error("Не удалось получить доступ к записи.");
  const length = Number(response.headers.get("content-length") || "0");
  if (length > MAX_AUDIO_BYTES) {
    await response.body.cancel();
    throw new Error("Запись слишком большая для защищённого воспроизведения.");
  }
  const type = response.headers.get("content-type")?.split(";")[0] || "";
  if (!type.startsWith("audio/")) {
    await response.body.cancel();
    throw new Error("Сервер не предоставил аудиозапись.");
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let total = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      total += value.length;
      if (total > MAX_AUDIO_BYTES) {
        await reader.cancel();
        throw new Error("Запись слишком большая для защищённого воспроизведения.");
      }
      chunks.push(new Uint8Array(value));
    }
    signal?.throwIfAborted();
    const url = URL.createObjectURL(new Blob(chunks, { type }));
    return { url, dispose: () => URL.revokeObjectURL(url) };
  } finally { reader.releaseLock(); }
}
