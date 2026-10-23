"""Integração do teste grátis com o barber-shop: provisionamento, travas, painel e webhooks."""
import asyncio
import json
import os
import time
import uuid
import unittest
from dataclasses import dataclass, field
from typing import List
from unittest import mock
import httpx
from fastapi.testclient import TestClient
from backend.app.main import app, validate_startup_config
from backend.app.models import BarbershopLead, LeadSource, LeadStatus, TechMaturity, TrialStatus
from backend.app.store import store
from backend.app.services import barbershop_platform as platform_module
from backend.app.services import barbershop_webhook
from backend.app.services.barbershop_platform import BarbershopPlatformClient, ProvisionedTrial, ProvisioningError
from backend.app.services.evolution import evolution_client
from backend.app.services.trial_activation import trial_block_reason
from backend.tests.helpers import WEBHOOK_HEADERS, login, patch_auth_env

BARBERSHOP_SECRET = "segredo-do-webhook-do-barber-shop-com-32+"


def _lead(**overrides) -> BarbershopLead:
    data = dict(
        shop_name="Barbearia São José & Cia",
        owner_name="Marcos Oliveira",
        phone="5577999123456",
        city="Vitória da Conquista",
        state="BA",
        estimated_barbers=3,
        current_system=TechMaturity.MANUAL_WHATSAPP,
        status=LeadStatus.CONTACTED_D1,
        source=LeadSource.GOOGLE_PLACES,
    )
    data.update(overrides)
    return BarbershopLead(**data)


def _run(coro):
    return asyncio.run(coro)


@dataclass
class FakePlatform:
    """Substitui o cliente HTTP do barber-shop nos testes de fluxo."""

    enabled: bool = True
    allow_synthetic: bool = False
    trial_days: int = 7
    fail_with: str = ""
    calls: List[BarbershopLead] = field(default_factory=list)

    async def provision_trial(self, lead):
        self.calls.append(lead)
        if self.fail_with:
            raise ProvisioningError(self.fail_with)
        return ProvisionedTrial(
            created=True,
            shop_id="shop-123",
            slug=f"{lead.slug}-2",
            url=f"https://barber-shop.test/{lead.slug}-2",
            trial_ends_at="2026-10-29T13:00:00.000Z",
            setup_url="https://barber-shop.test/x/login/recuperar?token=abc",
        )


class TestPlatformClient(unittest.TestCase):
    def setUp(self):
        self.client = BarbershopPlatformClient(base_url="https://bs.test", provisioning_key="k", trial_days=10)
        self.lead = _lead(email="dono@x.com")

    def _transport(self, handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_payload_maps_plan_and_owner(self):
        payload = self.client.build_payload(self.lead)
        self.assertEqual(payload["externalLeadId"], self.lead.id)
        self.assertEqual(payload["plan"], "SQUAD")
        self.assertEqual(payload["slug"], "barbearia-sao-jose-cia")
        self.assertEqual(payload["trialDays"], 10)
        self.assertEqual((payload["adminName"], payload["adminEmail"]), ("Marcos Oliveira", "dono@x.com"))
        self.assertEqual(self.client.build_payload(_lead(owner_name=None))["adminName"], "Responsável Barbearia São José & Cia")

    def test_parses_success_and_sends_key(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["key"] = request.headers["X-Provisioning-Key"]
            seen["url"] = str(request.url)
            return httpx.Response(201, json={
                "created": True,
                "shop": {"id": "s1", "slug": "barbearia-sao-jose-cia", "url": "https://bs.test/barbearia-sao-jose-cia",
                         "trialEndsAt": "2026-10-29T13:00:00.000Z"},
                "setupUrl": "https://bs.test/barbearia-sao-jose-cia/login/recuperar?token=t",
            })

        async def go():
            async with self._transport(handler) as http:
                return await self.client.provision_trial(self.lead, http_client=http)

        trial = _run(go())
        self.assertEqual(seen, {"key": "k", "url": "https://bs.test/provisioning/trials"})
        self.assertTrue(trial.created)
        self.assertEqual(trial.shop_id, "s1")
        self.assertIn("token=t", trial.setup_url)

    def test_error_messages_are_surfaced(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"message": ["Informe um e-mail válido.", "slug inválido"]})

        async def go():
            async with self._transport(handler) as http:
                await self.client.provision_trial(self.lead, http_client=http)

        with self.assertRaisesRegex(ProvisioningError, "400.*Informe um e-mail válido.; slug inválido"):
            _run(go())

    def test_disabled_client_refuses(self):
        with self.assertRaisesRegex(ProvisioningError, "não configurada"):
            _run(BarbershopPlatformClient().provision_trial(self.lead))


class TestBlockReasons(unittest.TestCase):
    def test_reasons_in_priority_order(self):
        with mock.patch.object(platform_module, "barbershop_platform", FakePlatform(enabled=False)):
            self.assertIn("não configurada", trial_block_reason(_lead(email="a@b.com")))
        with mock.patch.object(platform_module, "barbershop_platform", FakePlatform()):
            self.assertIn("fictício", trial_block_reason(_lead(email="a@b.com", source=LeadSource.SYNTHETIC)))
            self.assertIn("e-mail", trial_block_reason(_lead()))
            self.assertIn("não ser contatado", trial_block_reason(_lead(email="a@b.com", status=LeadStatus.DO_NOT_CONTACT)))
            self.assertIsNone(trial_block_reason(_lead(email="a@b.com")))
        with mock.patch.object(platform_module, "barbershop_platform", FakePlatform(allow_synthetic=True)):
            self.assertIsNone(trial_block_reason(_lead(email="a@b.com", source=LeadSource.SYNTHETIC)))


class TrialApiCase(unittest.TestCase):
    def setUp(self):
        env = patch_auth_env({"BARBERSHOP_WEBHOOK_SECRET": BARBERSHOP_SECRET})
        env.start()
        self.addCleanup(env.stop)
        self.platform = FakePlatform()
        patcher = mock.patch.object(platform_module, "barbershop_platform", self.platform)
        patcher.start()
        self.addCleanup(patcher.stop)
        store.reset()
        self.client = TestClient(app)
        login(self.client)
        self.lead = _run(store.upsert_lead(_lead()))

    def _whatsapp(self, text: str) -> dict:
        res = self.client.post("/webhook/whatsapp", json={"phone": self.lead.phone, "message": text}, headers=WEBHOOK_HEADERS)
        self.assertEqual(res.status_code, 200)
        return res.json()


class TestConversationFlow(TrialApiCase):
    def test_auto_link_asks_email_then_provisions_and_sends_links(self):
        self.client.post("/api/settings/trial-mode", json={"trial_mode": "AUTO_LINK"})
        self.assertIn("e-mail", self._whatsapp("quero testar")["reply"])
        self.assertEqual(self.platform.calls, [])

        body = self._whatsapp("é dono@barbearia.com")
        lead = body["lead"]
        self.assertEqual(lead["status"], "TRIAL_STARTED")
        self.assertEqual(lead["trial_status"], "PROVISIONED")
        self.assertEqual(lead["trial_shop_id"], "shop-123")
        self.assertEqual(lead["slug"], "barbearia-sao-jose-cia-2")  # slug final devolvido pelo barber-shop
        self.assertIn("https://barber-shop.test/barbearia-sao-jose-cia-2", body["reply"])
        self.assertIn("token=abc", body["reply"])
        self.assertEqual(self.platform.calls[0].email, "dono@barbearia.com")

        saved = _run(store.get_lead(self.lead.id))
        self.assertEqual(saved.email, "dono@barbearia.com")
        self.assertIsNone(saved.next_touch_at)

        # Pedido repetido: não provisiona de novo, só reenvia o link.
        again = self._whatsapp("quero sim")
        self.assertEqual(again["langgraph_node"], "5.TrialActivator:AlreadyActive")
        self.assertEqual(len(self.platform.calls), 1)

    def test_provisioning_failure_goes_to_manual_queue(self):
        self.platform.fail_with = "barber-shop indisponível"
        self.client.post("/api/settings/trial-mode", json={"trial_mode": "AUTO_LINK"})
        body = self._whatsapp("bora, meu email é dono@barbearia.com")
        self.assertEqual(body["lead"]["status"], "INTERESTED")
        self.assertEqual(body["lead"]["langgraph_node"], "5.TrialActivator:PendingManual")
        self.assertIn("em instantes", body["reply"])

    def test_stand_by_keeps_lead_for_manual_release(self):
        body = self._whatsapp("tenho interesse, dono@barbearia.com")
        self.assertEqual(body["lead"]["status"], "INTERESTED")
        self.assertIn("VIP", body["reply"])
        self.assertEqual(self.platform.calls, [])


class TestManualActivation(TrialApiCase):
    def test_requires_email(self):
        res = self.client.post(f"/api/leads/{self.lead.id}/activate-trial", json={})
        self.assertEqual(res.status_code, 409)
        self.assertIn("e-mail", res.json()["detail"])

    def test_rejects_invalid_email(self):
        res = self.client.post(f"/api/leads/{self.lead.id}/activate-trial", json={"email": "não-é-email"})
        self.assertEqual(res.status_code, 422)

    def test_activates_and_sends_link_on_whatsapp(self):
        res = self.client.post(f"/api/leads/{self.lead.id}/activate-trial", json={"email": "Dono@Barbearia.com"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["delivery"]["delivered"])
        self.assertEqual(body["lead"]["status"], "TRIAL_STARTED")
        self.assertEqual(body["lead"]["email"], "dono@barbearia.com")
        sent = [i.message_text for i in _run(store.get_lead_interactions(self.lead.id)) if i.direction == "OUTBOUND"]
        self.assertIn("token=abc", sent[-1])

    def test_provisioning_error_is_502(self):
        self.platform.fail_with = "cota estourada"
        res = self.client.post(f"/api/leads/{self.lead.id}/activate-trial", json={"email": "dono@barbearia.com"})
        self.assertEqual(res.status_code, 502)

    def test_synthetic_lead_blocked(self):
        fake = _run(store.upsert_lead(_lead(phone="5577999000111", source=LeadSource.SYNTHETIC)))
        res = self.client.post(f"/api/leads/{fake.id}/activate-trial", json={"email": "dono@barbearia.com"})
        self.assertEqual(res.status_code, 409)
        self.assertIn("fictício", res.json()["detail"])


class TestBarbershopWebhook(TrialApiCase):
    def _post(self, event: str, *, secret: str = BARBERSHOP_SECRET, timestamp: int | None = None, lead_id=None):
        body = json.dumps({
            "id": f"evt-{uuid.uuid4()}",  # o barber-shop gera um id por aviso
            "event": event,
            "occurredAt": "2026-10-22T13:00:00.000Z",
            "shop": {"id": "shop-123", "slug": "barbearia-sao-jose-cia-2", "externalLeadId": lead_id or self.lead.id,
                     "subscriptionStatus": "TRIAL", "plan": "SQUAD", "trialEndsAt": "2026-10-29T13:00:00.000Z"},
        }).encode()
        ts = str(timestamp if timestamp is not None else int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Gerente-Barber-Timestamp": ts,
            "X-Gerente-Barber-Signature": f"sha256={barbershop_webhook.sign(secret, ts, body)}",
        }
        return self.client.post("/webhook/barbershop", content=body, headers=headers)

    def test_rejects_bad_signature_and_replay(self):
        self.assertEqual(self._post("trial.activated", secret="outro-segredo").status_code, 401)
        self.assertEqual(self._post("trial.activated", timestamp=int(time.time()) - 600).status_code, 401)
        res = self.client.post("/webhook/barbershop", content=b"{}", headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 401)

    def test_lifecycle_updates_lead_until_conversion(self):
        res = self._post("trial.activated")
        self.assertEqual(res.status_code, 200)
        lead = _run(store.get_lead(self.lead.id))
        self.assertEqual(lead.trial_status, TrialStatus.ACTIVATED)
        self.assertEqual(lead.slug, "barbearia-sao-jose-cia-2")
        self.assertTrue(lead.trial_ends_at.startswith("2026-10-29T13:00:00"))

        self._post("trial.expired")
        self.assertEqual(_run(store.get_lead(self.lead.id)).trial_status, TrialStatus.EXPIRED)

        self._post("subscription.activated")
        lead = _run(store.get_lead(self.lead.id))
        self.assertEqual((lead.trial_status, lead.status), (TrialStatus.CONVERTED, LeadStatus.CONVERTED))
        self.assertEqual(self.client.get("/api/metrics").json()["converted_count"], 1)

    def test_unknown_lead_or_event_is_ignored(self):
        self.assertEqual(self._post("trial.activated", lead_id="desconhecido").json()["status"], "ignored")
        self.assertEqual(self._post("shop.renamed").json()["status"], "ignored")

    def test_secret_missing_rejects_everything(self):
        with mock.patch.dict(os.environ, {"BARBERSHOP_WEBHOOK_SECRET": ""}):
            self.assertEqual(self._post("trial.activated").status_code, 401)


class TestStartupGuard(unittest.TestCase):
    def test_real_provider_with_integration_requires_strong_webhook_secret(self):
        strong = {
            "ADMIN_EMAIL": "dono@gerentebarber.com.br",
            "ADMIN_PASSWORD": "uma-senha-bem-forte-2026",
            "JWT_SECRET": "x" * 48,
            "WEBHOOK_SECRET": "y" * 48,
            "COOKIE_SECURE": "true",
            "BARBERSHOP_WEBHOOK_SECRET": "curto",
        }
        with mock.patch.dict(os.environ, strong), \
                mock.patch.object(evolution_client, "provider", "EVOLUTION"), \
                mock.patch.object(platform_module, "barbershop_platform", FakePlatform()):
            with self.assertRaisesRegex(RuntimeError, "BARBERSHOP_WEBHOOK_SECRET"):
                validate_startup_config()
            with mock.patch.dict(os.environ, {"BARBERSHOP_WEBHOOK_SECRET": "z" * 40}):
                validate_startup_config()


if __name__ == "__main__":
    unittest.main()
