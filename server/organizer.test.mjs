import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { organizerMiddleware, validatePayload } from './organizer.mjs';

// Synthetic records exist only in this test file; they are never loaded by the app.
function dataset(count = 65, group = 'DEV', marker = 'test-only') {
  return {
    error: '', isSuccess: true,
    data: {
      dataset_group_id: group, total_records: count,
      records: Array.from({ length: count }, (_, index) => ({
        conversation_id: `synthetic-test-${index + 1}`, channel: 'agent chat', language: 'RU', audio_url: null,
        transcript: [{ timestamp: '00:00', speaker: 'client', text: marker }],
      })),
    },
  };
}

async function temporaryCache(t) {
  // Workspace-local temp directories support atomic rename in the Windows execution environment.
  const root = resolve('tmp');
  await mkdir(root, { recursive: true });
  const directory = resolve(await mkdtemp(join(root, 'freedom-organizer-tests-')));
  t.after(async () => {
    // Verify the absolute deletion target is exactly our own newly-created temp directory.
    assert.equal(dirname(directory), root);
    assert.match(basename(directory), /^freedom-organizer-tests-[a-zA-Z0-9]+$/);
    await rm(directory, { recursive: true, force: true });
  });
  return directory;
}

async function request(middleware, { method = 'GET', url = '/api/organizer/dataset', headers = {} } = {}) {
  const response = { status: 200, headers: {}, body: undefined, nextCalled: false };
  const res = {
    statusCode: 200,
    setHeader(name, value) { response.headers[name.toLowerCase()] = value; },
    end(body) { response.status = this.statusCode; response.body = body ? JSON.parse(body) : undefined; },
  };
  await middleware({ method, url, headers: { host: 'localhost:5173', ...headers } }, res, () => { response.nextCalled = true; });
  return response;
}

const ok = payload => ({ ok: true, text: async () => JSON.stringify(payload) });

test('DEV validation requires exactly 65 distinct records in the correct group', () => {
  const valid = dataset();
  assert.equal(validatePayload(valid), valid);
  assert.throws(() => validatePayload(dataset(64)), /invalid_dev_count/);
  assert.throws(() => validatePayload(dataset(66)), /invalid_dev_count/);
  assert.throws(() => validatePayload(dataset(65, 'TEST')), /invalid_count/);
  const wrongCount = dataset();
  wrongCount.data.total_records = 64;
  assert.throws(() => validatePayload(wrongCount), /invalid_count/);
  const duplicate = dataset();
  duplicate.data.records[64].conversation_id = duplicate.data.records[0].conversation_id;
  assert.throws(() => validatePayload(duplicate), /duplicate_ids/);
});

test('validation rejects unsuccessful and malformed envelopes', () => {
  for (const invalid of [null, {}, { isSuccess: false }, { ...dataset(), isSuccess: false }, { isSuccess: true, data: { records: [] } }]) {
    assert.throws(() => validatePayload(invalid), /invalid_payload/);
  }
});

test('GET loads the verified dataset once and then uses its private cache', async t => {
  const cacheDirectory = await temporaryCache(t);
  let calls = 0;
  const middleware = organizerMiddleware({
    organizerUrl: 'https://organizer.example/data?token=test-signature&group_id=WRONG', cacheDirectory,
    fetchImpl: async (url, options) => {
      calls += 1;
      assert.equal(url.protocol, 'https:');
      assert.equal(url.searchParams.get('group_id'), 'DEV');
      assert.equal(url.searchParams.get('token'), 'test-signature');
      assert.equal(options.redirect, 'error');
      assert.equal(options.headers.Accept, 'application/json');
      assert.ok(options.signal instanceof AbortSignal);
      return ok(dataset());
    },
  });
  const live = await request(middleware);
  assert.equal(live.status, 200);
  assert.equal(live.body.source, 'live');
  assert.equal(live.body.payload.data.records.length, 65);
  assert.ok(Number.isFinite(Date.parse(live.body.fetchedAt)));
  assert.equal(live.headers['cache-control'], 'no-store');
  assert.equal(live.headers['x-content-type-options'], 'nosniff');
  assert.match(live.headers['content-type'], /application\/json/);
  const cached = await request(middleware);
  assert.equal(cached.status, 200);
  assert.equal(cached.body.source, 'cache');
  assert.equal(cached.body.fetchedAt, live.body.fetchedAt);
  assert.deepEqual(cached.body.payload, live.body.payload);
  assert.equal(calls, 1);
  const onDisk = JSON.parse(await readFile(join(cacheDirectory, 'organizer-DEV.json'), 'utf8'));
  assert.deepEqual(onDisk.payload, live.body.payload);
  assert.ok(!JSON.stringify(onDisk).includes('test-signature'));
});

test('POST refreshes a valid cache and the next GET sees the refreshed snapshot', async t => {
  const cacheDirectory = await temporaryCache(t);
  let calls = 0;
  const middleware = organizerMiddleware({ organizerUrl: 'https://organizer.example/data', cacheDirectory, fetchImpl: async () => ok(dataset(65, 'DEV', `version-${++calls}`)) });
  await request(middleware);
  const refreshed = await request(middleware, { method: 'POST' });
  assert.equal(refreshed.status, 200);
  assert.equal(refreshed.body.source, 'live');
  assert.equal(refreshed.body.payload.data.records[0].transcript[0].text, 'version-2');
  const cached = await request(middleware);
  assert.equal(cached.body.source, 'cache');
  assert.equal(cached.body.payload.data.records[0].transcript[0].text, 'version-2');
  assert.equal(calls, 2);
});

test('concurrent refresh requests share one upstream fetch', async t => {
  const cacheDirectory = await temporaryCache(t);
  let calls = 0;
  let release;
  const gate = new Promise(resolveGate => { release = resolveGate; });
  const middleware = organizerMiddleware({ organizerUrl: 'https://organizer.example/data', cacheDirectory, fetchImpl: async () => { calls += 1; await gate; return ok(dataset()); } });
  const first = request(middleware, { method: 'POST' });
  const second = request(middleware, { method: 'POST' });
  assert.equal(calls, 1);
  release();
  const results = await Promise.all([first, second]);
  assert.deepEqual(results.map(result => result.status), [200, 200]);
  assert.equal(results[0].body.fetchedAt, results[1].body.fetchedAt);
});

test('missing configuration produces an actionable 503 without invoking upstream', async t => {
  const cacheDirectory = await temporaryCache(t);
  let called = false;
  const middleware = organizerMiddleware({ cacheDirectory, fetchImpl: async () => { called = true; throw new Error('must-not-fetch'); } });
  const response = await request(middleware);
  assert.equal(response.status, 503);
  assert.match(response.body.error, /ORGANIZER_DEV_URL/);
  assert.equal(called, false);
});

test('non-HTTPS configuration is rejected before fetching', async t => {
  const cacheDirectory = await temporaryCache(t);
  const middleware = organizerMiddleware({ organizerUrl: 'http://organizer.example/data?token=SECRET', cacheDirectory, fetchImpl: async () => assert.fail('HTTP upstream must not be used') });
  const response = await request(middleware, { method: 'POST' });
  assert.equal(response.status, 503);
  assert.ok(!JSON.stringify(response.body).includes('SECRET'));
});

test('upstream exceptions, bodies and malformed URLs never leak secrets', async t => {
  const cacheDirectory = await temporaryCache(t);
  const secret = 'PRIVATE-SIGNED-TOKEN';
  const cases = [
    { organizerUrl: `https://organizer.example/data?token=${secret}`, fetchImpl: async () => { throw new Error(`Request failed: https://organizer.example/data?token=${secret}`); } },
    { organizerUrl: `https://organizer.example/data?token=${secret}`, fetchImpl: async () => ({ ok: false, text: async () => secret }) },
    { organizerUrl: `https://organizer.example/data?token=${secret}`, fetchImpl: async () => ({ ok: true, text: async () => `invalid JSON ${secret}` }) },
    { organizerUrl: `invalid-${secret}`, fetchImpl: async () => assert.fail('Invalid URL must not reach fetch') },
  ];
  for (const options of cases) {
    const response = await request(organizerMiddleware({ ...options, cacheDirectory }), { method: 'POST' });
    assert.equal(response.status, 502);
    assert.ok(response.body.error);
    assert.ok(!JSON.stringify(response.body).includes(secret));
    assert.ok(!JSON.stringify(response.body).includes('organizer.example'));
  }
});

test('failed refresh preserves the prior valid snapshot and resets the in-flight state', async t => {
  const cacheDirectory = await temporaryCache(t);
  let calls = 0;
  const middleware = organizerMiddleware({ organizerUrl: 'https://organizer.example/data', cacheDirectory, fetchImpl: async () => {
    calls += 1;
    if (calls === 2) return ok(dataset(64));
    return ok(dataset(65, 'DEV', `version-${calls}`));
  } });
  await request(middleware);
  const before = await readFile(join(cacheDirectory, 'organizer-DEV.json'), 'utf8');
  const failed = await request(middleware, { method: 'POST' });
  assert.equal(failed.status, 502);
  assert.equal(await readFile(join(cacheDirectory, 'organizer-DEV.json'), 'utf8'), before);
  const cached = await request(middleware);
  assert.equal(cached.body.source, 'cache');
  assert.equal(cached.body.payload.data.records[0].transcript[0].text, 'version-1');
  assert.equal(calls, 2);
  const retried = await request(middleware, { method: 'POST' });
  assert.equal(retried.status, 200);
  assert.equal(retried.body.payload.data.records[0].transcript[0].text, 'version-3');
});

test('damaged or invalidly dated cache is refetched', async t => {
  const cacheDirectory = await temporaryCache(t);
  let calls = 0;
  const middleware = organizerMiddleware({ organizerUrl: 'https://organizer.example/data', cacheDirectory, fetchImpl: async () => { calls += 1; return ok(dataset()); } });
  for (const corrupt of ['{broken', JSON.stringify({ payload: dataset(), fetchedAt: 'invalid-date' }), JSON.stringify({ payload: dataset(64), fetchedAt: new Date().toISOString() })]) {
    await writeFile(join(cacheDirectory, 'organizer-DEV.json'), corrupt);
    const response = await request(middleware);
    assert.equal(response.status, 200);
    assert.equal(response.body.source, 'live');
  }
  assert.equal(calls, 3);
});

test('oversized upstream responses fail safely', async t => {
  const cacheDirectory = await temporaryCache(t);
  const middleware = organizerMiddleware({ organizerUrl: 'https://organizer.example/data', cacheDirectory, fetchImpl: async () => ({ ok: true, text: async () => 'x'.repeat(5 * 1024 * 1024 + 1) }) });
  assert.equal((await request(middleware, { method: 'POST' })).status, 502);
});

test('cross-host, cross-port and malformed origins are rejected before upstream access', async t => {
  const cacheDirectory = await temporaryCache(t);
  let calls = 0;
  const middleware = organizerMiddleware({ organizerUrl: 'https://organizer.example/data', cacheDirectory, fetchImpl: async () => { calls += 1; return ok(dataset()); } });
  for (const origin of ['https://attacker.example', 'http://localhost:9999', 'null', 'not-a-url']) {
    const response = await request(middleware, { method: 'POST', headers: { origin } });
    assert.equal(response.status, 403);
    assert.match(response.body.error, /Источник запроса/);
  }
  assert.equal(calls, 0);
  assert.equal((await request(middleware, { headers: { origin: 'http://localhost:5173' } })).status, 200);
  assert.equal(calls, 1);
});

test('unsupported methods are refused and unrelated routes are delegated', async t => {
  const cacheDirectory = await temporaryCache(t);
  const middleware = organizerMiddleware({ cacheDirectory, fetchImpl: async () => assert.fail('No upstream access expected') });
  const refused = await request(middleware, { method: 'DELETE' });
  assert.equal(refused.status, 405);
  assert.equal(refused.headers.allow, 'GET, POST');
  const unrelated = await request(middleware, { url: '/api/unrelated' });
  assert.equal(unrelated.nextCalled, true);
  assert.equal(unrelated.body, undefined);
});
