import type { Lead, LeadStatus, ObjectionType } from "./types";

export type Tone = "danger" | "brand" | "success" | "muted";

export interface KanbanColumn {
  key: string;
  title: string;
  statuses: LeadStatus[];
  headTone?: "success" | "muted";
  emptyText: string;
}

/** Colunas do funil outbound, na ordem do Kanban. */
export const KANBAN_COLUMNS: KanbanColumn[] = [
  { key: "detected", title: "1. Detectadas", statuses: ["RAW", "ENRICHED"], emptyText: "Busque uma cidade para começar." },
  {
    key: "talking",
    title: "2. Em Conversa / Objeção",
    statuses: ["CONTACTED_D1", "FOLLOWUP_SENT", "OBJECTION_RAISED"],
    emptyText: "Envie o D0 para iniciar conversas.",
  },
  {
    key: "trial",
    title: "3. Trial / Stand-by",
    statuses: ["INTERESTED", "TRIAL_STARTED", "CONVERTED"],
    headTone: "success",
    emptyText: "Ninguém pediu teste ainda.",
  },
  {
    key: "blocked",
    title: "Não contatar",
    statuses: ["DO_NOT_CONTACT"],
    headTone: "muted",
    emptyText: "Nenhum descadastro.",
  },
];

export const OBJECTION_LABELS: Record<ObjectionType, string> = {
  CADERNO: "Já uso caderno",
  CLIENTES_VELHOS: "Clientes só usam Whats",
  SEM_TEMPO: "Sem tempo p/ cadastrar",
  CONCORRENTE: "Já usa concorrente",
};

/** Linha de destaque do card: o sinal mais relevante para o próximo passo do SDR. */
export function leadSignal(lead: Lead): { text: string; tone: Tone } {
  switch (lead.status) {
    case "DO_NOT_CONTACT":
      return { text: "⛔ Pediu para não ser contatado", tone: "muted" };
    case "TRIAL_STARTED":
      return { text: "✅ Link do teste 7 dias enviado", tone: "success" };
    case "CONVERTED":
      return { text: "🏆 Cliente convertido", tone: "success" };
    case "INTERESTED":
      return { text: "⭐ Fila VIP (Stand-by)", tone: "success" };
    case "OBJECTION_RAISED":
      return {
        text: `💬 Objeção: "${lead.last_objection_type ? OBJECTION_LABELS[lead.last_objection_type] : "não identificada"}"`,
        tone: "brand",
      };
    case "CONTACTED_D1":
    case "FOLLOWUP_SENT":
      return { text: "📨 D0 enviado, aguardando resposta", tone: "muted" };
    default:
      break;
  }
  if (lead.current_system === "MANUAL_WHATSAPP") return { text: "🔥 Bio: wa.me (Atendimento Manual)", tone: "danger" };
  if (lead.current_system === "COMPETITOR") {
    return { text: `⚔️ Bio: Usa ${lead.competitor_name ?? "concorrente"} (Migração)`, tone: "brand" };
  }
  return { text: "❔ Sem link na bio", tone: "muted" };
}

export function chairsLabel(count: number): string {
  return `${count} ${count === 1 ? "cadeira" : "cadeiras"}`;
}

export function canSendD0(lead: Lead): boolean {
  return lead.status === "RAW" || lead.status === "ENRICHED";
}

const touchFormatter = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "America/Sao_Paulo",
});

const NEXT_STEP: Record<string, string> = { D0: "D2", D2: "D5" };

/** Próximo toque automático da régua (D2/D5), ou `null` se o lead está fora dela. */
export function nextTouchLabel(lead: Lead): string | null {
  if (!lead.next_touch_at || !lead.cadence_step) return null;
  const step = NEXT_STEP[lead.cadence_step];
  return step ? `${step} automático em ${touchFormatter.format(new Date(lead.next_touch_at))}` : null;
}

/** Remove o protocolo para exibir a URL da vitrine como no navegador. */
export function displayUrl(url: string): string {
  return url.replace(/^https?:\/\//, "");
}
