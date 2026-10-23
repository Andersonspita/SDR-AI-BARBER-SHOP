"""Números sem WhatsApp: ficam fora do funil na entrada e saem dele se o envio descobrir."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
import httpx
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.models import BarbershopLead, LeadSource, LeadStatus
from backend.app.services import evolution as evolution_module
from backend.app.services.cadence_worker import CadenceConfig, CadenceWorker
from backend.app.services.evolution import NO_WHATSAPP_ERROR, EvolutionClient, evolution_client
from backend.app.store import store
from backend.tests.helpers import login, patch_auth_env

NO_WHATSAPP = {"delivered": False, "provider": "EVOLUTION", "message_id": None, "error": NO_WHATSAPP_ERROR, "no_whatsapp": True}
MONDAY_10H_BRT = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _lead(**overrides) -> BarbershopLead:
    data = dict(shop_name="Barbearia Fixo", phone="557734221100", city="Vitória da Conquista", state="BA",
                status=LeadStatus.ENRICHED, source=LeadSource.APIFY_GOOGLE_MAPS)
    data.update(overrides)
    return BarbershopLead(**data)


def _patch_http(handler):
    """Faz o EvolutionClient falar com uma Evolution de mentira."""
    real = httpx.AsyncClient
    return mock.patch.object(
        evolution_module.httpx, "AsyncClient",
        side_effect=lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
    )


class TestEvolutionClient(unittest.TestCase):
    def setUp(self):
        self.client = EvolutionClient(provider="EVOLUTION", base_url="http://evo.test", instance="sdr", api_key="k")

    def test_400_number_not_on_whatsapp_is_reported_as_such(self):
        body = {"status": 400, "error": "Bad Request",
                "response": {"message": [{"jid": "557734221100@s.whatsapp.net", "exists": False, "number": "557734221100"}]}}
        with _patch_http(lambda request: httpx.Response(400, json=body)):
            result = _run(self.client.send_text("557734221100", "oi"))
        self.assertEqual((result["delivered"], result.get("no_whatsapp"), result["error"]), (False, True, NO_WHATSAPP_ERROR))

    def test_other_400_stays_a_generic_failure(self):
        with _patch_http(lambda request: httpx.Response(400, json={"error": "instance not connected"})):
            result = _run(self.client.send_text("5577999112233", "oi"))
        self.assertFalse(result["delivered"])
        self.assertNotIn("no_whatsapp", result)

    def test_numbers_on_whatsapp(self):
        rows = [{"number": "5577999112233", "exists": True}, {"number": "557734221100", "exists": False}]
        with _patch_http(lambda request: httpx.Response(200, json=rows)):
            self.assertEqual(_run(self.client.numbers_on_whatsapp(["5577999112233", "557734221100"])), {"5577999112233"})
        with _patch_http(lambda request: httpx.Response(500)):
            self.assertIsNone(_run(self.client.numbers_on_whatsapp(["5577999112233"])))  # na dúvida, não descarta


class TestFunnel(unittest.TestCase):
    def setUp(self):
        env = patch_auth_env()
        env.start()
        self.addCleanup(env.stop)
        store.reset()
        self.client = TestClient(app)
        login(self.client)

    def test_d0_to_number_without_whatsapp_moves_lead_out_of_the_funnel(self):
        lead = _run(store.upsert_lead(_lead()))
        with mock.patch.object(evolution_client, "provider", "EVOLUTION"), \
                mock.patch.object(evolution_client, "send_text", mock.AsyncMock(return_value=NO_WHATSAPP)):
            res = self.client.post(f"/api/leads/{lead.id}/send-d0")
        self.assertEqual(res.status_code, 422)
        self.assertIn("não tem WhatsApp", res.json()["detail"])
        saved = _run(store.get_lead(lead.id))
        self.assertEqual((saved.status, saved.langgraph_node), (LeadStatus.DO_NOT_CONTACT, "1.Scraper:SemWhatsApp"))

    def test_import_leaves_out_numbers_without_whatsapp(self):
        csv_text = "nome;telefone;cidade;uf\nCom Whats;(77) 99911-2233;VCA;BA\nSó fixo;(77) 3422-1100;VCA;BA\n"
        with mock.patch.object(evolution_client, "numbers_on_whatsapp", mock.AsyncMock(return_value={"5577999112233"})):
            res = self.client.post("/api/leads/import", json={"csv": csv_text}).json()
        self.assertEqual((res["imported"], res["without_whatsapp"]), (1, 1))
        self.assertEqual([lead["shop_name"] for lead in res["leads"]], ["Com Whats"])


class TestCadence(unittest.TestCase):
    def test_follow_up_to_number_without_whatsapp_stops_for_good(self):
        store.reset()
        lead = _run(store.upsert_lead(_lead(
            phone="5577999112233", status=LeadStatus.CONTACTED_D1, cadence_step="D0",
            next_touch_at=(MONDAY_10H_BRT - timedelta(days=2)).isoformat(),
        )))
        worker = CadenceWorker(store, CadenceConfig(), clock=lambda: MONDAY_10H_BRT)
        with mock.patch.object(evolution_client, "send_text", mock.AsyncMock(return_value=NO_WHATSAPP)):
            result = _run(worker.run_once())
        self.assertEqual(result["status"], "no_whatsapp")
        self.assertIsNone(lead.next_touch_at)  # sem nova tentativa em 30 min
        self.assertEqual(lead.status, LeadStatus.DO_NOT_CONTACT)


if __name__ == "__main__":
    unittest.main()
