"""Configuração de autenticação compartilhada pelos testes de API."""
import os
from unittest import mock
from fastapi.testclient import TestClient

TEST_ADMIN_EMAIL = "admin@gerentebarber.test"
TEST_ADMIN_PASSWORD = "senha-de-teste-123"
TEST_WEBHOOK_SECRET = "segredo-do-webhook-de-teste-com-32-chars"

TEST_AUTH_ENV = {
    "ADMIN_EMAIL": TEST_ADMIN_EMAIL,
    "ADMIN_PASSWORD": TEST_ADMIN_PASSWORD,
    "JWT_SECRET": "jwt-secret-de-teste-com-pelo-menos-32-caracteres",
    "WEBHOOK_SECRET": TEST_WEBHOOK_SECRET,
    "CADENCE_WORKER_ENABLED": "false",
}

WEBHOOK_HEADERS = {"X-Webhook-Token": TEST_WEBHOOK_SECRET}


def patch_auth_env(extra: dict | None = None):
    """Context manager que aplica o env de autenticação de teste (e extras)."""
    return mock.patch.dict(os.environ, {**TEST_AUTH_ENV, **(extra or {})})


def login(client: TestClient) -> None:
    """Autentica o TestClient: o cookie de sessão fica guardado nele."""
    response = client.post("/api/auth/login", json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
