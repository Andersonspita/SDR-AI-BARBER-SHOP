# Gerente Barber SDR-AI — Documento de Design & Arquitetura

**Data:** 2026-10-21  
**Produto:** Gerente Barber (`https://barber-shop.horizonaj.com.br/`) — Sistema Autônomo de Prospecção Outbound (SDR-AI)

---

## 1. Visão e Objetivo
Construir um sistema autônomo de prospecção outbound, qualificação e conversão de donos de barbearias focado em WhatsApp e Instagram Direct para o SaaS **Gerente Barber**.

O sistema opera localmente de imediato (com simuladores de busca no Google Maps/Instagram, LLM determinístico/template ou plugável via API Key OpenAI/Gemini e simulador Evolution API) e está 100% preparado para produção via Docker Compose.

---

## 2. Regras de Negócio e Segmentação de Planos
- **Plano Solo (1 Barbeiro)**: R$ 49,90 a R$ 59,90/mês — Foco: agendamento 24/7 e fim da agenda de papel/caderno e tempo perdido respondendo WhatsApp durante cortes.
- **Plano Duo/Squad (2 a 4 Barbeiros)**: R$ 99,90 a R$ 129,90/mês — Foco: cálculo de comissão sem brigas no fim do mês e controle de estoque.
- **Plano Club/Pro (5 a 9 Barbeiros)**: R$ 179,90 a R$ 219,90/mês — Foco: painel DRE, redução de no-show e clube de assinaturas.
- **Plano Empire (10+ Barbeiros / Multi-unidade)**: R$ 299,90 a R$ 389,90/mês — Foco: split de pagamento automático (Lei do Salão Parceiro) e gestão multi-unidade.

---

## 3. Os 5 Agentes Autônomos (Máquina de Estados / LangGraph)

1. **Agente 1 — Lead Scraper & Bio Detector (`scraper.py`)**:
   - Recebe `state` (UF), `city` e opcionalmente `neighborhood` pelo painel.
   - Extrai/descobre barbearias (integração Google Places / Instagram + gerador realista local quando sem chave de API).
   - Analisa a URL da bio:
     - Contém `wa.me`, `api.whatsapp.com`, `whats.link`: `MANUAL_WHATSAPP` (Prioridade **ALTA**).
     - Contém `appbarber`, `booksy`, `trinks`, `gendo`, `avec`, `beleza`: `COMPETITOR` (Estratégia de **MIGRAÇÃO** com nome do concorrente detectado).
     - Sem link ou link genérico: `UNKNOWN` (Prioridade **MÉDIA**).
   - Gera automaticamente o `slug` da barbearia no padrão `https://barber-shop.horizonaj.com.br/{slug}` (como `/barbearia-elite`).

2. **Agente 2 — WhatsApp & Direct Copywriter (`copywriter.py`)**:
   - Prompts com exemplos **Few-Shot** reais, sem jargões corporativos ("ROI", "sinergia"), mensagens curtas (máx 4–5 linhas).
   - Adapta o gancho conforme `current_system` (`MANUAL_WHATSAPP` vs `COMPETITOR`) e `estimated_barbers` (Solo vs Equipe 3+ barbeiros).

3. **Agente 3 — Execution & Anti-Ban Cadence Engine (`cadence.py`)**:
   - Régua de contato D0 (gancho inicial), D2 (follow-up visual/áudio sobre agenda) e D5 (oferta de 7 dias grátis com vitrine pré-configurada).
   - Proteção Anti-Ban: delay randômico de `45s` a `180s`, spintax dinâmico para evitar mensagens idênticas e controle de limite diário por instância na Evolution API.

4. **Agente 4 — Objection Handler RAG (`objection_rag.py`)**:
   - Base de conhecimento especializada nas 4 objeções clássicas de barbeiros:
     - `"Não tenho tempo para cadastrar clientes"` -> Importação automática/feita pela equipe em 10 min.
     - `"Meus clientes são velhos e só usam ligação/Whats"` -> Confirmação no WhatsApp com 1 clique sem baixar aplicativo.
     - `"Já uso caderno e funciona bem"` -> Cálculo de faturamento perdido com esquecimentos e no-show (R$ 600 a R$ 1.200/mês).
     - `"Já uso o concorrente X"` -> Comparativo de mensalidade menor, lembrete via WhatsApp incluso e split sem taxas abusivas.

5. **Agente 5 — Trial Activator & Closer (`graph.py`)**:
   - Suporta chaveamento entre **Modo Stand-by VIP** (coloca o lead em fila VIP no Kanban + avisa que a vitrine `/slug` está sendo liberada ou sugere call rápida de 10 min no WhatsApp) e **Modo Link Automático** (`https://barber-shop.horizonaj.com.br/{slug}`).

---

## 4. Identidade Visual (Sincronizada com `barber-shop.horizonaj.com.br/barbearia-elite`)
- **Cores**: `--surface-0: #0a0a0b`, `--surface-1: #131315`, `--surface-2: #1c1c1f`, `--brand-400: #fbbf24`, `--brand-500: #f59e0b`, `--ink: #f5f5f6`, `--line: #2e2e33`.
- **Tipografia**: `Archivo` (Display/Títulos Black) + `Public Sans` (Corpo).
- **Preview de Vitrine Integrado**: Ao selecionar qualquer barbearia no Kanban, o painel exibe tanto o chat ao vivo do WhatsApp quanto o preview da página pública da barbearia no estilo `/barbearia-elite` ("Seu horário na cadeira, sem fila e sem espera" + Passos 1, 2 e 3).
