import unittest
from backend.app.models import (
    LeadStatus,
    TechMaturity,
    BarbershopLead,
    generate_shop_slug,
    recommend_plan,
)


class TestModels(unittest.TestCase):
    def test_slug_generation_handles_accents_and_special_chars(self):
        self.assertEqual(generate_shop_slug("Barbearia Elite"), "barbearia-elite")
        self.assertEqual(generate_shop_slug("Studio São José & Cia!"), "studio-sao-jose-cia")

    def test_recommend_plan_tiers(self):
        # Mesma tabela do barber-shop: limite de profissionais ativos de cada plano.
        expected = {
            1: ("solo", "Plano Solo", "R$ 35,00"),
            2: ("essencial", "Plano Essencial", "R$ 89,90"),
            3: ("essencial", "Plano Essencial", "R$ 89,90"),
            4: ("profissional", "Plano Profissional", "R$ 149,90"),
            6: ("profissional", "Plano Profissional", "R$ 149,90"),
            7: ("premium", "Plano Premium", "R$ 219,90"),
            10: ("premium", "Plano Premium", "R$ 219,90"),
            12: ("rede", "Plano Rede", "R$ 219,90"),
        }
        for chairs, (code, name, price) in expected.items():
            plan = recommend_plan(chairs)
            self.assertEqual((plan["plan_code"], plan["plan_name"], plan["monthly_anchor"]), (code, name, price), chairs)
        self.assertIn("19,90 por profissional extra", recommend_plan(12)["price_range"])
        self.assertEqual(recommend_plan(0)["plan_code"], "solo")

    def test_lead_model_defaults(self):
        lead = BarbershopLead(
            shop_name="Barbearia Elite",
            phone="5577999112233",
            city="Vitória da Conquista",
            state="BA",
        )
        self.assertEqual(lead.status, LeadStatus.RAW)
        self.assertEqual(lead.current_system, TechMaturity.UNKNOWN)
        self.assertEqual(lead.slug, "barbearia-elite")
        self.assertEqual(lead.preview_url, "https://barber-shop.horizonaj.com.br/barbearia-elite")


if __name__ == "__main__":
    unittest.main()
