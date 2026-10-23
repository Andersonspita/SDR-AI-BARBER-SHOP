import asyncio
import unittest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.models import BarbershopLead, LeadStatus, TechMaturity
from backend.app.store import store
from backend.app.agents.graph import classify_and_run_graph, classify_intent
from backend.app.agents.objection_rag import detect_objections
from backend.tests.helpers import WEBHOOK_HEADERS, login, patch_auth_env


def _make_lead(**overrides) -> BarbershopLead:
    data = dict(
        shop_name="Barbearia São José & Cia",
        phone="5577999123456",
        city="Vitória da Conquista",
        state="BA",
        estimated_barbers=1,
        current_system=TechMaturity.MANUAL_WHATSAPP,
        status=LeadStatus.CONTACTED_D1,
    )
    data.update(overrides)
    return BarbershopLead(**data)


class TestObjectionRag(unittest.TestCase):
    def test_detects_all_four_classic_objections(self):
        self.assertEqual(detect_objections("Não tenho tempo pra cadastrar cliente"), ["SEM_TEMPO"])
        self.assertEqual(detect_objections("Meus clientes são velhos, só ligam"), ["CLIENTES_VELHOS"])
        self.assertEqual(detect_objections("Já uso caderno e funciona bem"), ["CADERNO"])
        self.assertEqual(detect_objections("Já uso o Trinks aqui"), ["CONCORRENTE"])

    def test_detects_multiple_objections_in_same_message(self):
        found = detect_objections("uso caderno e meus clientes são velhos")
        self.assertIn("CADERNO", found)
        self.assertIn("CLIENTES_VELHOS", found)


class TestGraph(unittest.TestCase):
    def test_unsubscribe_variants_move_to_do_not_contact(self):
        for text in ("para de mandar mensagem", "sair", "Não quero", "me tira dessa lista"):
            lead = _make_lead()
            result = classify_and_run_graph(lead, text)
            self.assertEqual(result["intent"], "UNSUBSCRIBE", text)
            self.assertEqual(lead.status, LeadStatus.DO_NOT_CONTACT)

    def test_unsubscribe_wins_over_interest_keywords(self):
        self.assertEqual(classify_intent("não quero, para de mandar"), "UNSUBSCRIBE")

    def test_do_not_contact_lead_gets_no_reply(self):
        lead = _make_lead(status=LeadStatus.DO_NOT_CONTACT)
        result = classify_and_run_graph(lead, "quero sim, manda o link")
        self.assertIsNone(result["reply"])
        self.assertEqual(lead.status, LeadStatus.DO_NOT_CONTACT)

    def test_interested_without_email_asks_for_it_in_both_modes(self):
        for mode in ("STAND_BY", "AUTO_LINK"):
            lead = _make_lead()
            result = classify_and_run_graph(lead, "Tenho interesse, como funciona?", trial_mode=mode)
            self.assertEqual(result["intent"], "INTERESTED")
            self.assertEqual(lead.status, LeadStatus.INTERESTED)
            self.assertEqual(result["langgraph_node"], "5.TrialActivator:AwaitingEmail")
            self.assertIn("e-mail", result["reply"])
            self.assertIsNone(result["action"])

    def test_interested_stand_by_with_email_queues_vip_without_link(self):
        lead = _make_lead(email="dono@barbearia.com")
        result = classify_and_run_graph(lead, "Tenho interesse", trial_mode="STAND_BY")
        self.assertEqual(lead.status, LeadStatus.INTERESTED)
        self.assertNotIn(lead.preview_url, result["reply"])
        self.assertIn("VIP", result["reply"])
        self.assertIsNone(result["action"])

    def test_email_reply_in_auto_link_requests_provisioning(self):
        lead = _make_lead()
        result = classify_and_run_graph(lead, "pode ser, é Marcos.Dono@Gmail.com", trial_mode="AUTO_LINK")
        self.assertEqual(lead.email, "marcos.dono@gmail.com")
        self.assertEqual(result["intent"], "INTERESTED")
        self.assertEqual(result["action"], "PROVISION_TRIAL")
        self.assertIsNone(result["reply"])

    def test_unsubscribe_with_email_does_not_capture_email(self):
        lead = _make_lead()
        result = classify_and_run_graph(lead, "para de mandar mensagem pro dono@x.com", trial_mode="AUTO_LINK")
        self.assertEqual(result["intent"], "UNSUBSCRIBE")
        self.assertIsNone(lead.email)

    def test_multiple_objections_reply_focuses_on_booking_without_app(self):
        lead = _make_lead()
        result = classify_and_run_graph(lead, "uso caderno e meus clientes são velhos")
        self.assertEqual(result["intent"], "OBJECTION_RAISED")
        self.assertEqual(lead.status, LeadStatus.OBJECTION_RAISED)
        self.assertIn("sem baixar", result["reply"].lower())
        self.assertNotIn("roi", result["reply"].lower())


class TestApi(unittest.TestCase):
    def setUp(self):
        env = patch_auth_env()
        env.start()
        self.addCleanup(env.stop)
        store.reset()
        self.client = TestClient(app)
        login(self.client)
        self.lead = asyncio.run(store.upsert_lead(_make_lead()))

    def _webhook(self, text: str, phone: str = "5577999123456"):
        return self.client.post("/webhook/whatsapp", json={"phone": phone, "message": text}, headers=WEBHOOK_HEADERS)

    def test_webhook_interested_stand_by_default(self):
        res = self._webhook("tenho interesse")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["intent"], "INTERESTED")
        self.assertEqual(body["lead"]["status"], "INTERESTED")
        self.assertNotIn(self.lead.preview_url, body["reply"])

    def test_webhook_auto_link_without_integration_falls_back_to_manual(self):
        res = self.client.post("/api/settings/trial-mode", json={"trial_mode": "AUTO_LINK"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("e-mail", self._webhook("quero testar").json()["reply"])
        body = self._webhook("dono@barbearia.com").json()
        # Sem BARBERSHOP_API_URL o teste não pode ser criado: fica na fila de liberação manual.
        self.assertEqual(body["lead"]["status"], "INTERESTED")
        self.assertEqual(body["lead"]["langgraph_node"], "5.TrialActivator:PendingManual")
        self.assertIn("dono@barbearia.com", body["reply"])

    def test_trial_mode_rejects_invalid_value(self):
        res = self.client.post("/api/settings/trial-mode", json={"trial_mode": "WHATEVER"})
        self.assertEqual(res.status_code, 422)

    def test_webhook_each_objection_is_recorded(self):
        cases = {
            "não tenho tempo de cadastrar": "SEM_TEMPO",
            "meus clientes são velhos": "CLIENTES_VELHOS",
            "já uso caderno": "CADERNO",
            "já uso o booksy": "CONCORRENTE",
        }
        for text, objection in cases.items():
            body = self._webhook(text).json()
            self.assertEqual(body["intent"], "OBJECTION_RAISED", text)
            self.assertIn(objection, body["objection_types"])
            self.assertEqual(body["lead"]["last_objection_type"], objection)

    def test_webhook_unsubscribe_blocks_further_d0(self):
        body = self._webhook("para de mandar mensagem").json()
        self.assertEqual(body["lead"]["status"], "DO_NOT_CONTACT")
        res = self.client.post(f"/api/leads/{self.lead.id}/send-d0")
        self.assertEqual(res.status_code, 409)

    def test_webhook_accepts_evolution_payload_and_ignores_from_me(self):
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "5577999123456@s.whatsapp.net", "fromMe": False},
                "message": {"conversation": "tenho interesse"},
            },
        }
        body = self.client.post("/webhook/whatsapp", json=payload, headers=WEBHOOK_HEADERS).json()
        self.assertEqual(body["intent"], "INTERESTED")

        payload["data"]["key"]["fromMe"] = True
        body = self.client.post("/webhook/whatsapp", json=payload, headers=WEBHOOK_HEADERS).json()
        self.assertEqual(body["status"], "ignored")

    def test_webhook_lid_address_uses_the_alternate_phone(self):
        def evolution(key: dict) -> dict:
            payload = {"event": "messages.upsert", "data": {"key": {"fromMe": False, **key}, "message": {"conversation": "tenho interesse"}}}
            return self.client.post("/webhook/whatsapp", json=payload, headers=WEBHOOK_HEADERS).json()

        # Endereçamento novo do WhatsApp: o número vem à parte.
        body = evolution({"remoteJid": "182736455463728@lid", "remoteJidAlt": "5577999123456@s.whatsapp.net"})
        self.assertEqual(body["intent"], "INTERESTED")
        # Sem o número, o id do @lid nunca é tratado como telefone.
        self.assertEqual(evolution({"remoteJid": "182736455463728@lid"})["status"], "ignored")
        # Mensagem de grupo não é conversa com o lead, mesmo vinda do número dele.
        self.assertEqual(evolution({"remoteJid": "120363000000000000@g.us", "participant": "5577999123456@s.whatsapp.net"})["status"], "ignored")

    def test_webhook_from_another_instance_is_ignored(self):
        # Ex.: cliente de barbearia respondendo ao número de agendamentos, na mesma Evolution.
        payload = {
            "event": "messages.upsert",
            "instance": "gerente-barber-agendamentos",
            "data": {"key": {"remoteJid": "5577999123456@s.whatsapp.net", "fromMe": False}, "message": {"conversation": "tenho interesse"}},
        }
        body = self.client.post("/webhook/whatsapp", json=payload, headers=WEBHOOK_HEADERS).json()
        self.assertEqual(body["status"], "ignored")
        self.assertIn("outro número", body["reason"])

    def test_webhook_unknown_phone_is_ignored(self):
        body = self._webhook("oi", phone="5511900000000").json()
        self.assertEqual(body["status"], "ignored")

    def test_scrape_list_send_d0_and_metrics(self):
        res = self.client.post("/api/leads/scrape", json={"state": "BA", "city": "Vitória da Conquista"})
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.json()["leads"]), 0)

        leads = self.client.get("/api/leads", params={"state": "BA"}).json()["leads"]
        target = next(l for l in leads if l["id"] != self.lead.id)
        res = self.client.post(f"/api/leads/{target['id']}/send-d0")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["lead"]["status"], "CONTACTED_D1")

        metrics = self.client.get("/api/metrics").json()
        self.assertEqual(metrics["daily_sent_count"], 1)
        self.assertEqual(metrics["trial_mode"], "STAND_BY")

    def test_send_d0_unknown_lead_returns_404(self):
        self.assertEqual(self.client.post("/api/leads/nope/send-d0").status_code, 404)

    def test_send_d0_respects_daily_limit(self):
        store.daily_limit = 0
        res = self.client.post(f"/api/leads/{self.lead.id}/send-d0")
        self.assertEqual(res.status_code, 429)


if __name__ == "__main__":
    unittest.main()


class TestReplyToTheColdMessage(unittest.TestCase):
    """A D0 fecha com uma pergunta de sim ou não e uma saída; as respostas curtas têm de ser entendidas."""

    def test_short_yes_answers_are_interest(self):
        for text in ("pode sim", "Pode", "opa, pode deixar", "beleza, pode ser", "Show", "fechou"):
            self.assertEqual(classify_intent(text), "INTERESTED", text)

    def test_taking_the_offered_way_out_unsubscribes(self):
        for text in ("não faz sentido", "não mande mais", "pode parar", "sem interesse, obrigado"):
            self.assertEqual(classify_intent(text), "UNSUBSCRIBE", text)

    def test_not_now_is_not_a_yes(self):
        for text in ("não pode ser agora", "agora não"):
            self.assertEqual(classify_intent(text), "NEUTRAL", text)
