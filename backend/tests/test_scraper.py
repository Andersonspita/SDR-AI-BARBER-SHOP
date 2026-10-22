import unittest
from backend.app.models import TechMaturity, LeadStatus
from backend.app.agents.scraper import detect_bio_system, scrape_barbershops_by_location


class TestScraperAndBioDetector(unittest.TestCase):
    def test_detect_manual_whatsapp_links(self):
        res1 = detect_bio_system("https://wa.me/5577999112233?text=Oi")
        self.assertEqual(res1["current_system"], TechMaturity.MANUAL_WHATSAPP)
        self.assertEqual(res1["priority"], "ALTA")
        self.assertIsNone(res1["competitor_name"])

        res2 = detect_bio_system("https://api.whatsapp.com/send?phone=5511988887777")
        self.assertEqual(res2["current_system"], TechMaturity.MANUAL_WHATSAPP)
        self.assertEqual(res2["priority"], "ALTA")

    def test_detect_known_competitors(self):
        for url, expected_name in [
            ("https://sites.appbarber.com.br/vikingbarber", "AppBarber"),
            ("https://booksy.com/pt-br/12345_barbearia", "Booksy"),
            ("https://www.trinks.com/barbearia-imperial", "Trinks"),
            ("https://gendo.app/corte-fino", "Gendo"),
        ]:
            res = detect_bio_system(url)
            self.assertEqual(res["current_system"], TechMaturity.COMPETITOR)
            self.assertEqual(res["competitor_name"], expected_name)
            self.assertEqual(res["priority"], "MIGRACAO")

    def test_detect_no_link_is_medium_priority(self):
        res = detect_bio_system(None)
        self.assertEqual(res["current_system"], TechMaturity.UNKNOWN)
        self.assertEqual(res["priority"], "MEDIA")

    def test_scrape_barbershops_by_state_and_city(self):
        leads = scrape_barbershops_by_location(
            state="BA", city="Vitória da Conquista", neighborhood="Candeias"
        )
        self.assertGreaterEqual(len(leads), 4)
        for lead in leads:
            self.assertEqual(lead.state, "BA")
            self.assertEqual(lead.city, "Vitória da Conquista")
            self.assertEqual(lead.status, LeadStatus.ENRICHED)
            self.assertTrue(lead.slug)
            self.assertTrue(lead.preview_url.startswith("https://barber-shop.horizonaj.com.br/"))


if __name__ == "__main__":
    unittest.main()
