"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, sdrApi } from "./api";
import type { Interaction, Lead, Metrics } from "./types";

/** Intervalo de polling: o webhook real da Evolution API altera leads sem ação no painel. */
export const POLL_INTERVAL_MS = 5000;

/**
 * Carrega leads + métricas e mantém sincronizado via polling.
 * `error` reflete a última tentativa; os dados anteriores são preservados em caso de falha.
 */
export function useDashboardData(pollMs: number = POLL_INTERVAL_MS) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [leadsResponse, metricsResponse] = await Promise.all([sdrApi.listLeads(), sdrApi.getMetrics()]);
      setLeads(leadsResponse.leads);
      setMetrics(metricsResponse);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const intervalId = setInterval(() => void refresh(), pollMs);
    return () => clearInterval(intervalId);
  }, [refresh, pollMs]);

  return { leads, metrics, isLoading, error, refresh };
}

/** Histórico de conversa do lead selecionado; recarrega ao trocar de lead ou quando `version` muda. */
export function useLeadInteractions(leadId: string | null, version: number, pollMs: number = POLL_INTERVAL_MS) {
  const [interactions, setInteractions] = useState<Interaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const activeLeadId = useRef<string | null>(leadId);

  useEffect(() => {
    activeLeadId.current = leadId;
    setInteractions([]);
    setError(null);
    if (!leadId) return;

    const load = async () => {
      try {
        const response = await sdrApi.getInteractions(leadId);
        // Ignora respostas atrasadas de um lead que já não está selecionado.
        if (activeLeadId.current === leadId) {
          setInteractions(response.interactions);
          setError(null);
        }
      } catch (err) {
        if (activeLeadId.current === leadId) setError(errorMessage(err));
      }
    };

    void load();
    const intervalId = setInterval(() => void load(), pollMs);
    return () => clearInterval(intervalId);
  }, [leadId, version, pollMs]);

  return { interactions, error };
}
