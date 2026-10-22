"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Interaction, Lead } from "../types";

interface WhatsAppSimulatorProps {
  lead: Lead;
  interactions: Interaction[];
  loadError: string | null;
  isSending: boolean;
  /** Falso com WhatsApp real: a resposta iria para o barbeiro de verdade. */
  isSimulatorEnabled: boolean;
  onSimulate: (message: string) => Promise<boolean>;
}

/** Mensagens prontas que exercitam cada caminho do grafo (interesse, 4 objeções e descadastro). */
const QUICK_REPLIES = [
  "Tenho interesse, como funciona?",
  "Já uso caderno e funciona bem",
  "Meus clientes são velhos, só ligam",
  "Não tenho tempo pra cadastrar cliente",
  "Já uso o Trinks aqui",
  "Para de mandar mensagem",
];

const timeFormatter = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });

/** Chat do lead + simulador do webhook WhatsApp (mensagens do barbeiro passam pelo grafo de verdade). */
export function WhatsAppSimulator({
  lead,
  interactions,
  loadError,
  isSending,
  isSimulatorEnabled,
  onSimulate,
}: WhatsAppSimulatorProps) {
  const [draft, setDraft] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const isBlocked = lead.status === "DO_NOT_CONTACT";

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [interactions.length]);

  const send = async (message: string) => {
    const text = message.trim();
    if (!text) return;
    const ok = await onSimulate(text);
    if (ok) setDraft("");
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await send(draft);
  };

  return (
    <div id="simulador" className="chat">
      <p className="eyebrow">💬 Simulador Webhook WhatsApp • {lead.langgraph_node}</p>

      <div ref={logRef} className="chat-log" aria-live="polite">
        {loadError && <p className="tone-danger" style={{ fontSize: 12 }}>{loadError}</p>}
        {!loadError && interactions.length === 0 && (
          <p className="k-col-empty">Sem mensagens ainda. Envie o D0 ou simule uma resposta do barbeiro abaixo.</p>
        )}
        {interactions.map((interaction) => {
          const isInbound = interaction.direction === "INBOUND";
          return (
            <div key={interaction.id} className={`bubble ${isInbound ? "inbound" : "outbound"}`}>
              <strong className={isInbound ? "tone-muted" : "tone-brand"}>{isInbound ? "Barbeiro: " : "SDR-AI: "}</strong>
              {interaction.message_text}
              <span className="bubble-meta">
                {timeFormatter.format(new Date(interaction.sent_at))}
                {interaction.intent_detected ? ` • ${interaction.intent_detected}` : ""}
              </span>
            </div>
          );
        })}
      </div>

      {isBlocked && (
        <p className="tone-muted" style={{ fontSize: 12, marginBottom: 10 }}>
          Lead descadastrado: o SDR-AI não responde mais esse número.
        </p>
      )}

      {!isSimulatorEnabled ? (
        <p className="tone-muted" style={{ fontSize: 12 }}>
          Simulador desligado: com WhatsApp real, as respostas chegam pelo webhook da Evolution API.
        </p>
      ) : (
        <>
        <div className="chip-row" aria-label="Respostas rápidas">
          {QUICK_REPLIES.map((reply) => (
            <button key={reply} type="button" className="chip" disabled={isSending} onClick={() => void send(reply)}>
              {reply}
            </button>
          ))}
        </div>

        <form className="chat-form" onSubmit={handleSubmit}>
          <label className="sr-only" htmlFor="simulated-message">Mensagem do barbeiro</label>
          <input
            id="simulated-message"
            className="ctrl-input"
            placeholder="Digite como se fosse o barbeiro..."
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button className="btn-primary" type="submit" disabled={isSending || !draft.trim()}>
            {isSending ? "..." : "Simular"}
          </button>
        </form>
        </>
      )}
    </div>
  );
}
