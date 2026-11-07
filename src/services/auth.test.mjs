import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
const nativeFetch = globalThis.fetch;

async function moduleUrl(file, replacements = {}) {
  let source = await readFile(new URL(file, import.meta.url), "utf8");
  for (const [path, replacement] of Object.entries(replacements)) source = source.replaceAll(`"${path}"`, JSON.stringify(replacement));
  return `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText).toString("base64")}`;
}
const mockSdk = `data:text/javascript,${encodeURIComponent(`
  export const BrowserCacheLocation = {MemoryStorage: 'memoryStorage'};
  export class PublicClientApplication {
    constructor(config) { globalThis.__authTest.config = config; }
    async initialize() { globalThis.__authTest.initialized = true; }
    async loginPopup(request) { globalThis.__authTest.login = request; return {account: {id: 'test-user'}}; }
    setActiveAccount(account) { this.account = account; }
    getActiveAccount() { return this.account; }
    async acquireTokenSilent(request) {
      globalThis.__authTest.tokenRequest = request;
      if (globalThis.__authTest.fail) throw new Error('secret upstream error');
      return {accessToken: 'offline-test-access-token'};
    }
    async logoutPopup() {}
  }
`)}`;
const auth = await import(await moduleUrl("./auth.ts", { "@azure/msal-browser": mockSdk }));
const mediaAuth = `data:text/javascript,${encodeURIComponent("export async function getEnterpriseSession(){return globalThis.__mediaSession}")}`;
const media = await import(await moduleUrl("./authMedia.ts", { "./auth": mediaAuth }));
const configuration = {
  mode: "entra", authenticationRequired: true,
  authority: "https://login.microsoftonline.com/11111111-1111-4111-8111-111111111111",
  clientId: "33333333-3333-4333-8333-333333333333",
  scopes: ["api://22222222-2222-4222-8222-222222222222/access_as_user"],
};

function setup(t, config = configuration) {
  const prior = { fetch: globalThis.fetch, location: globalThis.location, window: globalThis.window };
  const calls = [];
  globalThis.location = { origin: "https://quality.example", reload() {} };
  globalThis.window = new EventTarget();
  globalThis.__authTest = {};
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return Response.json(String(url) === "/api/auth/config" ? config : {});
  };
  t.after(() => { Object.assign(globalThis, prior); delete globalThis.__authTest; delete globalThis.__mediaSession; });
  return calls;
}

test("local mode needs no login and strips caller-supplied bearer", async (t) => {
  const calls = setup(t, { mode: "local", authenticationRequired: false });
  const session = await auth.createEnterpriseSession();
  await session.fetch("/api/state", { headers: { Authorization: "Bearer browser-forgery" } });
  assert.equal(session.mode, "local");
  assert.equal(globalThis.__authTest.initialized, undefined);
  assert.equal(calls[1].options.headers.has("Authorization"), false);
});

test("real-flow adapter acquires resource access token and restricts request transport", async (t) => {
  const calls = setup(t);
  const session = await auth.createEnterpriseSession();
  await assert.rejects(session.fetch("/api/state"), /Войдите/);
  await session.signIn();
  await session.fetch("/api/export", { headers: { Authorization: "Bearer forged" }, redirect: "follow", credentials: "include" });
  assert.equal(globalThis.__authTest.config.cache.cacheLocation, "memoryStorage");
  assert.equal(globalThis.__authTest.config.auth.redirectUri, "https://quality.example/auth-redirect.html");
  assert.deepEqual(globalThis.__authTest.tokenRequest.scopes, configuration.scopes);
  const request = calls.at(-1);
  assert.equal(request.options.headers.get("Authorization"), "Bearer offline-test-access-token");
  assert.equal(request.options.redirect, "error");
  assert.equal(request.options.credentials, "omit");
  const count = calls.length;
  await assert.rejects(session.fetch("https://evil.example/api/state"));
  assert.equal(calls.length, count);
});

test("invalid configuration never falls back to anonymous and failed tokens never send requests", async (t) => {
  const calls = setup(t, { mode: "disabled" });
  await assert.rejects(auth.createEnterpriseSession(), /не настроен/);
  globalThis.fetch = async () => Response.json(configuration);
  const session = await auth.createEnterpriseSession();
  await session.signIn();
  globalThis.__authTest.fail = true;
  let expired = false;
  window.addEventListener("cqa-session-expired", () => { expired = true; });
  await assert.rejects(session.fetch("/api/import", { method: "POST" }), /Сеанс завершён/);
  assert.equal(expired, true);
  assert.equal(calls.length, 1);
});

test("audio local mode preserves original source without a request", async (t) => {
  setup(t);
  globalThis.__mediaSession = { mode: "local", fetch() { assert.fail("No local blob request"); } };
  const result = await media.loadAuthorizedAudio("https://audio.example/local.mp3");
  assert.equal(result.url, "https://audio.example/local.mp3");
  result.dispose();
});

test("enterprise audio rejects external origins and bad MIME, yields disposable blob for valid stream", async (t) => {
  setup(t);
  let calls = 0;
  globalThis.__mediaSession = { mode: "entra", async fetch() { calls++; return new Response("sample", { headers: { "content-type": "audio/mpeg" } }); } };
  await assert.rejects(media.loadAuthorizedAudio("https://evil.example/api/conversations/c1/audio"));
  assert.equal(calls, 0);
  const result = await media.loadAuthorizedAudio("/api/conversations/c1/audio");
  assert.match(result.url, /^blob:/);
  assert.equal(await (await nativeFetch(result.url)).text(), "sample");
  result.dispose();
  await assert.rejects(nativeFetch(result.url));
  globalThis.__mediaSession.fetch = async () => new Response("html", { headers: { "content-type": "text/html" } });
  await assert.rejects(media.loadAuthorizedAudio("/api/conversations/c1/audio"), /аудиозапись/);
});

test("audio enforces stream cap even when Content-Length is absent", async (t) => {
  setup(t);
  let cancelled = false;
  globalThis.__mediaSession = { mode: "entra", async fetch() {
    return new Response(new ReadableStream({
      start(controller) { controller.enqueue(new Uint8Array(17 * 1024 * 1024)); controller.enqueue(new Uint8Array(17 * 1024 * 1024)); },
      cancel() { cancelled = true; },
    }), { headers: { "content-type": "audio/mpeg" } });
  } };
  await assert.rejects(media.loadAuthorizedAudio("/api/conversations/c1/audio"), /слишком большая/);
  assert.equal(cancelled, true);
});
