"""Avisos de teste e de cobrança do barber-shop saindo pela conversa do SDR, e as respostas do dono."""
import json
import time
import unittest
import uuid
from backend.app.models import LeadStatus, TrialStatus
from backend.app.store import store
from backend.app.services import barbershop_webhook
from backend.tests.helpers import WEBHOOK_HEADERS
from backend.tests.test_trial_integration import BARBERSHOP_SECRET, TrialApiCase, _run

SHOP_URL = "https://barber-shop.test/barbearia-sao-jose-cia-2"


class NoticeCase(TrialApiCase):
    def setUp(self):
        super().setUp()
        # Cliente com barbearia no barber-shop (teste já liberado e acessado).
        self.lead.trial_shop_id = "shop-123"
        self.lead.trial_status = TrialStatus.ACTIVATED
        self.lead.status = LeadStatus.TRIAL_STARTED
        self.lead.email = "dono@barbearia.com"
        _run(store.save_lead(self.lead))

    def notify(self, event: str, *, event_id: str | None = None, **shop_overrides) -> dict:
        shop = {
            "id": "shop-123",
            "slug": "barbearia-sao-jose-cia-2",
            "name": "Barbearia São José & Cia",
            "externalLeadId": self.lead.id,
            "subscriptionStatus": "TRIAL",
            "plan": "SQUAD",
            "trialEndsAt": "2026-10-29T13:00:00.000Z",
            "url": SHOP_URL,
            "subscribeUrl": f"{SHOP_URL}/dashboard/assinatura",
            "invoiceUrl": None,
            "suspendsAt": None,
            **shop_overrides,
        }
        body = json.dumps({"id": event_id or f"evt-{uuid.uuid4()}", "event": event, "shop": shop}).encode()
        ts = str(int(time.time()))
        res = self.client.post(
            "/webhook/barbershop",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Gerente-Barber-Timestamp": ts,
                "X-Gerente-Barber-Signature": f"sha256={barbershop_webhook.sign(BARBERSHOP_SECRET, ts, body)}",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def outbound(self) -> list[str]:
        return [i.message_text for i in _run(store.get_lead_interactions(self.lead.id)) if i.direction == "OUTBOUND"]

    def owner_says(self, text: str) -> dict:
        res = self.client.post("/webhook/whatsapp", json={"phone": self.lead.phone, "message": text}, headers=WEBHOOK_HEADERS)
        return res.json()

    def saved(self):
        return _run(store.get_lead(self.lead.id))


class TestTrialNotices(NoticeCase):
    def test_expiring_sends_subscribe_link_with_the_trial_day(self):
        # O barber-shop manda o fim do teste como dia puro (meia-noite UTC).
        self.notify("trial.expiring", trialEndsAt="2026-10-29T00:00:00.000Z")
        [message] = self.outbound()
        self.assertIn(f"{SHOP_URL}/dashboard/assinatura", message)
        self.assertIn("acaba em 29/10.", message)  # não 28/10 às 21:00
        self.assertEqual(self.saved().trial_status, TrialStatus.EXPIRING)

    def test_timestamp_with_time_is_shown_in_brasilia(self):
        self.notify("trial.expiring")
        self.assertIn("29/10 às 10:00", self.outbound()[-1])  # 13:00 UTC = 10:00 em Brasília

    def test_resent_event_is_not_sent_twice(self):
        self.notify("trial.expiring", event_id="evt-repetido")
        again = self.notify("trial.expiring", event_id="evt-repetido")
        self.assertEqual(again["status"], "duplicate")
        self.assertEqual(len(self.outbound()), 1)

    def test_owner_in_trial_asking_to_subscribe_gets_the_link(self):
        self.notify("trial.expiring")
        reply = self.owner_says("quero assinar")
        self.assertEqual(reply["langgraph_node"], "5.Billing:SubscribeLink")
        self.assertIn("/dashboard/assinatura", reply["reply"])

    def test_expired_then_owner_wants_to_pay_without_invoice_flags_the_panel(self):
        self.notify("trial.expired", subscriptionStatus="PAST_DUE", suspendsAt="2026-11-06T00:00:00.000Z")
        message = self.outbound()[-1]
        self.assertIn("terminou", message)
        self.assertIn("agendamento online fica pausado", message)
        self.assertIn("06/11", message)
        self.assertNotIn("saiu do ar", message)

        reply = self.owner_says("quero continuar sim")
        self.assertEqual(reply["langgraph_node"], "5.Billing:NeedsCharge")
        self.assertIn("gerar a cobrança", reply["reply"])
        self.assertTrue(self.saved().needs_billing_help)
        self.assertEqual(self.client.get("/api/metrics").json()["billing_attention_count"], 1)

        # A plataforma gera a cobrança: o link chega pela conversa e o alerta some.
        self.notify("subscription.invoice_created", subscriptionStatus="PAST_DUE", invoiceUrl="https://asaas.test/i/1")
        self.assertIn("https://asaas.test/i/1", self.outbound()[-1])
        self.assertFalse(self.saved().needs_billing_help)

        reply = self.owner_says("manda o link de novo")
        self.assertEqual(reply["langgraph_node"], "5.Billing:InvoiceSent")
        self.assertIn("https://asaas.test/i/1", reply["reply"])


class TestBillingNotices(NoticeCase):
    def test_activation_converts_and_confirms(self):
        self.notify("subscription.activated", subscriptionStatus="ACTIVE", plan="PRO")
        lead = self.saved()
        self.assertEqual((lead.status, lead.trial_status, lead.plan_code), (LeadStatus.CONVERTED, TrialStatus.CONVERTED, "PRO"))
        self.assertIn("Pagamento confirmado", self.outbound()[-1])

    def test_past_due_suspended_and_reactivated(self):
        self.notify("subscription.activated", subscriptionStatus="ACTIVE")
        self.notify(
            "subscription.past_due",
            subscriptionStatus="PAST_DUE",
            invoiceUrl="https://asaas.test/i/2",
            suspendsAt="2026-11-05T15:30:00.000Z",
        )
        message = self.outbound()[-1]
        self.assertIn("https://asaas.test/i/2", message)
        self.assertIn("05/11 às 12:30", message)
        self.assertEqual(self.saved().subscription_status, "PAST_DUE")

        reply = self.owner_says("vou pagar hoje")
        self.assertIn("https://asaas.test/i/2", reply["reply"])

        self.notify("subscription.suspended", subscriptionStatus="BLOCKED", invoiceUrl="https://asaas.test/i/2")
        self.assertIn("agendamento online da Barbearia São José & Cia foi pausado", self.outbound()[-1])
        self.assertEqual(self.client.get("/api/metrics").json()["billing_attention_count"], 1)

        reply = self.owner_says("como faço pra voltar?")
        self.assertEqual(reply["langgraph_node"], "5.Billing:InvoiceSent")

        self.notify("subscription.reactivated", subscriptionStatus="ACTIVE")
        lead = self.saved()
        self.assertEqual(lead.subscription_status, "ACTIVE")
        self.assertIsNone(lead.invoice_url)
        self.assertIn("em dia", self.outbound()[-1])

    def test_paying_customer_does_not_get_the_sales_pitch(self):
        self.notify("subscription.activated", subscriptionStatus="ACTIVE")
        reply = self.owner_says("valeu, tá funcionando")
        self.assertEqual(reply["langgraph_node"], "5.Billing:Support")
        self.assertNotIn("te mostro", reply["reply"])

    def test_unsubscribed_owner_gets_state_but_no_message(self):
        self.lead.status = LeadStatus.DO_NOT_CONTACT
        _run(store.save_lead(self.lead))
        result = self.notify("subscription.past_due", subscriptionStatus="PAST_DUE", invoiceUrl="https://asaas.test/i/3")
        self.assertIsNone(result["delivery"])
        self.assertEqual(self.outbound(), [])
        self.assertEqual(self.saved().subscription_status, "PAST_DUE")

    def test_activated_trial_event_sends_nothing(self):
        result = self.notify("trial.activated")
        self.assertIsNone(result["delivery"])
        self.assertEqual(self.outbound(), [])


if __name__ == "__main__":
    unittest.main()
