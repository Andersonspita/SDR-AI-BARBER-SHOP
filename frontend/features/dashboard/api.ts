import type { ImportResult, Interaction, Lead, Metrics, ScrapeParams, ScrapeResult, TrialMode, WebhookResult } from "./types";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

/** Registra quem trata sessão expirada/ausente (o AuthGate volta para a tela de login). */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

/** Erro da API com status HTTP (0 = API inacessível). */
export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI devolve `detail` como string ou como lista de erros de validação. */
function extractDetail(body: unknown): string | null {
  if (!body || typeof body !== "object" || !("detail" in body)) return null;
  const { detail } = body as { detail: unknown };
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : ""))
      .filter(Boolean)
      .join("; ");
  }
  return null;
}

type RequestOptions = RequestInit & { skipAuthRedirect?: boolean };

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { skipAuthRedirect = false, ...init } = options;
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init.headers },
      cache: "no-store",
      credentials: "include", // cookie de sessão httpOnly emitido pela API
    });
  } catch {
    throw new ApiError(`Não consegui falar com a API em ${API_URL}. Ela está rodando?`, 0);
  }

  const body: unknown = await response.json().catch(() => null);
  if (response.status === 401 && !skipAuthRedirect) onUnauthorized?.();
  if (!response.ok) {
    throw new ApiError(extractDetail(body) ?? `Erro ${response.status} na API`, response.status);
  }
  return body as T;
}

const post = <T>(path: string, payload?: unknown) =>
  request<T>(path, { method: "POST", body: payload === undefined ? undefined : JSON.stringify(payload) });

/** Cliente tipado dos endpoints do SDR-AI. */
export const sdrApi = {
  login: (email: string, password: string) =>
    request<{ email: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
      skipAuthRedirect: true,
    }),
  logout: () => post<{ status: string }>("/api/auth/logout"),
  me: () => request<{ email: string }>("/api/auth/me", { skipAuthRedirect: true }),
  listLeads: () => request<{ count: number; leads: Lead[] }>("/api/leads"),
  scrapeLeads: (params: ScrapeParams) => post<ScrapeResult>("/api/leads/scrape", params),
  /** Andamento de uma busca no Apify (a mesma UF/cidade/bairro do pedido). */
  scrapeProgress: (runId: string, params: ScrapeParams) => {
    const query = new URLSearchParams({ state: params.state, city: params.city });
    if (params.neighborhood) query.set("neighborhood", params.neighborhood);
    return request<ScrapeResult>(`/api/leads/scrape/${encodeURIComponent(runId)}?${query.toString()}`);
  },
  importLeads: (csv: string, defaults: { default_state?: string; default_city?: string }) =>
    post<ImportResult>("/api/leads/import", { csv, ...defaults }),
  importTemplate: () => request<{ filename: string; csv: string }>("/api/leads/import/template"),
  sendD0: (leadId: string) =>
    post<{ message: string; lead: Lead }>(`/api/leads/${encodeURIComponent(leadId)}/send-d0`),
  getInteractions: (leadId: string) =>
    request<{ interactions: Interaction[] }>(`/api/leads/${encodeURIComponent(leadId)}/interactions`),
  getMetrics: () => request<Metrics>("/api/metrics"),
  /** Libera o teste grátis no barber-shop e manda o link para o dono no WhatsApp. */
  activateTrial: (leadId: string, email?: string) =>
    post<{ trial: { url: string; created: boolean }; lead: Lead }>(
      `/api/leads/${encodeURIComponent(leadId)}/activate-trial`,
      email ? { email } : {},
    ),
  setTrialMode: (trialMode: TrialMode) =>
    post<{ trial_mode: TrialMode }>("/api/settings/trial-mode", { trial_mode: trialMode }),
  /** Simula uma mensagem do barbeiro (só com WHATSAPP_PROVIDER=MOCK; a API recusa com WhatsApp real). */
  simulateInbound: (phone: string, message: string) => post<WebhookResult>("/api/simulator/inbound", { phone, message }),
};

/** Mensagem amigável para qualquer erro capturado. */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Erro inesperado";
}
