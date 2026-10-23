import type { Lead } from "../types";
import { PLAN_LABELS, SUBSCRIPTION_LABELS } from "../presentation";

const dateFormatter = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "America/Sao_Paulo",
});

/** Origem do barber-shop, para apontar o operador ao /plataforma. */
function platformUrl(previewUrl: string): string | null {
  try {
    return `${new URL(previewUrl).origin}/plataforma`;
  } catch {
    return null;
  }
}

/**
 * Situação da assinatura no barber-shop (vinda dos webhooks): plano, fatura em aberto,
 * prazo de suspensão e o alerta quando o dono pediu para pagar e ninguém gerou a cobrança.
 */
export function SubscriptionSummary({ lead }: { lead: Lead }) {
  if (!lead.subscription_status && !lead.needs_billing_help) return null;
  const owes = ["PAST_DUE", "BLOCKED", "SUSPENDED"].includes(lead.subscription_status ?? "");
  const platform = platformUrl(lead.preview_url);

  return (
    <div className="subscription-summary">
      {lead.subscription_status && (
        <p className={owes ? "tone-danger" : "tone-muted"} style={{ fontSize: 12, fontWeight: 600 }}>
          Assinatura: {SUBSCRIPTION_LABELS[lead.subscription_status] ?? lead.subscription_status}
          {lead.plan_code && ` · Plano ${PLAN_LABELS[lead.plan_code] ?? lead.plan_code}`}
          {lead.suspends_at && ` · sai do ar em ${dateFormatter.format(new Date(lead.suspends_at))}`}
        </p>
      )}
      {lead.invoice_url && (
        <a href={lead.invoice_url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
          Fatura em aberto ↗
        </a>
      )}
      {lead.needs_billing_help && (
        <p className="banner error" role="status" style={{ marginTop: 6 }}>
          O dono pediu para pagar e ainda não há fatura. Gere a cobrança em{" "}
          {platform ? (
            <a href={platform} target="_blank" rel="noreferrer">
              /plataforma
            </a>
          ) : (
            "/plataforma"
          )}{" "}
          → Gerar cobrança (precisa do CPF/CNPJ). O link chega na conversa sozinho.
        </p>
      )}
    </div>
  );
}
