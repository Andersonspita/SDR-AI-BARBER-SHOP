"""Fontes de lead além do Google Places: busca no Google Maps pelo Apify e planilha CSV."""
import asyncio
import json
import unittest
from unittest import mock
import httpx
from fastapi.testclient import TestClient
from backend.app.agents import apify_maps
from backend.app.agents.scraper import ScraperError, br_phone_digits
from backend.app.main import app
from backend.app.models import LeadSource, TechMaturity
from backend.app.services.lead_import import ImportFormatError, parse_leads_csv
from backend.app.store import store
from backend.tests.helpers import login, patch_auth_env

APIFY_ITEMS = [
    {
        "title": "Barbearia do Zé",
        "categoryName": "Barbearia",
        "phoneUnformatted": "+5577999112233",
        "website": "https://booksy.com/pt-br/123_barbearia-do-ze",
    },
    {"title": "Studio Navalha", "categories": ["Barbearia", "Salão de beleza"], "phone": "(77) 3422-1100"},
    {"title": "Salão Bela Vista", "categoryName": "Salão de beleza", "phoneUnformatted": "+5577988887777"},
    {"title": "Barbearia Fechada", "categoryName": "Barbearia", "phoneUnformatted": "+5577977776666", "permanentlyClosed": True},
    {"title": "Barbearia Sem Fone", "categoryName": "Barbearia"},
]


def apify_transport(run_status: str = "SUCCEEDED", calls: list | None = None) -> httpx.MockTransport:
    """API do Apify de mentira: dispara a execução, informa a situação e devolve os resultados."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.headers.get("Authorization") != "Bearer token-de-teste":
            return httpx.Response(401, json={"error": {"message": "invalid token"}})
        path = request.url.path
        if request.method == "POST" and path.endswith("/acts/compass~crawler-google-places/runs"):
            return httpx.Response(201, json={"data": {"id": "RunAbc12345", "status": "READY"}})
        if path.endswith("/actor-runs/RunAbc12345"):
            return httpx.Response(200, json={"data": {"id": "RunAbc12345", "status": run_status, "defaultDatasetId": "DsXyz"}})
        if path.endswith("/datasets/DsXyz/items"):
            return httpx.Response(200, json=APIFY_ITEMS)
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.MockTransport(handler)


class TestPhoneNormalization(unittest.TestCase):
    def test_accepts_brazilian_formats(self):
        for raw in ("+55 77 99911-2233", "(77) 99911-2233", "077 99911-2233", "5577999112233"):
            self.assertEqual(br_phone_digits(raw), "5577999112233", raw)
        self.assertEqual(br_phone_digits("(77) 3422-1100"), "557734221100")

    def test_rejects_incomplete_and_foreign(self):
        for raw in ("99911-2233", "+1 718 356 5168", "", None, "(00) 99911-2233"):
            self.assertIsNone(br_phone_digits(raw), raw)


class TestApifyParsing(unittest.TestCase):
    def test_keeps_only_open_barbershops_with_phone(self):
        leads = apify_maps.leads_from_items(APIFY_ITEMS, "ba", "Vitória da Conquista", None)
        self.assertEqual([lead.shop_name for lead in leads], ["Barbearia do Zé", "Studio Navalha"])
        self.assertEqual(leads[0].phone, "5577999112233")
        self.assertEqual(leads[0].state, "BA")
        self.assertEqual(leads[0].source, LeadSource.APIFY_GOOGLE_MAPS)
        self.assertFalse(leads[0].is_synthetic)
        # Site no Booksy: já usa concorrente.
        self.assertEqual(leads[0].current_system, TechMaturity.COMPETITOR)

    def test_actor_input_is_cheap_and_located(self):
        actor_input = apify_maps.build_actor_input("BA", "Salvador", "Barra", 20)
        self.assertEqual(actor_input["locationQuery"], "Barra, Salvador, BA, Brasil")
        self.assertEqual(actor_input["maxCrawledPlacesPerSearch"], 20)
        self.assertFalse(actor_input["scrapePlaceDetailPage"])
        self.assertFalse(actor_input["scrapeContacts"])


class TestApifyFlow(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_start_then_fetch(self):
        calls: list = []

        async def scenario():
            async with httpx.AsyncClient(transport=apify_transport(calls=calls)) as client:
                run_id = await apify_maps.start_search("BA", "Salvador", None, token="token-de-teste", client=client)
                result = await apify_maps.fetch_search(run_id, "BA", "Salvador", None, token="token-de-teste", client=client)
                return run_id, result

        run_id, result = self.run_async(scenario())
        self.assertEqual(run_id, "RunAbc12345")
        self.assertEqual(result.status, "done")
        self.assertEqual(len(result.leads), 2)
        # O token vai no cabeçalho, nunca na URL; e o teto de itens limita a cobrança.
        self.assertNotIn("token", str(calls[0].url))
        self.assertEqual(calls[0].url.params["maxItems"], "20")
        self.assertEqual(json.loads(calls[0].content)["searchStringsArray"], ["barbearia"])

    def test_running_and_failed_runs(self):
        async def fetch(status):
            async with httpx.AsyncClient(transport=apify_transport(run_status=status)) as client:
                return await apify_maps.fetch_search("RunAbc12345", "BA", "Salvador", token="token-de-teste", client=client)

        self.assertEqual(self.run_async(fetch("RUNNING")).status, "running")
        with self.assertRaises(ScraperError):
            self.run_async(fetch("FAILED"))

    def test_bad_token_has_clear_message(self):
        async def scenario():
            async with httpx.AsyncClient(transport=apify_transport()) as client:
                await apify_maps.start_search("BA", "Salvador", token="errado", client=client)

        with self.assertRaisesRegex(ScraperError, "APIFY_TOKEN"):
            self.run_async(scenario())


class TestCsvParsing(unittest.TestCase):
    def test_excel_style_semicolon_with_accents(self):
        content = (
            "﻿Nome da Barbearia;WhatsApp;Município;Estado;Instagram;Site;Responsável;Cadeiras\n"
            "Barbearia Alfa;(77) 99911-2233;Vitória da Conquista;Bahia;instagram.com/alfa.barber;;Zé;3\n"
            "Barbearia Beta;77 98888-7777;Vitória da Conquista;BA;@beta;https://wa.me/5577988887777;;\n"
        )
        parsed = parse_leads_csv(content)
        self.assertEqual(parsed.problems, [])
        alfa, beta = parsed.leads
        self.assertEqual((alfa.phone, alfa.state, alfa.instagram_handle, alfa.owner_name, alfa.estimated_barbers),
                         ("5577999112233", "BA", "@alfa.barber", "Zé", 3))
        self.assertEqual(beta.current_system, TechMaturity.MANUAL_WHATSAPP)
        self.assertEqual(alfa.source, LeadSource.CSV_IMPORT)

    def test_comma_separated_with_form_defaults_and_problems(self):
        content = (
            "nome,telefone\n"
            "Barbearia Um,77999112233\n"
            "Barbearia Dois,123\n"
            ",77977776666\n"
            "Barbearia Um de novo,(77) 99911-2233\n"
            "\n"
        )
        parsed = parse_leads_csv(content, default_state="ba", default_city="Itapetinga")
        self.assertEqual([lead.shop_name for lead in parsed.leads], ["Barbearia Um"])
        self.assertEqual(parsed.leads[0].city, "Itapetinga")
        self.assertEqual(parsed.duplicates_in_file, 1)
        self.assertEqual([(p.line, p.reason.split(":")[0]) for p in parsed.problems], [(3, "telefone inválido"), (4, "sem nome")])

    def test_rows_without_uf_or_city_are_rejected(self):
        parsed = parse_leads_csv("nome;telefone\nBarbearia;77999112233\n")
        self.assertEqual(parsed.leads, [])
        self.assertIn("sem UF", parsed.problems[0].reason)

    def test_missing_required_columns(self):
        with self.assertRaisesRegex(ImportFormatError, "telefone"):
            parse_leads_csv("nome;cidade\nBarbearia;Salvador\n")
        with self.assertRaises(ImportFormatError):
            parse_leads_csv("   ")


class TestLeadSourceApi(unittest.TestCase):
    def setUp(self):
        env = patch_auth_env({"GOOGLE_MAPS_API_KEY": "", "APIFY_TOKEN": "token-de-teste"})
        env.start()
        self.addCleanup(env.stop)
        store.reset()
        self.client = TestClient(app)
        login(self.client)

    def patch_apify(self, run_status: str = "SUCCEEDED"):
        transport = apify_transport(run_status=run_status)
        real_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        patcher = mock.patch.object(apify_maps.httpx, "AsyncClient", side_effect=client_factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_search_with_apify_runs_in_background_then_saves(self):
        self.patch_apify()
        self.assertEqual(self.client.get("/api/metrics").json()["lead_source"], "APIFY")

        started = self.client.post("/api/leads/scrape", json={"state": "BA", "city": "Salvador"}).json()
        self.assertEqual(started, {"status": "running", "run_id": "RunAbc12345", "source": "APIFY"})

        done = self.client.get("/api/leads/scrape/RunAbc12345", params={"state": "BA", "city": "Salvador"}).json()
        self.assertEqual((done["status"], done["count"], done["new"]), ("done", 2, 2))
        again = self.client.get("/api/leads/scrape/RunAbc12345", params={"state": "BA", "city": "Salvador"}).json()
        self.assertEqual((again["count"], again["new"]), (2, 0))  # sem duplicar por telefone
        self.assertEqual(self.client.get("/api/leads").json()["count"], 2)

    def test_progress_while_running_and_invalid_id(self):
        self.patch_apify(run_status="RUNNING")
        res = self.client.get("/api/leads/scrape/RunAbc12345", params={"state": "BA", "city": "Salvador"})
        self.assertEqual(res.json(), {"status": "running", "run_id": "RunAbc12345"})
        bad = self.client.get("/api/leads/scrape/..%2Fetc", params={"state": "BA", "city": "Salvador"})
        self.assertIn(bad.status_code, (400, 404))

    def test_without_any_key_search_stays_synthetic(self):
        with mock.patch.dict("os.environ", {"APIFY_TOKEN": ""}):
            res = self.client.post("/api/leads/scrape", json={"state": "BA", "city": "Salvador"}).json()
        self.assertEqual(res["status"], "done")
        self.assertTrue(all(lead["is_synthetic"] for lead in res["leads"]))

    def test_import_csv_endpoint(self):
        csv_text = "nome;telefone;cidade;uf\nBarbearia Alfa;(77) 99911-2233;Vitória da Conquista;BA\nSem fone;;Salvador;BA\n"
        res = self.client.post("/api/leads/import", json={"csv": csv_text}).json()
        self.assertEqual((res["imported"], res["already_in_funnel"]), (1, 0))
        self.assertEqual(res["rejected"][0]["line"], 3)
        self.assertEqual(res["leads"][0]["source"], "CSV_IMPORT")

        again = self.client.post("/api/leads/import", json={"csv": csv_text}).json()
        self.assertEqual((again["imported"], again["already_in_funnel"]), (0, 1))

        bad = self.client.post("/api/leads/import", json={"csv": "nome;cidade\nX;Y\n"})
        self.assertEqual(bad.status_code, 422)
        self.assertIn("telefone", bad.json()["detail"])

    def test_template_and_auth(self):
        template = self.client.get("/api/leads/import/template").json()
        self.assertTrue(template["csv"].startswith("nome;telefone"))
        anonymous = TestClient(app)
        self.assertEqual(anonymous.post("/api/leads/import", json={"csv": "x"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
