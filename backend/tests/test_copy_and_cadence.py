import os
import unittest
from unittest import mock
from backend.app.models import BarbershopLead, LeadSource, TechMaturity
from backend.app.agents.copywriter import generate_outbound_copy, FEW_SHOT_PROMPTS
from backend.app.agents.cadence import calculate_antiban_schedule


def _lead(**overrides) -> BarbershopLead:
    data = dict(
        shop_name="Barbearia Dom Bigode",
        phone="5577999110001",
        city="Vitória da Conquista",
        state="BA",
        estimated_barbers=1,
        current_system=TechMaturity.UNKNOWN,
        source=LeadSource.APIFY_GOOGLE_MAPS,
    )
    data.update(overrides)
    return BarbershopLead(**data)


class TestCopyAndCadence(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"SDR_SENDER_NAME": "Anderson"})
        env.start()
        self.addCleanup(env.stop)

    def assert_cold_message_rules(self, copy: str):
        """Toda D0: diz quem fala, pergunta, oferece a saída, sem jargão e curta."""
        self.assertIn("Aqui é o Anderson, do Gerente Barber.", copy)
        self.assertIn("?", copy)
        self.assertIn("me avisa que eu não mando mais", copy)
        self.assertNotIn("sinergia", copy.lower())
        self.assertNotIn("roi", copy.lower().split())
        self.assertLessEqual(len(copy.splitlines()), 4)

    def test_lead_from_maps_without_link_makes_no_claim_about_how_they_book(self):
        copy = generate_outbound_copy(_lead(), touchpoint="D0")
        self.assert_cold_message_rules(copy)
        self.assertIn("Achei a Barbearia Dom Bigode no Google Maps.", copy)
        self.assertNotIn("Vi ", copy)  # nada foi visto: não afirma como ela agenda
        self.assertIn("7 dias grátis", copy)

    def test_whatsapp_link_in_bio_is_mentioned(self):
        copy = generate_outbound_copy(_lead(current_system=TechMaturity.MANUAL_WHATSAPP), touchpoint="D0")
        self.assert_cold_message_rules(copy)
        self.assertIn("Vi que o agendamento de vocês é pelo WhatsApp.", copy)

    def test_imported_lead_does_not_claim_google_maps(self):
        copy = generate_outbound_copy(_lead(source=LeadSource.CSV_IMPORT), touchpoint="D0")
        self.assert_cold_message_rules(copy)
        self.assertNotIn("Google Maps", copy)
        self.assertIn("barbearias de Vitória da Conquista", copy)

    def test_team_barbers_commission_hook(self):
        copy = generate_outbound_copy(_lead(estimated_barbers=4), touchpoint="D0")
        self.assert_cold_message_rules(copy)
        self.assertIn("comissão", copy.lower())

    def test_competitor_migration_hook_uses_real_price(self):
        copy = generate_outbound_copy(
            _lead(estimated_barbers=6, current_system=TechMaturity.COMPETITOR, competitor_name="AppBarber"),
            touchpoint="D0",
        )
        self.assert_cold_message_rules(copy)
        self.assertIn("AppBarber", copy)
        self.assertIn("R$ 149,90", copy)  # Profissional, que comporta 6
        self.assertNotIn("reduzir", copy.lower())  # não promete economia que não dá pra garantir

    def test_without_sender_name_still_identifies_the_company(self):
        with mock.patch.dict(os.environ, {"SDR_SENDER_NAME": ""}):
            copy = generate_outbound_copy(_lead(), touchpoint="D0")
        self.assertIn("Aqui é do Gerente Barber.", copy)

    def test_follow_ups_offer_trial_without_claiming_a_page_already_exists(self):
        d2 = generate_outbound_copy(_lead(), touchpoint="D2")
        self.assertIn("me avisa que eu não mando mais", d2)
        d5 = generate_outbound_copy(_lead(shop_name="Barbearia Elite"), touchpoint="D5")
        self.assertIn("7 dias", d5)
        self.assertIn("Barbearia Elite", d5)
        self.assertIn("Última mensagem", d5)
        self.assertNotIn("Deixei", d5)

    def test_antiban_schedule_respects_delay_bounds_and_daily_limit(self):
        leads = [
            BarbershopLead(
                shop_name=f"Barbearia {i}",
                phone=f"55779990000{i:02d}",
                city="Vitória da Conquista",
                state="BA",
            )
            for i in range(10)
        ]
        schedule = calculate_antiban_schedule(leads, daily_limit=5)
        self.assertEqual(len(schedule), 5)
        for item in schedule:
            self.assertGreaterEqual(item["delay_seconds"], 45)
            self.assertLessEqual(item["delay_seconds"], 180)
            self.assertIn(item["touchpoint"], ("D0", "D2", "D5"))


if __name__ == "__main__":
    unittest.main()
