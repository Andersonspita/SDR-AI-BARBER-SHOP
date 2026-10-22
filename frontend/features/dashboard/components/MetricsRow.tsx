import type { Metrics } from "../types";

interface MetricsRowProps {
  metrics: Metrics | null;
}

/** KPIs dos 5 agentes no formato dos cards "Passo 1/2/3" da vitrine. */
export function MetricsRow({ metrics }: MetricsRowProps) {
  if (!metrics) {
    return (
      <section className="container steps-row" aria-busy="true" aria-label="Carregando métricas">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="skeleton" style={{ height: 92 }} />
        ))}
      </section>
    );
  }

  const cards = [
    {
      icon: "✂️",
      eyebrow: "Agente 1 • Scraper",
      metric: `${metrics.manual_whatsapp_count} Sem Sistema`,
      desc: `${metrics.competitor_count} usam concorrente • ${metrics.total_leads} leads no total.`,
    },
    {
      icon: "💬",
      eyebrow: "Agente 2 & 3 • WhatsApp",
      metric: `${metrics.response_rate_pct.toLocaleString("pt-BR")}% Resposta`,
      desc: `Anti-ban: ${metrics.daily_sent_count}/${metrics.daily_limit} envios hoje.`,
    },
    {
      icon: "🛡️",
      eyebrow: "Agente 4 • Objeções",
      metric: metrics.top_objection,
      desc: "Objeção mais frequente rebatida pela base RAG.",
    },
    {
      icon: "🚀",
      eyebrow: "Agente 5 • Trial Slug",
      metric: `${metrics.converted_count} Convertidas`,
      desc: `${metrics.active_trials_count} em teste grátis agora · ${
        metrics.trial_mode === "AUTO_LINK" ? "liberação automática" : "liberação manual (Stand-by)"
      }.`,
    },
  ];

  return (
    <section className="container steps-row" aria-label="Métricas">
      {cards.map((card) => (
        <div key={card.eyebrow} className="step-card">
          <span className="step-icon" aria-hidden>
            {card.icon}
          </span>
          <div>
            <p className="eyebrow">{card.eyebrow}</p>
            <h3 className="step-metric">{card.metric}</h3>
            <p className="step-desc">{card.desc}</p>
          </div>
        </div>
      ))}
    </section>
  );
}
