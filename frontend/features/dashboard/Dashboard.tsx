"use client";

import { useState } from "react";
import type { Session } from "@/features/auth";
import { errorMessage, sdrApi } from "./api";
import { useDashboardData, useLeadInteractions } from "./hooks";
import type { ImportResult, ScrapeParams, ScrapeResult, TrialMode } from "./types";
import { Header } from "./components/Header";
import { ImportLeadsPanel } from "./components/ImportLeadsPanel";
import { KanbanBoard } from "./components/KanbanBoard";
import { MetricsRow } from "./components/MetricsRow";
import { ScraperHero } from "./components/ScraperHero";
import { StorefrontPreview } from "./components/StorefrontPreview";
import { TrialPanel } from "./components/TrialPanel";
import { WhatsAppSimulator } from "./components/WhatsAppSimulator";

type Notice = { tone: "error" | "info"; text: string } | null;

const APIFY_POLL_INTERVAL_MS = 5_000;
// A execução no Apify tem teto de 5 min; um pouco de folga para a fila de lá.
const APIFY_MAX_WAIT_MS = 7 * 60_000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Espera a busca no Apify terminar, consultando o andamento a cada poucos segundos. */
async function waitForSearch(first: ScrapeResult, params: ScrapeParams): Promise<Extract<ScrapeResult, { status: "done" }>> {
  let result = first;
  const deadline = Date.now() + APIFY_MAX_WAIT_MS;
  while (result.status === "running") {
    if (Date.now() > deadline) throw new Error("A busca no Apify passou de 7 minutos. Tente de novo em instantes.");
    await sleep(APIFY_POLL_INTERVAL_MS);
    result = await sdrApi.scrapeProgress(result.run_id, params);
  }
  return result;
}

interface DashboardProps {
  session: Session;
}

/** Painel de monitoramento do SDR-AI: busca, Kanban, vitrine /slug e simulador do webhook. */
export function Dashboard({ session }: DashboardProps) {
  const { leads, metrics, isLoading, error: loadError, refresh } = useDashboardData();
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null);
  const [interactionsVersion, setInteractionsVersion] = useState(0);
  const [notice, setNotice] = useState<Notice>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [isSavingMode, setIsSavingMode] = useState(false);
  const [sendingLeadId, setSendingLeadId] = useState<string | null>(null);
  const [isSimulating, setIsSimulating] = useState(false);
  const [isActivatingTrial, setIsActivatingTrial] = useState(false);
  const [isImportOpen, setIsImportOpen] = useState(false);

  const isMockProvider = metrics?.whatsapp_provider === "MOCK";
  const blockedSyntheticCount = metrics && !isMockProvider ? leads.filter((lead) => lead.is_synthetic).length : 0;
  const selectedLead = leads.find((lead) => lead.id === selectedLeadId) ?? leads[0] ?? null;
  const { interactions, error: interactionsError } = useLeadInteractions(selectedLead?.id ?? null, interactionsVersion);

  const afterMutation = async () => {
    setInteractionsVersion((version) => version + 1);
    await refresh();
  };

  const handleSearch = async (params: ScrapeParams) => {
    setIsSearching(true);
    try {
      const started = await sdrApi.scrapeLeads(params);
      if (started.status === "running") {
        setNotice({ tone: "info", text: `Buscando barbearias em ${params.city}/${params.state} no Google Maps. Leva de 1 a 3 minutos…` });
      }
      const result = await waitForSearch(started, params);
      const repeated = result.count - result.new;
      setNotice({
        tone: "info",
        text:
          `${result.count} barbearias encontradas em ${params.city}/${params.state}` +
          (repeated > 0 ? ` (${result.new} novas, ${repeated} já estavam no funil).` : ".") +
          (result.without_whatsapp > 0 ? ` ${result.without_whatsapp} ficaram de fora por não ter WhatsApp.` : ""),
      });
      if (result.leads[0]) setSelectedLeadId(result.leads[0].id);
      await refresh();
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
    } finally {
      setIsSearching(false);
    }
  };

  const handleImported = async (result: ImportResult) => {
    if (result.leads[0]) setSelectedLeadId(result.leads[0].id);
    await refresh();
  };

  const handleSendD0 = async (leadId: string) => {
    setSendingLeadId(leadId);
    try {
      await sdrApi.sendD0(leadId);
      setSelectedLeadId(leadId);
      setNotice(null);
      await afterMutation();
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
    } finally {
      setSendingLeadId(null);
    }
  };

  const handleTrialModeChange = async (mode: TrialMode) => {
    setIsSavingMode(true);
    try {
      await sdrApi.setTrialMode(mode);
      await refresh();
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
    } finally {
      setIsSavingMode(false);
    }
  };

  /** Retorna `true` quando a mensagem foi processada (para o simulador limpar o campo). */
  const handleSimulate = async (message: string): Promise<boolean> => {
    if (!selectedLead) return false;
    setIsSimulating(true);
    try {
      const result = await sdrApi.simulateInbound(selectedLead.phone, message);
      setNotice(result.status === "ignored" ? { tone: "info", text: `Mensagem ignorada: ${result.reason}` } : null);
      await afterMutation();
      return true;
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
      return false;
    } finally {
      setIsSimulating(false);
    }
  };

  /** Liberação manual do teste grátis (Stand-by VIP ou quando o automático falhou). */
  const handleActivateTrial = async (email: string): Promise<boolean> => {
    if (!selectedLead) return false;
    setIsActivatingTrial(true);
    try {
      const result = await sdrApi.activateTrial(selectedLead.id, email);
      setNotice({ tone: "info", text: `Teste liberado: ${result.trial.url}. Link enviado no WhatsApp do dono.` });
      await afterMutation();
      return true;
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
      return false;
    } finally {
      setIsActivatingTrial(false);
    }
  };

  return (
    <>
      <Header
        trialMode={metrics?.trial_mode ?? null}
        isSaving={isSavingMode}
        onTrialModeChange={handleTrialModeChange}
        userEmail={session.email}
        onLogout={() => void session.logout()}
      />
      <ScraperHero
        isSearching={isSearching}
        searchSource={metrics?.lead_source ?? null}
        onSearch={handleSearch}
        onOpenImport={() => setIsImportOpen(true)}
      />

      <div className="container">
        {loadError && (
          <div className="banner error" role="alert">
            <span>{loadError}</span>
            <button type="button" className="btn-secondary btn-sm" onClick={() => void refresh()}>
              Tentar de novo
            </button>
          </div>
        )}
        {blockedSyntheticCount > 0 && (
          <div className="banner info" role="status">
            <span>
              {blockedSyntheticCount} lead(s) fictício(s) na base: com WhatsApp real ({metrics?.whatsapp_provider}) eles nunca
              recebem mensagem. Busque com APIFY_TOKEN ou GOOGLE_MAPS_API_KEY configurado, ou importe uma planilha.
            </span>
          </div>
        )}
        {notice && (
          <div className={`banner ${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>
            <span>{notice.text}</span>
            <button type="button" className="btn-secondary btn-sm" onClick={() => setNotice(null)}>
              Fechar
            </button>
          </div>
        )}
        {isImportOpen && <ImportLeadsPanel onImported={handleImported} onClose={() => setIsImportOpen(false)} />}
      </div>

      <MetricsRow metrics={metrics} />

      <main className="container main-grid">
        <KanbanBoard
          leads={leads}
          isLoading={isLoading}
          selectedLeadId={selectedLead?.id ?? null}
          sendingLeadId={sendingLeadId}
          onSelect={setSelectedLeadId}
          onSendD0={(leadId) => void handleSendD0(leadId)}
        />

        <aside id="vitrine" className="right-panel" aria-label="Lead selecionado">
          <div>
            <p className="section-eyebrow">Agente 5 • Vitrine Pré-Configurada + Chat WhatsApp</p>
            <h2 className="section-heading">Preview do Lead Selecionado</h2>
          </div>
          {selectedLead ? (
            <div className="preview-card">
              <StorefrontPreview lead={selectedLead} trialMode={metrics?.trial_mode ?? null} />
              <TrialPanel
                lead={selectedLead}
                isIntegrationEnabled={metrics?.barbershop_integration ?? false}
                isActivating={isActivatingTrial}
                onActivate={handleActivateTrial}
              />
              <WhatsAppSimulator
                lead={selectedLead}
                interactions={interactions}
                loadError={interactionsError}
                isSending={isSimulating}
                isSimulatorEnabled={isMockProvider}
                onSimulate={handleSimulate}
              />
            </div>
          ) : (
            <div className="empty-state">
              <strong>Selecione uma barbearia</strong>
              A vitrine /slug e a conversa do WhatsApp aparecem aqui.
            </div>
          )}
        </aside>
      </main>
    </>
  );
}
