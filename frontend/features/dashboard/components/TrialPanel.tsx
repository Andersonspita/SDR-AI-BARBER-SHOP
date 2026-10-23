"use client";

import { useEffect, useState, type FormEvent } from "react";
import type { Lead } from "../types";
import { TRIAL_STATUS_LABELS, canActivateTrial } from "../presentation";
import { SubscriptionSummary } from "./SubscriptionSummary";

interface TrialPanelProps {
  lead: Lead;
  isIntegrationEnabled: boolean;
  isActivating: boolean;
  /** Devolve `true` quando o teste foi liberado. */
  onActivate: (email: string) => Promise<boolean>;
}

const dateFormatter = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "America/Sao_Paulo",
});

/**
 * Teste grátis no barber-shop: situação atual (vinda dos webhooks) ou, se ainda não
 * foi liberado, o formulário para um humano liberar com o e-mail do dono.
 */
export function TrialPanel({ lead, isIntegrationEnabled, isActivating, onActivate }: TrialPanelProps) {
  const [email, setEmail] = useState(lead.email ?? "");

  useEffect(() => setEmail(lead.email ?? ""), [lead.id, lead.email]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onActivate(email.trim());
  };

  if (lead.trial_shop_id) {
    return (
      <div className="trial-panel">
        <p className="eyebrow">🚀 Teste grátis no barber-shop</p>
        <p className="trial-status">
          {lead.trial_status ? TRIAL_STATUS_LABELS[lead.trial_status] : "Página criada"}
          {lead.trial_ends_at && lead.trial_status !== "CONVERTED" && (
            <span className="tone-muted"> · até {dateFormatter.format(new Date(lead.trial_ends_at))}</span>
          )}
        </p>
        {lead.email && (
          <p className="tone-muted" style={{ fontSize: 12 }}>
            Login do dono: <strong>{lead.email}</strong>
          </p>
        )}
        <SubscriptionSummary lead={lead} />
      </div>
    );
  }

  if (!canActivateTrial(lead)) return null;

  return (
    <form className="trial-panel" onSubmit={handleSubmit}>
      <p className="eyebrow">🚀 Liberar teste grátis</p>
      {!isIntegrationEnabled ? (
        <p className="tone-muted" style={{ fontSize: 12 }}>
          Integração com o barber-shop desligada. Configure BARBERSHOP_API_URL e BARBERSHOP_PROVISIONING_KEY na API.
        </p>
      ) : (
        <div className="chat-form">
          <label className="sr-only" htmlFor="trial-email">E-mail do dono</label>
          <input
            id="trial-email"
            className="ctrl-input"
            type="email"
            placeholder="E-mail do dono (login no painel)"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <button className="btn-primary" type="submit" disabled={isActivating || !email.trim()}>
            {isActivating ? "Liberando..." : "Liberar e enviar link"}
          </button>
        </div>
      )}
    </form>
  );
}
