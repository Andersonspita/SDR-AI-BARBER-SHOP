"use client";

import { useState, type FormEvent } from "react";
import type { ScrapeParams, SearchSource } from "../types";

export const BRAZILIAN_STATES: [string, string][] = [
  ["AC", "Acre"], ["AL", "Alagoas"], ["AP", "Amapá"], ["AM", "Amazonas"], ["BA", "Bahia"], ["CE", "Ceará"],
  ["DF", "Distrito Federal"], ["ES", "Espírito Santo"], ["GO", "Goiás"], ["MA", "Maranhão"], ["MT", "Mato Grosso"],
  ["MS", "Mato Grosso do Sul"], ["MG", "Minas Gerais"], ["PA", "Pará"], ["PB", "Paraíba"], ["PR", "Paraná"],
  ["PE", "Pernambuco"], ["PI", "Piauí"], ["RJ", "Rio de Janeiro"], ["RN", "Rio Grande do Norte"],
  ["RS", "Rio Grande do Sul"], ["RO", "Rondônia"], ["RR", "Roraima"], ["SC", "Santa Catarina"], ["SP", "São Paulo"],
  ["SE", "Sergipe"], ["TO", "Tocantins"],
];

const CITY_SUGGESTIONS = ["Vitória da Conquista", "Salvador", "São Paulo", "Campinas", "Belo Horizonte", "Curitiba"];

const SOURCE_LABELS: Record<SearchSource, string> = {
  GOOGLE_PLACES: "Google Places",
  APIFY: "Google Maps via Apify (leva de 1 a 3 min)",
  SYNTHETIC: "barbearias fictícias (configure APIFY_TOKEN ou GOOGLE_MAPS_API_KEY)",
};

interface ScraperHeroProps {
  isSearching: boolean;
  searchSource: SearchSource | null;
  onSearch: (params: ScrapeParams) => Promise<void>;
  onOpenImport: () => void;
}

/** Hero com o formulário do Agente 1 (busca de barbearias por UF/Cidade/Bairro). */
export function ScraperHero({ isSearching, searchSource, onSearch, onOpenImport }: ScraperHeroProps) {
  const [state, setState] = useState("BA");
  const [city, setCity] = useState("Vitória da Conquista");
  const [neighborhood, setNeighborhood] = useState("");

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedNeighborhood = neighborhood.trim();
    await onSearch({ state, city: city.trim(), neighborhood: trimmedNeighborhood || undefined });
  };

  return (
    <section className="hero-banner">
      <div className="hero-glow" />
      <div className="container hero-container">
        <div>
          <p className="pill-badge">✂️ Máquina Autônoma de Vendas Outbound</p>
          <h1 className="hero-title">
            Prospecção inteligente, <span className="accent">com vitrine pronta em 1 clique.</span>
          </h1>
        </div>

        <div className="scraper-column">
          <form className="scraper-box" onSubmit={handleSubmit}>
            <label className="sr-only" htmlFor="scrape-state">Estado</label>
            <select id="scrape-state" className="ctrl-input" value={state} onChange={(e) => setState(e.target.value)}>
              {BRAZILIAN_STATES.map(([uf, name]) => (
                <option key={uf} value={uf}>
                  {uf} - {name}
                </option>
              ))}
            </select>
            <label className="sr-only" htmlFor="scrape-city">Cidade</label>
            <input
              id="scrape-city"
              className="ctrl-input"
              list="city-suggestions"
              placeholder="Cidade"
              value={city}
              onChange={(e) => setCity(e.target.value)}
              required
              minLength={2}
            />
            <datalist id="city-suggestions">
              {CITY_SUGGESTIONS.map((suggestion) => (
                <option key={suggestion} value={suggestion} />
              ))}
            </datalist>
            <label className="sr-only" htmlFor="scrape-neighborhood">Bairro</label>
            <input
              id="scrape-neighborhood"
              className="ctrl-input"
              placeholder="Bairro (opcional)"
              value={neighborhood}
              onChange={(e) => setNeighborhood(e.target.value)}
              style={{ width: 150 }}
            />
            <button className="btn-primary" type="submit" disabled={isSearching}>
              {isSearching ? "Buscando..." : "Buscar Barbearias"}
            </button>
          </form>
          <p className="scraper-source">
            {searchSource && <>Fonte da busca: {SOURCE_LABELS[searchSource]} · </>}
            <button type="button" className="link-button" onClick={onOpenImport}>
              Importar planilha
            </button>
          </p>
        </div>
      </div>
    </section>
  );
}
