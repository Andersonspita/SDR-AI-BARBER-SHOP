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
        solo = recommend_plan(1)
        self.assertEqual(solo["plan_name"], "Plano Solo")
        self.assertEqual(solo["price_range"], "R$ 49,90 a R$ 59,90/mês")

        squad = recommend_plan(3)
        self.assertEqual(squad["plan_name"], "Plano Duo/Squad")
        self.assertEqual(squad["price_range"], "R$ 99,90 a R$ 129,90/mês")

        pro = recommend_plan(6)
        self.assertEqual(pro["plan_name"], "Plano Club/Pro")
        self.assertEqual(pro["price_range"], "R$ 179,90 a R$ 219,90/mês")

        empire = recommend_plan(12)
        self.assertEqual(empire["plan_name"], "Plano Empire")
        self.assertEqual(empire["price_range"], "R$ 299,90 a R$ 389,90/mês")

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
