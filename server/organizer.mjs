import { readFile, writeFile, mkdir, rename } from 'node:fs/promises';
import { resolve } from 'node:path';

const MAX_BYTES = 5 * 1024 * 1024;
export function validatePayload(payload, group = 'DEV') {
  const records = payload?.data?.records;
  if (payload?.isSuccess !== true || !Array.isArray(records) || !records.length) throw new Error('invalid_payload');
  if (payload.data.dataset_group_id !== group || payload.data.total_records !== records.length) throw new Error('invalid_count');
  if (group === 'DEV' && records.length !== 65) throw new Error('invalid_dev_count');
  if (new Set(records.map(record => record.conversation_id)).size !== records.length) throw new Error('duplicate_ids');
  return payload;
}

export function organizerMiddleware({ organizerUrl, cacheDirectory = resolve('local-data'), fetchImpl = fetch }) {
  let activeFetch;
  const cachePath = resolve(cacheDirectory, 'organizer-DEV.json');
  async function fetchDataset() {
    if (!organizerUrl) throw new Error('not_configured');
    const endpoint = new URL(organizerUrl);
    if (endpoint.protocol !== 'https:') throw new Error('not_configured');
    endpoint.searchParams.set('group_id', 'DEV');
    const response = await fetchImpl(endpoint, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(30000), redirect: 'error' });
    if (!response.ok) throw new Error('upstream_unavailable');
    const text = await response.text();
    if (Buffer.byteLength(text) > MAX_BYTES) throw new Error('invalid_payload');
    const snapshot = { payload: validatePayload(JSON.parse(text)), fetchedAt: new Date().toISOString() };
    await mkdir(cacheDirectory, { recursive: true });
    await writeFile(`${cachePath}.tmp`, JSON.stringify(snapshot), { mode: 0o600 });
    await rename(`${cachePath}.tmp`, cachePath);
    return { ...snapshot, source: 'live' };
  }
  async function loadDataset(refresh) {
    if (!refresh) {
      try {
        const snapshot = JSON.parse(await readFile(cachePath, 'utf8'));
        validatePayload(snapshot.payload);
        if (!Number.isFinite(Date.parse(snapshot.fetchedAt))) throw new Error('invalid_cache');
        return { ...snapshot, source: 'cache' };
      } catch { /* Missing or damaged cache is refetched, never replaced by invented data. */ }
    }
    if (!activeFetch) activeFetch = fetchDataset().finally(() => { activeFetch = undefined; });
    return activeFetch;
  }
  return async (req, res, next) => {
    const path = new URL(req.url || '/', 'http://localhost').pathname;
    if (path !== '/api/organizer/dataset') return next();
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (!['GET', 'POST'].includes(req.method)) { res.statusCode = 405; res.setHeader('Allow','GET, POST'); return res.end(JSON.stringify({ error: 'Метод не поддерживается.' })); }
    // Prevent a different web page from using this local proxy to read the private dataset.
    const origin = req.headers.origin;
    if (origin) {
      try { if (new URL(origin).host !== req.headers.host) throw new Error('origin'); }
      catch { res.statusCode = 403; return res.end(JSON.stringify({ error: 'Источник запроса не разрешён.' })); }
    }
    try {
      const snapshot = await loadDataset(req.method === 'POST');
      res.statusCode = 200;
      return res.end(JSON.stringify(snapshot));
    } catch (error) {
      res.statusCode = error?.message === 'not_configured' ? 503 : 502;
      const message = error?.message === 'not_configured'
        ? 'Укажите ORGANIZER_DEV_URL в локальном .env.local и перезапустите сервер.'
        : 'Не удалось получить проверенный набор DEV от заказчика. Повторите запрос позже. Сохранённые данные не изменены.';
      // Error text intentionally excludes the signed URL, response body and credentials.
      return res.end(JSON.stringify({ error: message }));
    }
  };
}
