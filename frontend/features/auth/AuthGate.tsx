"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ApiError, errorMessage, sdrApi, setUnauthorizedHandler } from "@/features/dashboard/api";
import { LoginScreen } from "./LoginScreen";

export interface Session {
  email: string;
  logout: () => Promise<void>;
}

type SessionState =
  | { status: "checking" }
  | { status: "anonymous"; notice: string | null }
  | { status: "offline"; error: string }
  | { status: "authenticated"; email: string };

interface AuthGateProps {
  children: (session: Session) => ReactNode;
}

/**
 * Só renderiza o painel com sessão válida. Qualquer 401 da API (sessão expirada,
 * cookie apagado) volta para a tela de login.
 */
export function AuthGate({ children }: AuthGateProps) {
  const [state, setState] = useState<SessionState>({ status: "checking" });

  const checkSession = useCallback(async () => {
    setState({ status: "checking" });
    try {
      const { email } = await sdrApi.me();
      setState({ status: "authenticated", email });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setState({ status: "anonymous", notice: null });
      else setState({ status: "offline", error: errorMessage(err) });
    }
  }, []);

  useEffect(() => {
    void checkSession();
    setUnauthorizedHandler(() => setState({ status: "anonymous", notice: "Sua sessão expirou. Entre de novo." }));
    return () => setUnauthorizedHandler(null);
  }, [checkSession]);

  /** Devolve a mensagem de erro para o formulário, ou `null` se entrou. */
  const login = async (email: string, password: string): Promise<string | null> => {
    try {
      const session = await sdrApi.login(email, password);
      setState({ status: "authenticated", email: session.email });
      return null;
    } catch (err) {
      return errorMessage(err);
    }
  };

  const logout = async () => {
    try {
      await sdrApi.logout();
    } finally {
      setState({ status: "anonymous", notice: "Você saiu do painel." });
    }
  };

  if (state.status === "checking") {
    return (
      <main className="login-shell" aria-busy="true">
        <p className="eyebrow">Verificando sessão...</p>
      </main>
    );
  }
  if (state.status === "offline") {
    return (
      <main className="login-shell">
        <div className="banner error" role="alert">
          <span>{state.error}</span>
          <button type="button" className="btn-secondary btn-sm" onClick={() => void checkSession()}>
            Tentar de novo
          </button>
        </div>
      </main>
    );
  }
  if (state.status === "anonymous") return <LoginScreen notice={state.notice} onLogin={login} />;
  return <>{children({ email: state.email, logout })}</>;
}
