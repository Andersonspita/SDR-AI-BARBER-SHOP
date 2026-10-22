-- Schema Oficial Gerente Barber SDR-AI
-- Idempotente: roda no initdb do Postgres (Docker) e também na inicialização da API,
-- o que aplica colunas novas em bancos/volumes já existentes.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

DO $$ BEGIN
  CREATE TYPE lead_status AS ENUM (
    'RAW', 'ENRICHED', 'CONTACTED_D1', 'FOLLOWUP_SENT',
    'INTERESTED', 'OBJECTION_RAISED', 'TRIAL_STARTED', 'CONVERTED', 'DO_NOT_CONTACT'
  );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE tech_maturity AS ENUM ('MANUAL_WHATSAPP', 'COMPETITOR', 'UNKNOWN');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS barbershop_leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    owner_name VARCHAR(255),
    phone VARCHAR(30) UNIQUE NOT NULL,
    instagram_handle VARCHAR(100),
    bio_url TEXT,
    competitor_name VARCHAR(100),
    estimated_barbers INT DEFAULT 1,
    current_system tech_maturity DEFAULT 'UNKNOWN',
    priority VARCHAR(20) DEFAULT 'MEDIA',
    status lead_status DEFAULT 'RAW',
    city VARCHAR(100),
    state VARCHAR(2),
    neighborhood VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Colunas adicionadas pelos Agentes 4 e 5 (grafo de estados)
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS langgraph_node VARCHAR(80) DEFAULT '1.Scraper';
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS last_objection_type VARCHAR(30);
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Origem do lead. O default SYNTHETIC é proposital: linhas antigas vieram do gerador local
-- (telefones inventados) e precisam continuar bloqueadas para envio real.
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS source VARCHAR(30) NOT NULL DEFAULT 'SYNTHETIC';

-- Régua automática D0 -> D2 -> D5 (worker de cadência)
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS cadence_step VARCHAR(5);
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS next_touch_at TIMESTAMP WITH TIME ZONE;

-- Teste grátis no barber-shop (provisionado pelo SDR, atualizado pelos webhooks de lá)
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS email VARCHAR(254);
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS trial_shop_id VARCHAR(64);
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS trial_status VARCHAR(20);
ALTER TABLE barbershop_leads ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP WITH TIME ZONE;

CREATE TABLE IF NOT EXISTS interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES barbershop_leads(id) ON DELETE CASCADE,
    channel VARCHAR(20) DEFAULT 'WHATSAPP',
    direction VARCHAR(10) CHECK (direction IN ('INBOUND', 'OUTBOUND')),
    message_text TEXT NOT NULL,
    intent_detected VARCHAR(50),
    langgraph_node VARCHAR(80),
    sent_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Configurações operacionais alteradas pelo painel (ex: modo de trial do Agente 5)
CREATE TABLE IF NOT EXISTS sdr_settings (
    key VARCHAR(50) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON barbershop_leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_city_state ON barbershop_leads(state, city);
CREATE INDEX IF NOT EXISTS idx_interactions_lead_id ON interactions(lead_id);
CREATE INDEX IF NOT EXISTS idx_leads_next_touch_at ON barbershop_leads(next_touch_at) WHERE next_touch_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_interactions_outbound_sent_at ON interactions(sent_at) WHERE direction = 'OUTBOUND';
