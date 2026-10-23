"use client";

import { useRef, useState, type FormEvent } from "react";
import { errorMessage, sdrApi } from "../api";
import type { ImportResult } from "../types";
import { BRAZILIAN_STATES } from "./ScraperHero";

const MAX_FILE_BYTES = 1_000_000;
const MAX_REJECTED_SHOWN = 15;

interface ImportLeadsPanelProps {
  onImported: (result: ImportResult) => Promise<void>;
  onClose: () => void;
}

/** Baixa o modelo de planilha (o conteúdo vem da API, para a coluna nunca divergir do importador). */
async function downloadTemplate(): Promise<void> {
  const { filename, csv } = await sdrApi.importTemplate();
  // BOM para o Excel abrir os acentos certos.
  const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * Importação de barbearias por planilha CSV: para leads de qualquer origem (Google Maps anotado à mão,
 * Instagram, indicações). Telefones já no funil não duplicam.
 */
export function ImportLeadsPanel({ onImported, onClose }: ImportLeadsPanelProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [defaultState, setDefaultState] = useState("");
  const [defaultCity, setDefaultCity] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) {
      setError("Escolha o arquivo .csv da planilha.");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setError("O arquivo passa de 1 MB. Divida a planilha em arquivos menores.");
      return;
    }
    setIsImporting(true);
    setError(null);
    setResult(null);
    try {
      const csv = await file.text();
      const imported = await sdrApi.importLeads(csv, {
        default_state: defaultState || undefined,
        default_city: defaultCity.trim() || undefined,
      });
      setResult(imported);
      await onImported(imported);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <section className="import-panel" aria-labelledby="import-title">
      <div className="import-panel-head">
        <div>
          <p className="section-eyebrow">Agente 1 • Planilha</p>
          <h2 id="import-title" className="section-heading">Importar barbearias</h2>
        </div>
        <button type="button" className="btn-secondary btn-sm" onClick={onClose}>
          Fechar
        </button>
      </div>

      <p className="import-help">
        Arquivo <strong>.csv</strong> (no Excel: Salvar como → CSV). Colunas obrigatórias: <code>nome</code> e{" "}
        <code>telefone</code> com DDD. Opcionais: <code>cidade</code>, <code>uf</code>, <code>bairro</code>,{" "}
        <code>instagram</code>, <code>site</code>, <code>dono</code>, <code>profissionais</code>.{" "}
        <button type="button" className="link-button" onClick={() => void downloadTemplate().catch((err) => setError(errorMessage(err)))}>
          Baixar modelo
        </button>
      </p>

      <form className="import-form" onSubmit={handleSubmit}>
        <label className="field">
          Planilha
          <input
            ref={fileInput}
            id="import-file"
            className="ctrl-input"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setFileName(e.target.files?.[0]?.name ?? null)}
          />
        </label>
        <label className="field">
          UF (linhas sem UF)
          <select id="import-state" className="ctrl-input" value={defaultState} onChange={(e) => setDefaultState(e.target.value)}>
            <option value="">Da planilha</option>
            {BRAZILIAN_STATES.map(([uf, name]) => (
              <option key={uf} value={uf}>
                {uf} - {name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Cidade (linhas sem cidade)
          <input
            id="import-city"
            className="ctrl-input"
            placeholder="Da planilha"
            value={defaultCity}
            onChange={(e) => setDefaultCity(e.target.value)}
          />
        </label>
        <button className="btn-primary" type="submit" disabled={isImporting || !fileName}>
          {isImporting ? "Importando..." : "Importar"}
        </button>
      </form>

      {error && (
        <div className="banner error" role="alert">
          <span>{error}</span>
        </div>
      )}

      {result && (
        <div className="import-result" role="status">
          <p>
            <strong>{result.imported}</strong> barbearia(s) nova(s) no funil
            {result.already_in_funnel > 0 && <> · {result.already_in_funnel} já estavam</>}
            {result.duplicates_in_file > 0 && <> · {result.duplicates_in_file} repetida(s) na planilha</>}
            {result.rejected.length > 0 && <> · {result.rejected.length} linha(s) recusada(s)</>}
          </p>
          {result.rejected.length > 0 && (
            <ul>
              {result.rejected.slice(0, MAX_REJECTED_SHOWN).map((problem) => (
                <li key={problem.line}>
                  Linha {problem.line}: {problem.reason}
                </li>
              ))}
              {result.rejected.length > MAX_REJECTED_SHOWN && (
                <li>… e mais {result.rejected.length - MAX_REJECTED_SHOWN}.</li>
              )}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
