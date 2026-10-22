# Gerente Barber SDR-AI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir o sistema completo do SDR-AI autônomo para o SaaS Gerente Barber (`barber-shop.horizonaj.com.br`), incluindo Docker Compose (Postgres, Redis, Evolution API mock/conector), API FastAPI com os 5 agentes (Scraper por UF/Cidade, Copywriter Few-Shot, Cadência Anti-Ban, RAG de Objeções e Máquina de Estados LangGraph no `/webhook/whatsapp`) e Dashboard Next.js/Web com o Design System oficial do Gerente Barber + preview da vitrine `/{slug}`.

**Architecture:** Backend assíncrono em FastAPI (Python 3.11+) com persistência SQL/memória híbrida (suporta PostgreSQL via Docker e fallback SQLite/In-Memory local imediato para rodar sem depender de containers ativos), orquestrador de estados LangGraph/StateMachine determinístico + LLM plugável, e Frontend moderno com a estética exata de `barber-shop.horizonaj.com.br/barbearia-elite` conectado à API em tempo real.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, Pytest, PostgreSQL, Redis, Docker Compose, Next.js 14 / React + Tailwind/CSS Variables (`Archivo` + `Public Sans`).

**Spec:** `docs/superpowers/specs/2026-10-21-sdr-ai-design.md`

## Global Constraints

- Respostas e copy sempre em `pt-BR` informal e respeitoso para donos de barbearia, sem jargões corporativos ("ROI", "sinergia").
- Matriz de planos respeitada: Solo (1 cadeira, R$ 49,90), Squad (2-4 cadeiras, R$ 99,90), Pro (5-9 cadeiras, R$ 179,90), Empire (10+ cadeiras, R$ 299,90).
- URL base de vitrine padrão: `https://barber-shop.horizonaj.com.br/{slug}` com modo **Stand-by VIP** ativo por padrão e alternável no painel.
- Delay anti-ban configurável entre `45` e `180` segundos com spintax dinâmico.

## Review Focus

- Detecção incorreta de URL na bio quando o link possui maiúsculas, parâmetros UTM ou encurtadores `wa.me`.
- Geração de `slug` com acentos, caracteres especiais ou espaços (ex: `"Barbearia São José & Cia"` deve virar `"barbearia-sao-jose-cia"`).
- Classificação de mensagens de descadastro ("para de mandar mensagem", "sair", "não quero") deve sempre mover o lead para `DO_NOT_CONTACT` e bloquear novos envios.
- Respostas com múltiplas objeções na mesma mensagem (ex: "uso caderno e meus clientes são velhos") devem ser tratadas com empatia e foco no agendamento sem baixar app.
- Chaveamento entre modo `STAND_BY` e `AUTO_LINK` no Agente 5 ao detectar intenção `INTERESTED`.

---

### Task 1: Infraestrutura Docker, Banco de Dados SQL & Modelos Base

**Files:**
- Create: `docker-compose.yml`
- Create: `database/init.sql`
- Create: `.env.example`
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`
- Create: `backend/app/models.py`
- Create: `backend/app/store.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces:
  - `LeadStatus` (`RAW`, `ENRICHED`, `CONTACTED_D1`, `FOLLOWUP_SENT`, `INTERESTED`, `OBJECTION_RAISED`, `TRIAL_STARTED`, `CONVERTED`, `DO_NOT_CONTACT`)
  - `TechMaturity` (`MANUAL_WHATSAPP`, `COMPETITOR`, `UNKNOWN`)
  - `generate_shop_slug(shop_name: str) -> str`
  - ` recommend_plan(estimated_barbers: int) -> dict`

- [x] **Step 1: Write the failing test in `backend/tests/test_models.py`**
- [x] **Step 2: Run pytest to verify it fails**
- [x] **Step 3: Create `docker-compose.yml`, `database/init.sql`, `.env.example`, `backend/app/models.py` and `backend/app/store.py`**
- [x] **Step 4: Run pytest to verify it passes**

---

### Task 2: Agente 1 — Scraper por Estado/Cidade & Detector de Bio Instagram

**Files:**
- Create: `backend/app/agents/__init__.py`
- Create: `backend/app/agents/scraper.py`
- Test: `backend/tests/test_scraper.py`

**Interfaces:**
- Consumes: `TechMaturity`, `LeadStatus`, `generate_shop_slug`, `recommend_plan` from `backend.app.models`
- Produces:
  - `detect_bio_system(bio_url: str | None, bio_text: str = "") -> dict`
  - `scrape_barbershops_by_location(state: str, city: str, neighborhood: str | None = None) -> list[BarbershopLead]`

- [x] **Step 1: Write the failing tests for URL detection (`wa.me`, `AppBarber`, `Booksy`, `Trinks`, `Gendo`, empty) and location search in `backend/tests/test_scraper.py`**
- [x] **Step 2: Run pytest to verify it fails**
- [x] **Step 3: Implement `backend/app/agents/scraper.py` with Google Places connector + rich realistic Brazilian barbershop discovery generator by UF/City**
- [x] **Step 4: Run pytest to verify it passes**

---

### Task 3: Agentes 2 & 3 — Copywriter Few-Shot & Motor Anti-Ban (Cadência D0/D2/D5)

**Files:**
- Create: `backend/app/agents/copywriter.py`
- Create: `backend/app/agents/cadence.py`
- Test: `backend/tests/test_copy_and_cadence.py`

**Interfaces:**
- Consumes: `BarbershopLead`, `TechMaturity` from `backend.app.models`
- Produces:
  - `FEW_SHOT_PROMPTS: dict`
  - `generate_outbound_copy(lead: BarbershopLead, touchpoint: str = "D0") -> str`
  - `calculate_antiban_schedule(leads: list[BarbershopLead], daily_limit: int = 50) -> list[dict]`

- [x] **Step 1: Write failing tests in `backend/tests/test_copy_and_cadence.py` verifying Solo vs Multi-barber hooks, competitor hook, no corporate jargon, and 45s-180s anti-ban intervals**
- [x] **Step 2: Run pytest to verify it fails**
- [x] **Step 3: Implement `copywriter.py` and `cadence.py`**
- [x] **Step 4: Run pytest to verify it passes**

---

### Task 4: Agentes 4 & 5 — RAG de Quebra de Objeções, LangGraph State Machine & Webhook WhatsApp

**Files:**
- Create: `backend/app/agents/objection_rag.py`
- Create: `backend/app/agents/graph.py`
- Create: `backend/app/services/evolution.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/test_webhook_and_graph.py`

**Interfaces:**
- Consumes: `store`, `scraper`, `copywriter`, `cadence`
- Produces:
  - `classify_and_run_graph(lead: BarbershopLead, inbound_message: str, trial_mode: str = "STAND_BY") -> dict`
  - FastAPI endpoints:
    - `POST /api/leads/scrape`
    - `GET /api/leads`
    - `POST /api/leads/{lead_id}/send-d0`
    - `POST /webhook/whatsapp`
    - `GET /api/metrics`
    - `POST /api/settings/trial-mode`

- [x] **Step 1: Write failing tests in `backend/tests/test_webhook_and_graph.py` covering `/webhook/whatsapp` for `INTERESTED` (Stand-by vs Auto Link `/slug`), `OBJECTION_RAISED` (all 4 objections), and `UNSUBSCRIBE` (`DO_NOT_CONTACT`)**
- [x] **Step 2: Run pytest to verify it fails**
- [x] **Step 3: Implement `objection_rag.py`, `graph.py`, `evolution.py`, and `main.py`**
- [x] **Step 4: Run pytest to verify all backend tests pass**

---

### Task 5: Dashboard de Monitoramento & Simulador Ao Vivo (Design System Gerente Barber)

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/next.config.mjs`
- Create: `frontend/app/layout.tsx`
- Create: `frontend/app/globals.css`
- Create: `frontend/app/page.tsx`
- Create: `README.md`

- [x] **Step 1: Create Next.js 14 application with the exact CSS tokens and typography (`Archivo` + `Public Sans`) from `barber-shop.horizonaj.com.br/barbearia-elite` and serve the interactive dashboard**
- [x] **Step 2: Connect Frontend to FastAPI endpoints (State/City scraper, real-time KPIs, Kanban board, `/slug` storefront live preview, and WhatsApp webhook simulator)**
- [x] **Step 3: Verify end-to-end flow and document setup in `README.md`**
