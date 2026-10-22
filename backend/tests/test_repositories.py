"""
Testes de contrato do repositório: rodam sempre em memória e também em PostgreSQL
quando TEST_DATABASE_URL aponta para um banco descartável (as tabelas são truncadas!).
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.models import BarbershopLead, Interaction, LeadStatus, TechMaturity
from backend.app.repositories import InMemoryRepository
from backend.app.store import store
from backend.tests.helpers import WEBHOOK_HEADERS, TEST_AUTH_ENV, login

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


def _lead(phone: str = "5577999123456", **overrides) -> BarbershopLead:
    data = dict(
        shop_name="Barbearia São José & Cia",
        phone=phone,
        city="Vitória da Conquista",
        state="BA",
        estimated_barbers=3,
        current_system=TechMaturity.MANUAL_WHATSAPP,
        status=LeadStatus.ENRICHED,
    )
    data.update(overrides)
    return BarbershopLead(**data)


class RepositoryContract:
    """Comportamento que toda implementação de LeadRepository precisa garantir."""

    async def make_repo(self):
        raise NotImplementedError

    async def asyncSetUp(self):
        self.repo = await self.make_repo()

    async def asyncTearDown(self):
        await self.repo.close()

    async def test_upsert_dedupes_by_phone_and_keeps_original(self):
        first = await self.repo.upsert_lead(_lead())
        second = await self.repo.upsert_lead(_lead(shop_name="Outro Nome"))
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.shop_name, "Barbearia São José & Cia")
        self.assertEqual(len(await self.repo.list_leads()), 1)

    async def test_save_lead_persists_graph_changes(self):
        lead = await self.repo.upsert_lead(_lead())
        lead.status = LeadStatus.OBJECTION_RAISED
        lead.last_objection_type = "CADERNO"
        lead.langgraph_node = "4.ObjectionRAG"
        await self.repo.save_lead(lead)

        loaded = await self.repo.get_lead(lead.id)
        self.assertEqual(loaded.status, LeadStatus.OBJECTION_RAISED)
        self.assertEqual(loaded.last_objection_type, "CADERNO")
        self.assertEqual(loaded.langgraph_node, "4.ObjectionRAG")
        self.assertEqual(loaded.slug, "barbearia-sao-jose-cia")
        self.assertEqual(loaded.recommended_plan["plan_name"], "Plano Duo/Squad")

    async def test_get_lead_unknown_or_invalid_id_returns_none(self):
        self.assertIsNone(await self.repo.get_lead("nope"))
        self.assertIsNone(await self.repo.get_lead("00000000-0000-0000-0000-000000000000"))

    async def test_get_lead_by_phone_matches_suffix_and_rejects_short_numbers(self):
        lead = await self.repo.upsert_lead(_lead())
        self.assertEqual((await self.repo.get_lead_by_phone("+55 (77) 99912-3456")).id, lead.id)
        self.assertEqual((await self.repo.get_lead_by_phone("77999123456")).id, lead.id)
        self.assertIsNone(await self.repo.get_lead_by_phone(""))
        self.assertIsNone(await self.repo.get_lead_by_phone("5511900000000"))

    async def test_list_leads_filters_case_insensitive_in_insertion_order(self):
        a = await self.repo.upsert_lead(_lead("5577990000001", shop_name="A"))
        b = await self.repo.upsert_lead(_lead("5577990000002", shop_name="B"))
        await self.repo.upsert_lead(_lead("5511990000003", shop_name="C", state="SP", city="Campinas"))
        found = await self.repo.list_leads(state="ba", city="VITÓRIA DA CONQUISTA")
        self.assertEqual([l.id for l in found], [a.id, b.id])

    async def test_interactions_order_counts_and_replies(self):
        lead = await self.repo.upsert_lead(_lead())
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        await self.repo.add_interaction(Interaction(lead_id=lead.id, direction="OUTBOUND", message_text="D0 ontem", sent_at=yesterday))
        await self.repo.add_interaction(Interaction(lead_id=lead.id, direction="OUTBOUND", message_text="D2 hoje"))
        await self.repo.add_interaction(
            Interaction(lead_id=lead.id, direction="INBOUND", message_text="uso caderno", intent_detected="OBJECTION_RAISED")
        )

        history = await self.repo.get_lead_interactions(lead.id)
        self.assertEqual([i.message_text for i in history], ["D0 ontem", "D2 hoje", "uso caderno"])
        self.assertEqual(history[2].intent_detected, "OBJECTION_RAISED")

        start_of_today = datetime.now(timezone.utc) - timedelta(hours=1)
        self.assertEqual(await self.repo.count_outbound_since(start_of_today), 1)
        self.assertEqual(await self.repo.replied_lead_ids(), {lead.id})

    async def test_settings_roundtrip_and_overwrite(self):
        self.assertIsNone(await self.repo.get_setting("trial_mode"))
        await self.repo.set_setting("trial_mode", "AUTO_LINK")
        await self.repo.set_setting("trial_mode", "STAND_BY")
        self.assertEqual(await self.repo.get_setting("trial_mode"), "STAND_BY")


class TestInMemoryRepository(RepositoryContract, unittest.IsolatedAsyncioTestCase):
    async def make_repo(self):
        return InMemoryRepository()


@unittest.skipUnless(TEST_DATABASE_URL, "defina TEST_DATABASE_URL para testar o PostgreSQL")
class TestPostgresRepository(RepositoryContract, unittest.IsolatedAsyncioTestCase):
    async def make_repo(self):
        from backend.app.repositories.postgres import PostgresRepository

        repo = await PostgresRepository.connect(TEST_DATABASE_URL, attempts=1)
        await repo._pool.execute("TRUNCATE interactions, barbershop_leads, sdr_settings CASCADE")
        return repo


class TestStorageSelection(unittest.TestCase):
    def tearDown(self):
        store.reset()

    def test_falls_back_to_memory_when_postgres_is_unreachable(self):
        env = {**TEST_AUTH_ENV, "DATABASE_URL": "postgresql://x:y@127.0.0.1:1/nada", "DATABASE_CONNECT_ATTEMPTS": "1"}
        with mock.patch.dict(os.environ, env), TestClient(app) as client:
            self.assertEqual(client.get("/health").json()["storage"], "memory")
            login(client)
            self.assertEqual(client.post("/api/leads/scrape", json={"state": "BA", "city": "Salvador"}).status_code, 200)

    def test_memory_when_storage_backend_forced(self):
        env = {**TEST_AUTH_ENV, "DATABASE_URL": "postgresql://x:y@127.0.0.1:1/nada", "STORAGE_BACKEND": "memory"}
        with mock.patch.dict(os.environ, env), TestClient(app) as client:
            self.assertEqual(client.get("/health").json()["storage"], "memory")


@unittest.skipUnless(TEST_DATABASE_URL, "defina TEST_DATABASE_URL para testar o PostgreSQL")
class TestPostgresSurvivesRestart(unittest.TestCase):
    def tearDown(self):
        store.reset()

    def test_leads_interactions_and_trial_mode_survive_api_restart(self):
        env = {**TEST_AUTH_ENV, "DATABASE_URL": TEST_DATABASE_URL, "STORAGE_BACKEND": "postgres"}
        with mock.patch.dict(os.environ, env):
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").json()["storage"], "postgres")
                login(client)
                client.post("/api/settings/trial-mode", json={"trial_mode": "STAND_BY"})
                leads = client.post("/api/leads/scrape", json={"state": "PR", "city": "Curitiba"}).json()["leads"]
                target = leads[0]
                self.assertEqual(client.post(f"/api/leads/{target['id']}/send-d0").status_code, 200)
                client.post("/api/settings/trial-mode", json={"trial_mode": "AUTO_LINK"})
                reply = client.post("/webhook/whatsapp", json={"phone": target["phone"], "message": "quero testar"}, headers=WEBHOOK_HEADERS).json()
                self.assertEqual(reply["lead"]["langgraph_node"], "5.TrialActivator:AwaitingEmail")
                reply = client.post("/webhook/whatsapp", json={"phone": target["phone"], "message": "dono@curitiba.com"}, headers=WEBHOOK_HEADERS).json()
                # Sem integração com o barber-shop no teste: o lead fica na fila de liberação manual.
                self.assertEqual(reply["lead"]["langgraph_node"], "5.TrialActivator:PendingManual")

            store.reset()  # simula o processo reiniciando: nada fica na memória

            with TestClient(app) as client:
                login(client)
                saved = {l["id"]: l for l in client.get("/api/leads", params={"city": "Curitiba"}).json()["leads"]}
                self.assertEqual(saved[target["id"]]["status"], "INTERESTED")
                self.assertEqual(saved[target["id"]]["email"], "dono@curitiba.com")
                self.assertEqual(saved[target["id"]]["langgraph_node"], "5.TrialActivator:PendingManual")
                history = client.get(f"/api/leads/{target['id']}/interactions").json()["interactions"]
                self.assertEqual([i["direction"] for i in history], ["OUTBOUND", "INBOUND", "OUTBOUND", "INBOUND", "OUTBOUND"])
                metrics = client.get("/api/metrics").json()
                self.assertEqual(metrics["trial_mode"], "AUTO_LINK")
                self.assertGreaterEqual(metrics["daily_sent_count"], 3)


if __name__ == "__main__":
    unittest.main()
