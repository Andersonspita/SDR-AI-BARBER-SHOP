import type { Lead } from "../types";
import { canSendD0, chairsLabel, leadSignal, nextTouchLabel } from "../presentation";

interface LeadCardProps {
  lead: Lead;
  isSelected: boolean;
  isSending: boolean;
  onSelect: (leadId: string) => void;
  onSendD0: (leadId: string) => void;
}

/** Card de barbearia no Kanban: sinal da bio/conversa, slug da vitrine e ação de envio D0. */
export function LeadCard({ lead, isSelected, isSending, onSelect, onSendD0 }: LeadCardProps) {
  const signal = leadSignal(lead);
  const nextTouch = nextTouchLabel(lead);
  const classes = ["shop-card", isSelected && "active", lead.status === "DO_NOT_CONTACT" && "blocked"]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      role="button"
      tabIndex={0}
      aria-pressed={isSelected}
      onClick={() => onSelect(lead.id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(lead.id);
        }
      }}
    >
      <div className="shop-card-head">
        <span className="gb-avatar-mark sm" aria-hidden>
          {lead.initials}
        </span>
        <div>
          <div className="shop-name">
            {lead.shop_name}
            {lead.is_synthetic && (
              <span className="badge" title="Gerado localmente: telefone inventado, nunca recebe WhatsApp real">
                Fictício
              </span>
            )}
          </div>
          <div className="shop-meta">
            {lead.city} • {chairsLabel(lead.estimated_barbers)}
          </div>
        </div>
      </div>
      <div className={`signal tone-${signal.tone}`}>{signal.text}</div>
      {nextTouch && <div className="signal tone-muted">⏱️ {nextTouch}</div>}
      <code className="url-slug-preview">/{lead.slug}</code>
      {canSendD0(lead) && (
        <div className="shop-card-actions">
          <button
            type="button"
            className="btn-secondary btn-sm"
            disabled={isSending}
            onClick={(event) => {
              event.stopPropagation();
              onSendD0(lead.id);
            }}
          >
            {isSending ? "Enviando..." : "Enviar D0"}
          </button>
        </div>
      )}
    </div>
  );
}
