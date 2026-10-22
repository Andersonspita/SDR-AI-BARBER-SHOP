import unittest
from backend.app.models import BarbershopLead, TechMaturity
from backend.app.agents.copywriter import generate_outbound_copy, FEW_SHOT_PROMPTS
from backend.app.agents.cadence import calculate_antiban_schedule


class TestCopyAndCadence(unittest.TestCase):
    def test_solo_manual_whatsapp_hook(self):
        lead = BarbershopLead(
            shop_name="Barbearia Dom Bigode",
            phone="5577999110001",
            city="Vitória da Conquista",
            state="BA",
            estimated_barbers=1,
            current_system=TechMaturity.MANUAL_WHATSAPP,
        )
        copy = generate_outbound_copy(lead, touchpoint="D0")
        self.assertIn("Dom Bigode", copy)
        self.assertTrue("Whats" in copy or "corta" in copy or "máquina" in copy)
        self.assertNotIn("sinergia", copy.lower())
        self.assertNotIn("roi", copy.lower())
        self.assertLessEqual(len(copy.splitlines()), 5)

    def test_team_barbers_commission_hook(self):
        lead = BarbershopLead(
            shop_name="Studio Corte Fino",
            phone="5577999110002",
            city="Vitória da Conquista",
            state="BA",
            estimated_barbers=4,
            current_system=TechMaturity.MANUAL_WHATSAPP,
        )
        copy = generate_outbound_copy(lead, touchpoint="D0")
        self.assertIn("comissão", copy.lower())
        self.assertIn("lápis", copy.lower())

    def test_competitor_migration_hook(self):
        lead = BarbershopLead(
            shop_name="Viking Barber Club",
            phone="5577999110003",
            city="Vitória da Conquista",
            state="BA",
            estimated_barbers=6,
            current_system=TechMaturity.COMPETITOR,
            competitor_name="AppBarber",
        )
        copy = generate_outbound_copy(lead, touchpoint="D0")
        self.assertIn("AppBarber", copy)
        self.assertIn("mensalidade", copy.lower())

    def test_d5_trial_offer_includes_shop_name(self):
        lead = BarbershopLead(
            shop_name="Barbearia Elite",
            phone="5577999110004",
            city="Vitória da Conquista",
            state="BA",
            estimated_barbers=2,
        )
        copy = generate_outbound_copy(lead, touchpoint="D5")
        self.assertIn("7 dias", copy)
        self.assertIn("Barbearia Elite", copy)

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
