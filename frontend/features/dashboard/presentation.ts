import type { Lead, LeadStatus, ObjectionType, SubscriptionStatus, TrialStatus } from "./types";

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

export const TRIAL_STATUS_LABELS: Record<TrialStatus, string> = {
  PROVISIONED: "Página criada, aguardando o dono entrar",
  ACTIVATED: "Dono já entrou no painel",
  EXPIRING: "Teste acaba em menos de 48h",
  EXPIRED: "Teste vencido (1ª mensalidade em aberto)",
  CONVERTED: "Plano ativado",
};

export const SUBSCRIPTION_LABELS: Record<SubscriptionStatus, string> = {
  TRIAL: "em teste grátis",
  ACTIVE: "em dia",
  PAST_DUE: "mensalidade vencida",
  BLOCKED: "agendamento online pausado por falta de pagamento",
  EXEMPT: "cortesia (sem mensalidade)",
  SUSPENDED: "suspensa por falta de pagamento",
  EXPIRED: "teste vencido",
};

export const PLAN_LABELS: Record<string, string> = {
  SOLO: "Solo",
  SQUAD: "Squad",
  PRO: "Pro",
  EMPIRE: "Empire",
  solo: "Solo",
  essencial: "Essencial",
  profissional: "Profissional",
  premium: "Premium",
  rede: "Rede",
};

/** Linha de destaque do card: o sinal mais relevante para o próximo passo do SDR. */
export function leadSignal(lead: Lead): { text: string; tone: Tone } {
  // Pendências de cobrança passam na frente: são dinheiro parado.
  if (lead.needs_billing_help) return { text: "🧾 Dono quer pagar: ligar pagamento online", tone: "danger" };
  if (lead.subscription_status === "BLOCKED") return { text: "⛔ Agendamento online pausado", tone: "danger" };
  if (lead.subscription_status === "SUSPENDED") return { text: "⛔ Suspensa por falta de pagamento", tone: "danger" };
  if (lead.subscription_status === "PAST_DUE") return { text: "💸 Mensalidade vencida", tone: "danger" };
  switch (lead.status) {
    case "DO_NOT_CONTACT":
      return { text: "⛔ Pediu para não ser contatado", tone: "muted" };
    case "TRIAL_STARTED":
      if (lead.trial_status === "EXPIRED") return { text: "⌛ Teste vencido", tone: "danger" };
      if (lead.trial_status === "EXPIRING") return { text: "⏳ Teste acaba em 48h", tone: "brand" };
      if (lead.trial_status === "ACTIVATED") return { text: "✅ Dono já entrou no painel", tone: "success" };
      return { text: "✅ Teste liberado, aguardando o dono entrar", tone: "success" };
    case "CONVERTED":
      return { text: "🏆 Cliente convertido", tone: "success" };
    case "INTERESTED":
      return lead.email
        ? { text: "⭐ Pronto para liberar o teste", tone: "success" }
        : { text: "✉️ Aguardando o e-mail do dono", tone: "brand" };
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

/** Lead pronto para um humano liberar o teste pelo painel (Stand-by ou falha no automático). */
export function canActivateTrial(lead: Lead): boolean {
  return !lead.trial_shop_id && lead.status !== "DO_NOT_CONTACT" && lead.status !== "CONVERTED";
}

/** Remove o protocolo para exibir a URL da vitrine como no navegador. */
export function displayUrl(url: string): string {
  return url.replace(/^https?:\/\//, "");
}
