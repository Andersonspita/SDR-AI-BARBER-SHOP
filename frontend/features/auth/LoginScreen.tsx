"use client";

import { useState, type FormEvent } from "react";

interface LoginScreenProps {
  notice: string | null;
  onLogin: (email: string, password: string) => Promise<string | null>;
}

/** Tela de login do painel (admin único configurado no servidor). */
export function LoginScreen({ notice, onLogin }: LoginScreenProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSubmitting(true);
    setError(await onLogin(email.trim(), password));
    setIsSubmitting(false);
  };

  return (
    <main className="login-shell">
      <div className="hero-glow" />
      <form className="login-card" onSubmit={handleSubmit} aria-labelledby="login-title">
        <div className="gb-logo">
          <span className="gb-avatar-mark" aria-hidden>
            GB
          </span>
          <span className="gb-brand-name">
            Gerente Barber <small>• SDR-AI</small>
          </span>
        </div>
        <h1 id="login-title" className="section-heading" style={{ marginTop: 20 }}>
          Entrar no painel
        </h1>
        {notice && !error && <p className="banner info" role="status">{notice}</p>}
        {error && <p className="banner error" role="alert">{error}</p>}

        <label className="field">
          <span>E-mail</span>
          <input
            className="ctrl-input"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span>Senha</span>
          <input
            className="ctrl-input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button className="btn-primary" type="submit" disabled={isSubmitting} style={{ width: "100%", marginTop: 8 }}>
          {isSubmitting ? "Entrando..." : "Entrar"}
        </button>
      </form>
    </main>
  );
}
