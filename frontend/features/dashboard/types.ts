/** Contratos espelhando `backend/app/models.py` e as respostas de `backend/app/main.py`. */

export type LeadStatus =
  | "RAW"
  | "ENRICHED"
  | "CONTACTED_D1"
  | "FOLLOWUP_SENT"
  | "INTERESTED"
  | "OBJECTION_RAISED"
  | "TRIAL_STARTED"
  | "CONVERTED"
  | "DO_NOT_CONTACT";

export type TechMaturity = "MANUAL_WHATSAPP" | "COMPETITOR" | "UNKNOWN";

export type TrialMode = "STAND_BY" | "AUTO_LINK";

/** SYNTHETIC = gerado localmente (telefone inventado): a API bloqueia envio real para ele. */
export type LeadSource = "SYNTHETIC" | "GOOGLE_PLACES" | "APIFY_GOOGLE_MAPS" | "CSV_IMPORT";

/** Fonte usada pelo botão de busca (definida pelas chaves configuradas no servidor). */
export type SearchSource = "GOOGLE_PLACES" | "APIFY" | "SYNTHETIC";

export type CadenceStep = "D0" | "D2" | "D5";

/** Teste grátis no barber-shop, atualizado pelos webhooks de lá. */
export type TrialStatus = "PROVISIONED" | "ACTIVATED" | "EXPIRING" | "EXPIRED" | "CONVERTED";

/** Assinatura no barber-shop (espelho dos webhooks de lá). */
/** BLOCKED = agendamento online pausado; SUSPENDED e EXPIRED vêm da cobrança antiga do barber-shop. */
export type SubscriptionStatus = "TRIAL" | "ACTIVE" | "PAST_DUE" | "BLOCKED" | "EXEMPT" | "SUSPENDED" | "EXPIRED";

export type ObjectionType = "SEM_TEMPO" | "CLIENTES_VELHOS" | "CADERNO" | "CONCORRENTE";

export type Intent = "UNSUBSCRIBE" | "OBJECTION_RAISED" | "INTERESTED" | "NEUTRAL" | "BLOCKED";

export interface RecommendedPlan {
  plan_name: string;
  price_range: string;
  monthly_anchor: string;
  pitch_focus: string;
}

export interface Lead {
  id: string;
  shop_name: string;
  phone: string;
  city: string;
  state: string;
  neighborhood: string | null;
  owner_name: string | null;
  instagram_handle: string | null;
  bio_url: string | null;
  competitor_name: string | null;
  estimated_barbers: number;
  current_system: TechMaturity;
  priority: string;
  status: LeadStatus;
  slug: string;
  preview_url: string;
  initials: string;
  recommended_plan: RecommendedPlan;
  langgraph_node: string;
  last_objection_type: ObjectionType | null;
  source: LeadSource;
  is_synthetic: boolean;
  cadence_step: CadenceStep | null;
  next_touch_at: string | null;
  email: string | null;
  trial_shop_id: string | null;
  trial_status: TrialStatus | null;
  trial_ends_at: string | null;
  subscription_status: SubscriptionStatus | null;
  plan_code: string | null;
  invoice_url: string | null;
  subscribe_url: string | null;
  suspends_at: string | null;
  /** O dono pediu para pagar e não há fatura: gerar a cobrança na plataforma. */
  needs_billing_help: boolean;
  created_at: string;
}

export interface Interaction {
  id: string;
  lead_id: string;
  direction: "INBOUND" | "OUTBOUND";
  message_text: string;
  channel: string;
  intent_detected: Intent | null;
  langgraph_node: string | null;
  sent_at: string;
}

export interface Metrics {
  total_leads: number;
  contacted_leads: number;
  replied_leads: number;
  response_rate_pct: number;
  manual_whatsapp_count: number;
  competitor_count: number;
  trial_ready_count: number;
  objection_breakdown: Partial<Record<ObjectionType, number>>;
  top_objection: string;
  trial_mode: TrialMode;
  daily_sent_count: number;
  daily_limit: number;
  whatsapp_provider: string;
  barbershop_integration: boolean;
  converted_count: number;
  active_trials_count: number;
  billing_attention_count: number;
  lead_source: SearchSource;
}

export interface ScrapeParams {
  state: string;
  city: string;
  neighborhood?: string;
}

/** Busca concluída (Google/fictícia na hora; Apify ao terminar) ou ainda rodando no Apify. */
export type ScrapeResult =
  | { status: "done"; count: number; new: number; leads: Lead[] }
  | { status: "running"; run_id: string };

export interface ImportResult {
  imported: number;
  already_in_funnel: number;
  duplicates_in_file: number;
  rejected: { line: number; reason: string }[];
  leads: Lead[];
}

export type WebhookResult =
  | {
      status: "processed";
      intent: Intent;
      reply: string | null;
      objection_types: ObjectionType[];
      langgraph_node: string;
      lead: Lead;
    }
  | { status: "ignored"; reason: string };
