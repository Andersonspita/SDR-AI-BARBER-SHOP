"""Bloqueadores de produção: autenticação, trava de leads fictícios e worker de cadência D2/D5."""
import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
import httpx
import jwt
from fastapi.testclient import TestClient
from backend.app.auth import AuthSettings, login_throttle, production_auth_problems
from backend.app.main import app, validate_startup_config
from backend.app.models import BarbershopLead, LeadSource, LeadStatus, TechMaturity
from backend.app.store import store
from backend.app.agents.scraper import ScraperError, discover_barbershops, search_google_places
from backend.app.services.cadence_worker import CadenceConfig, CadenceWorker
from backend.app.services.evolution import evolution_client
from backend.tests.helpers import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_AUTH_ENV,
    TEST_WEBHOOK_SECRET,
    WEBHOOK_HEADERS,
    login,
    patch_auth_env,
)

# Segunda-feira 19/10/2026 10h em Brasília (13h UTC): dentro da janela de envio.
MONDAY_10H_BRT = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)


def _lead(source: LeadSource = LeadSource.SYNTHETIC, **overrides) -> BarbershopLead:
    data = dict(
        shop_name="Barbearia Navalha Real",
        phone="5577999555444",
        city="Vitória da Conquista",
        state="BA",
        current_system=TechMaturity.MANUAL_WHATSAPP,
        status=LeadStatus.ENRICHED,
        source=source,
    )
    data.update(overrides)
    return BarbershopLead(**data)


def _run(coro):
    return asyncio.run(coro)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        env = patch_auth_env()
        env.start()
        self.addCleanup(env.stop)
        store.reset()
        login_throttle.reset()
        self.client = TestClient(app)


class TestAuthentication(ApiTestCase):
    def test_api_requires_session(self):
        for method, path in (("get", "/api/leads"), ("get", "/api/metrics"), ("post", "/api/settings/trial-mode")):
            self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)

    def test_login_sets_httponly_cookie_and_me_works(self):
        res = self.client.post("/api/auth/login", json={"email": TEST_ADMIN_EMAIL.upper(), "password": TEST_ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertIn("httponly", res.headers["set-cookie"].lower())
        self.assertIn("samesite=lax", res.headers["set-cookie"].lower())
        self.assertEqual(self.client.get("/api/auth/me").json()["email"], TEST_ADMIN_EMAIL)
        self.assertEqual(self.client.get("/api/leads").status_code, 200)

    def test_bearer_token_works_for_scripts(self):
        token = self.client.post("/api/auth/login", json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD}).json()["token"]
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/leads", headers={"Authorization": f"Bearer {token}"}).status_code, 200)

    def test_wrong_password_rejected_and_brute_force_throttled(self):
        for _ in range(5):
            res = self.client.post("/api/auth/login", json={"email": TEST_ADMIN_EMAIL, "password": "errada"})
            self.assertEqual(res.status_code, 401)
        res = self.client.post("/api/auth/login", json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 429)

    def test_logout_ends_session(self):
        login(self.client)
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/leads").status_code, 401)

    def test_expired_or_forged_tokens_rejected(self):
        secret = TEST_AUTH_ENV["JWT_SECRET"]
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        expired = jwt.encode({"sub": TEST_ADMIN_EMAIL, "exp": past}, secret, algorithm="HS256")
        forged = jwt.encode({"sub": TEST_ADMIN_EMAIL, "exp": past + timedelta(days=1)}, "outro-segredo" * 4, algorithm="HS256")
        other_user = jwt.encode({"sub": "intruso@x.com", "exp": past + timedelta(days=1)}, secret, algorithm="HS256")
        for token in (expired, forged, other_user, "lixo"):
            res = self.client.get("/api/leads", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(res.status_code, 401)

    def test_login_unavailable_without_admin_config(self):
        with mock.patch.dict(os.environ, {"ADMIN_PASSWORD": ""}):
            res = self.client.post("/api/auth/login", json={"email": TEST_ADMIN_EMAIL, "password": "x"})
        self.assertEqual(res.status_code, 503)


class TestWebhookToken(ApiTestCase):
    def setUp(self):
        super().setUp()
        _run(store.upsert_lead(_lead()))
        self.payload = {"phone": "5577999555444", "message": "tenho interesse"}

    def test_rejects_missing_or_wrong_token(self):
        self.assertEqual(self.client.post("/webhook/whatsapp", json=self.payload).status_code, 401)
        res = self.client.post("/webhook/whatsapp", json=self.payload, headers={"X-Webhook-Token": "errado"})
        self.assertEqual(res.status_code, 401)

    def test_accepts_header_or_query_token(self):
        self.assertEqual(self.client.post("/webhook/whatsapp", json=self.payload, headers=WEBHOOK_HEADERS).status_code, 200)
        res = self.client.post(f"/webhook/whatsapp?token={TEST_WEBHOOK_SECRET}", json=self.payload)
        self.assertEqual(res.json()["status"], "processed")

    def test_unconfigured_secret_fails_closed(self):
        with mock.patch.dict(os.environ, {"WEBHOOK_SECRET": ""}):
            res = self.client.post("/webhook/whatsapp", json=self.payload, headers={"X-Webhook-Token": ""})
        self.assertEqual(res.status_code, 503)


class TestProductionGuards(unittest.TestCase):
    def test_default_config_is_flagged(self):
        with mock.patch.dict(os.environ, {"ADMIN_EMAIL": "a@b.com", "ADMIN_PASSWORD": "troque-esta-senha", "JWT_SECRET": "", "WEBHOOK_SECRET": ""}):
            problems = production_auth_problems(AuthSettings.from_env())
        self.assertEqual(len(problems), 4)

    def test_strong_config_passes(self):
        strong = {
            "ADMIN_EMAIL": "dono@gerentebarber.com.br",
            "ADMIN_PASSWORD": "uma-senha-bem-forte-2026",
            "JWT_SECRET": "x" * 48,
            "WEBHOOK_SECRET": "y" * 48,
            "COOKIE_SECURE": "true",
        }
        with mock.patch.dict(os.environ, strong):
            self.assertEqual(production_auth_problems(AuthSettings.from_env()), [])
            with mock.patch.object(evolution_client, "provider", "EVOLUTION"):
                validate_startup_config()

    def test_real_provider_refuses_to_start_with_weak_config(self):
        with mock.patch.dict(os.environ, {"ADMIN_PASSWORD": "troque-esta-senha", "JWT_SECRET": ""}), \
                mock.patch.object(evolution_client, "provider", "EVOLUTION"):
            with self.assertRaises(RuntimeError):
                validate_startup_config()


class TestSyntheticLeadLock(ApiTestCase):
    def setUp(self):
        super().setUp()
        login(self.client)
        provider = mock.patch.object(evolution_client, "provider", "EVOLUTION")
        provider.start()
        self.addCleanup(provider.stop)
        self.send_text = mock.AsyncMock(return_value={"delivered": True, "provider": "EVOLUTION", "message_id": "x", "error": None})
        sender = mock.patch.object(evolution_client, "send_text", self.send_text)
        sender.start()
        self.addCleanup(sender.stop)

    def test_generator_leads_are_marked_synthetic(self):
        with mock.patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": ""}):
            leads = _run(discover_barbershops("BA", "Salvador"))
        self.assertTrue(leads)
        self.assertTrue(all(l.is_synthetic and l.to_dict()["is_synthetic"] for l in leads))

    def test_send_d0_to_synthetic_lead_blocked_with_real_provider(self):
        lead = _run(store.upsert_lead(_lead()))
        res = self.client.post(f"/api/leads/{lead.id}/send-d0")
        self.assertEqual(res.status_code, 409)
        self.assertIn("fictício", res.json()["detail"])
        self.send_text.assert_not_called()

    def test_reply_to_synthetic_lead_blocked_with_real_provider(self):
        lead = _run(store.upsert_lead(_lead(status=LeadStatus.CONTACTED_D1)))
        body = self.client.post("/webhook/whatsapp", json={"phone": lead.phone, "message": "tenho interesse"}, headers=WEBHOOK_HEADERS).json()
        self.assertTrue(body["delivery"]["blocked"])
        self.send_text.assert_not_called()
        directions = [i.direction for i in _run(store.get_lead_interactions(lead.id))]
        self.assertEqual(directions, ["INBOUND"])

    def test_real_lead_is_sent_and_scheduled_for_d2(self):
        lead = _run(store.upsert_lead(_lead(source=LeadSource.GOOGLE_PLACES)))
        res = self.client.post(f"/api/leads/{lead.id}/send-d0")
        self.assertEqual(res.status_code, 200)
        self.send_text.assert_awaited_once()
        saved = res.json()["lead"]
        self.assertEqual(saved["cadence_step"], "D0")
        wait = datetime.fromisoformat(saved["next_touch_at"]) - datetime.now(timezone.utc)
        self.assertAlmostEqual(wait.total_seconds(), timedelta(days=2).total_seconds(), delta=60)

    def test_simulator_disabled_with_real_provider(self):
        res = self.client.post("/api/simulator/inbound", json={"phone": "5577999555444", "message": "oi"})
        self.assertEqual(res.status_code, 403)


class TestGooglePlaces(unittest.TestCase):
    def _client(self, handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_parses_places_and_skips_unusable_numbers(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["key"] = request.headers["X-Goog-Api-Key"]
            captured["body"] = request.read().decode()
            return httpx.Response(200, json={"places": [
                {"displayName": {"text": "Barbearia do Zé"}, "internationalPhoneNumber": "+55 77 99876-5432", "websiteUri": "https://wa.me/5577998765432"},
                {"displayName": {"text": "Navalha Booksy"}, "internationalPhoneNumber": "+55 77 3421-1234", "websiteUri": "https://booksy.com/pt-br/navalha"},
                {"displayName": {"text": "Sem Telefone"}},
                {"displayName": {"text": "Gringa"}, "internationalPhoneNumber": "+1 415-555-0100"},
            ]})

        async def go():
            async with self._client(handler) as client:
                return await search_google_places("ba", "Vitória da Conquista", "Candeias", "chave-teste", http_client=client)

        leads = _run(go())
        self.assertEqual(captured["key"], "chave-teste")
        self.assertIn("barbearia em Candeias, Vitória da Conquista - BA", captured["body"])
        self.assertEqual([l.shop_name for l in leads], ["Barbearia do Zé", "Navalha Booksy"])
        self.assertEqual(leads[0].phone, "5577998765432")
        self.assertEqual(leads[0].current_system, TechMaturity.MANUAL_WHATSAPP)
        self.assertEqual(leads[1].competitor_name, "Booksy")
        self.assertTrue(all(l.source == LeadSource.GOOGLE_PLACES and not l.is_synthetic for l in leads))

    def test_api_error_raises_scraper_error(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": {"message": "API key not valid"}})

        async def go():
            async with self._client(handler) as client:
                await search_google_places("BA", "Salvador", None, "ruim", http_client=client)

        with self.assertRaisesRegex(ScraperError, "API key not valid"):
            _run(go())

    def test_scrape_endpoint_returns_502_on_scraper_error(self):
        with patch_auth_env():
            store.reset()
            client = TestClient(app)
            login(client)
            with mock.patch("backend.app.main.discover_barbershops", mock.AsyncMock(side_effect=ScraperError("cota estourada"))):
                res = client.post("/api/leads/scrape", json={"state": "BA", "city": "Salvador"})
        self.assertEqual(res.status_code, 502)
        self.assertIn("cota estourada", res.json()["detail"])


class TestCadenceWorker(unittest.TestCase):
    def setUp(self):
        store.reset()
        self.now = MONDAY_10H_BRT
        self.worker = CadenceWorker(store, CadenceConfig(), clock=lambda: self.now)
        self.lead = _run(store.upsert_lead(_lead(
            status=LeadStatus.CONTACTED_D1,
            cadence_step="D0",
            next_touch_at=(MONDAY_10H_BRT - timedelta(days=2)).isoformat(),
        )))

    def test_sends_d2_then_d5_then_stops(self):
        first = _run(self.worker.run_once())
        self.assertEqual(first["status"], "sent")
        self.assertEqual(first["touchpoint"], "D2")
        self.assertEqual(self.lead.status, LeadStatus.FOLLOWUP_SENT)
        self.assertEqual(datetime.fromisoformat(self.lead.next_touch_at), self.now + timedelta(days=3))

        self.assertEqual(_run(self.worker.run_once())["status"], "idle")  # D5 ainda não venceu

        self.now = self.now + timedelta(days=4)  # sexta 10h
        second = _run(self.worker.run_once())
        self.assertEqual(second["touchpoint"], "D5")
        self.assertIsNone(self.lead.next_touch_at)
        self.assertEqual(self.lead.cadence_step, "D5")

        sent = [i.langgraph_node for i in _run(store.get_lead_interactions(self.lead.id))]
        self.assertEqual(sent, ["3.Cadence:D2", "3.Cadence:D5"])

    def test_respects_send_window(self):
        for moment in (MONDAY_10H_BRT.replace(hour=1), MONDAY_10H_BRT - timedelta(days=1)):  # 22h BRT / domingo
            self.now = moment
            self.assertEqual(_run(self.worker.run_once())["status"], "outside_window")
        self.assertEqual(_run(store.get_lead_interactions(self.lead.id)), [])

    def test_respects_daily_limit(self):
        store.daily_limit = 0
        self.assertEqual(_run(self.worker.run_once())["status"], "daily_limit")

    def test_reply_removes_lead_from_cadence(self):
        with patch_auth_env():
            client = TestClient(app)
            client.post("/webhook/whatsapp", json={"phone": self.lead.phone, "message": "me conta mais"}, headers=WEBHOOK_HEADERS)
        self.assertIsNone(_run(store.get_lead(self.lead.id)).next_touch_at)
        self.assertEqual(_run(self.worker.run_once())["status"], "idle")

    def test_do_not_contact_lead_is_skipped_and_cleared(self):
        self.lead.status = LeadStatus.DO_NOT_CONTACT
        result = _run(self.worker.run_once())
        self.assertEqual(result["status"], "skipped")
        self.assertIsNone(self.lead.next_touch_at)
        self.assertEqual(_run(store.get_lead_interactions(self.lead.id)), [])

    def test_synthetic_lead_blocked_with_real_provider(self):
        with mock.patch.object(evolution_client, "provider", "EVOLUTION"):
            result = _run(self.worker.run_once())
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(self.lead.next_touch_at)

    def test_delivery_failure_retries_later(self):
        failure = {"delivered": False, "provider": "EVOLUTION", "message_id": None, "error": "timeout"}
        with mock.patch.object(evolution_client, "send_text", mock.AsyncMock(return_value=failure)):
            result = _run(self.worker.run_once())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(datetime.fromisoformat(self.lead.next_touch_at), self.now + timedelta(minutes=30))
        self.assertEqual(self.lead.cadence_step, "D0")

    def test_run_forever_waits_antiban_delay_between_sends(self):
        sleeps = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise asyncio.CancelledError

        worker = CadenceWorker(store, CadenceConfig(), clock=lambda: self.now, sleep=fake_sleep)
        with self.assertRaises(asyncio.CancelledError):
            _run(worker.run_forever())
        self.assertTrue(45 <= sleeps[0] <= 180, sleeps)  # depois do D2 enviado
        self.assertEqual(sleeps[1], 30)  # fila vazia: só o polling

    def test_config_from_env(self):
        env = {"CADENCE_SEND_HOURS": "9-18", "CADENCE_SEND_WEEKDAYS": "0-4", "ANTIBAN_MIN_DELAY_SEC": "60", "ANTIBAN_MAX_DELAY_SEC": "90"}
        with mock.patch.dict(os.environ, env):
            config = CadenceConfig.from_env()
        self.assertEqual((config.send_start_hour, config.send_end_hour), (9, 18))
        self.assertEqual(config.send_weekdays, frozenset(range(5)))
        self.assertEqual((config.min_delay_sec, config.max_delay_sec), (60, 90))
        self.assertFalse(config.is_within_send_window(MONDAY_10H_BRT + timedelta(days=5)))  # sábado


if __name__ == "__main__":
    unittest.main()
