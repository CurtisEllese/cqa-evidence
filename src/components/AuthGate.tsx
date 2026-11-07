import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Button } from "./ui";
import { getEnterpriseSession } from "../services/auth";
import type { EnterpriseSession } from "../services/auth";
import { configureApiTransport } from "../services/api";
import "./AuthGate.css";

const AccessContext = createContext<EnterpriseSession | null>(null);

export function SignOutButton() {
  const session = useContext(AccessContext);
  const [busy, setBusy] = useState(false);
  if (!session || session.mode === "local") return null;
  return <Button disabled={busy} onClick={() => { setBusy(true); void session.signOut(); }}>Выйти</Button>;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<EnterpriseSession | null>(null);
  const [status, setStatus] = useState<"loading" | "login" | "ready" | "blocked" | "expired" | "error">("loading");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    getEnterpriseSession().then((value) => {
      if (cancelled) return;
      configureApiTransport(value.fetch);
      setSession(value);
      setStatus(value.mode === "local" ? "ready" : "login");
    }).catch((reason: unknown) => {
      if (!cancelled) {
        setStatus("error");
        setError(reason instanceof Error ? reason.message : "Не удалось проверить доступ.");
      }
    });
    const expired = () => setStatus("expired");
    window.addEventListener("cqa-session-expired", expired);
    return () => { cancelled = true; window.removeEventListener("cqa-session-expired", expired); };
  }, []);

  async function signIn() {
    if (!session || busy) return;
    setBusy(true); setError("");
    try {
      await session.signIn();
      const response = await session.fetch("/api/auth/me");
      if (!response.ok) throw new Error("Для этой учётной записи доступ не предоставлен. Обратитесь к администратору.");
      const identity = await response.json() as { access?: { workspace?: boolean } };
      setStatus(identity.access?.workspace === true ? "ready" : "blocked");
    } catch {
      setError("Не удалось войти. Повторите вход или обратитесь к администратору.");
    } finally { setBusy(false); }
  }

  if (status === "ready") return <AccessContext value={session}>{children}</AccessContext>;
  return <main className="auth-page">
    <section className="auth-card" aria-labelledby="auth-heading" aria-busy={status === "loading" || busy}>
      <img src="/brand/fbroker-logo.svg" alt="Freedom" width="140" />
      <h1 id="auth-heading">{status === "blocked" ? "Доступ к диалогам ещё не открыт" : status === "expired" ? "Сеанс завершён" : "Freedom Quality"}</h1>
      <p>{status === "loading" ? "Проверяем доступ…" : status === "blocked"
        ? "Администратору нужно связать вашу учётную запись с диалогами. Пока данные недоступны."
        : status === "expired" ? "Войдите снова, чтобы продолжить работу." : "Войдите с рабочей учётной записью."}</p>
      {error && <p role="alert" className="auth-error">{error}</p>}
      {status === "login" && <Button variant="primary" disabled={busy} onClick={() => void signIn()}>{busy ? "Ожидаем вход…" : "Войти через Microsoft"}</Button>}
      {(status === "error" || status === "expired") && <Button variant="primary" onClick={() => location.reload()}>Продолжить</Button>}
      {status === "blocked" && session && <Button onClick={() => void session.signOut()}>Выйти</Button>}
    </section>
  </main>;
}
