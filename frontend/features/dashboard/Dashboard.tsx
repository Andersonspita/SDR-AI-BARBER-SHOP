"use client";

import { useState } from "react";
import type { Session } from "@/features/auth";
import { errorMessage, sdrApi } from "./api";
import { useDashboardData, useLeadInteractions } from "./hooks";
import type { ScrapeParams, TrialMode } from "./types";
import { Header } from "./components/Header";
import { KanbanBoard } from "./components/KanbanBoard";
import { MetricsRow } from "./components/MetricsRow";
import { ScraperHero } from "./components/ScraperHero";
import { StorefrontPreview } from "./components/StorefrontPreview";
import { WhatsAppSimulator } from "./components/WhatsAppSimulator";

type Notice = { tone: "error" | "info"; text: string } | null;

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
      const result = await sdrApi.scrapeLeads(params);
      setNotice({ tone: "info", text: `${result.count} barbearias encontradas em ${params.city}/${params.state}.` });
      if (result.leads[0]) setSelectedLeadId(result.leads[0].id);
      await refresh();
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
    } finally {
      setIsSearching(false);
    }
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

  return (
    <>
      <Header
        trialMode={metrics?.trial_mode ?? null}
        isSaving={isSavingMode}
        onTrialModeChange={handleTrialModeChange}
        userEmail={session.email}
        onLogout={() => void session.logout()}
      />
      <ScraperHero isSearching={isSearching} onSearch={handleSearch} />

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
              recebem mensagem. Configure GOOGLE_MAPS_API_KEY para buscar barbearias reais.
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
