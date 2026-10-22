import type { Lead, TrialMode } from "../types";
import { displayUrl } from "../presentation";

interface StorefrontPreviewProps {
  lead: Lead;
  trialMode: TrialMode | null;
}

const BOOKING_STEPS = ["Escolha o serviço", "Escolha o barbeiro", "Confirme no Whats"];

/** Réplica em miniatura da vitrine /barbearia-elite gerada para o lead selecionado. */
export function StorefrontPreview({ lead, trialMode }: StorefrontPreviewProps) {
  const isLive = lead.status === "TRIAL_STARTED" || lead.status === "CONVERTED";
  const statusLabel = isLive ? "● No ar" : trialMode === "AUTO_LINK" ? "● Link automático" : "● Pré-configurada (Stand-by)";

  return (
    <>
      <div className="preview-card-bar">
        <span>
          🌐{" "}
          <a href={lead.preview_url} target="_blank" rel="noreferrer">
            <code>{displayUrl(lead.preview_url)}</code>
          </a>
        </span>
        <span className={isLive ? "tone-success" : "tone-brand"} style={{ fontSize: 11 }}>
          {statusLabel}
        </span>
      </div>

      <div className="mini-elite-landing">
        <div className="mini-elite-glow" />
        <div className="mini-elite-header">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="gb-avatar-mark xs" aria-hidden>
              {lead.initials}
            </span>
            <strong style={{ fontFamily: "var(--font-display)" }}>{lead.shop_name}</strong>
          </div>
          <div className="mini-elite-nav">
            <span>Serviços</span>
            <span>Equipe</span>
            <span>Onde estamos</span>
            <span className="mini-elite-cta">Agendar</span>
          </div>
        </div>

        <span className="pill-badge" style={{ position: "relative", fontSize: 9, padding: "3px 10px", marginBottom: 8 }}>
          ✂️ {lead.neighborhood ? `${lead.neighborhood}, ` : ""}
          {lead.city}
        </span>
        <h3 className="mini-elite-title">
          Seu horário na cadeira, <span className="accent">sem fila e sem espera.</span>
        </h3>
        <p className="mini-elite-sub">
          Escolha o serviço, o profissional e o horário na <strong>{lead.shop_name}</strong>. Você recebe a confirmação
          no WhatsApp.
        </p>

        <div className="mini-steps">
          {BOOKING_STEPS.map((step, index) => (
            <div key={step} className="mini-step-item">
              <span className="eyebrow" style={{ fontSize: 9 }}>
                Passo {index + 1}
              </span>
              <strong>{step}</strong>
            </div>
          ))}
        </div>
      </div>

      <div className="plan-strip">
        <span>
          Plano sugerido: <strong className="accent">{lead.recommended_plan.plan_name}</strong> (
          {lead.recommended_plan.monthly_anchor}/mês)
        </span>
        <span>Foco: {lead.recommended_plan.pitch_focus}</span>
      </div>
    </>
  );
}
