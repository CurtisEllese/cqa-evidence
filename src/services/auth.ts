/** Real Microsoft authorization-code + PKCE flow. No secrets in the browser. */
type AccessConfig =
  | { mode: "local"; authenticationRequired: false }
  | { mode: "entra"; authenticationRequired: true; authority: string; clientId: string; scopes: string[] };

function validateConfig(value: unknown): AccessConfig {
  if (!value || typeof value !== "object") throw new Error("Не удалось прочитать настройки входа.");
  const config = value as Record<string, unknown>;
  if (config.mode === "local" && config.authenticationRequired === false) return config as AccessConfig;
  if (config.mode !== "entra" || config.authenticationRequired !== true
    || typeof config.authority !== "string" || !/^https:\/\/login\.microsoftonline\.com\/[0-9a-f-]{36}$/.test(config.authority)
    || typeof config.clientId !== "string" || !/^[0-9a-f-]{36}$/.test(config.clientId)
    || !Array.isArray(config.scopes) || config.scopes.length !== 1
    || typeof config.scopes[0] !== "string" || !/^api:\/\/[0-9a-f-]{36}\/[\w.-]+$/.test(config.scopes[0])) {
    throw new Error("Корпоративный вход пока не настроен.");
  }
  return config as AccessConfig;
}

export async function createEnterpriseSession() {
  const response = await fetch("/api/auth/config", { cache: "no-store", redirect: "error", credentials: "omit" });
  if (!response.ok) throw new Error("Не удалось получить настройки входа. Повторите попытку позже.");
  const config = validateConfig(await response.json());
  // Local users do not download the enterprise SDK.
  const sdk = config.mode === "entra" ? await import("@azure/msal-browser") : null;
  const msal = sdk && config.mode === "entra" ? new sdk.PublicClientApplication({
    auth: { clientId: config.clientId, authority: config.authority,
      redirectUri: location.origin + "/auth-redirect.html" },
    cache: { cacheLocation: sdk.BrowserCacheLocation.MemoryStorage },
  }) : null;
  if (msal) await msal.initialize();
  return {
    mode: config.mode,
    async signIn() {
      if (!msal || config.mode !== "entra") return;
      const result = await msal.loginPopup({ scopes: config.scopes });
      msal.setActiveAccount(result.account);
    },
    async signOut() {
      try {
        if (msal) await msal.logoutPopup({ account: msal.getActiveAccount(),
          postLogoutRedirectUri: location.origin + "/auth-redirect.html" });
      } finally {
        // Clear in-memory workspace caches as well as identity when accounts change.
        location.reload();
      }
    },
    async fetch(path: string, options: RequestInit = {}) {
      const target = new URL(path, location.origin);
      if (target.origin !== location.origin || target.username || target.password || !target.pathname.startsWith("/api/")) {
        throw new Error("API-запрос разрешён только этому приложению.");
      }
      const headers = new Headers(options.headers);
      headers.delete("Authorization");
      if (msal && config.mode === "entra") {
        const account = msal.getActiveAccount();
        if (!account) throw new Error("Войдите через Microsoft.");
        try {
          const result = await msal.acquireTokenSilent({ scopes: config.scopes, account });
          headers.set("Authorization", `Bearer ${result.accessToken}`);
        } catch {
          // No hidden popup, mutation retry or fallback to anonymous access.
          window.dispatchEvent(new Event("cqa-session-expired"));
          throw new Error("Сеанс завершён. Войдите снова.");
        }
      }
      const result = await fetch(target, { ...options, headers, credentials: "omit", redirect: "error", cache: "no-store" });
      if (result.redirected || (result.url && new URL(result.url).origin !== location.origin)) {
        await result.body?.cancel();
        throw new Error("Перенаправление API-запроса запрещено.");
      }
      if (config.mode === "entra" && result.status === 401) window.dispatchEvent(new Event("cqa-session-expired"));
      return result;
    },
  };
}

export type EnterpriseSession = Awaited<ReturnType<typeof createEnterpriseSession>>;
let initialization: Promise<EnterpriseSession> | undefined;
export function getEnterpriseSession() {
  return initialization ??= createEnterpriseSession().catch((error) => {
    initialization = undefined;
    throw error;
  });
}
