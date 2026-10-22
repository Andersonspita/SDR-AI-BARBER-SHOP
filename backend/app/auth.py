"""
Autenticação do SDR-AI:
- Painel/API: admin único configurado por env. O login devolve um JWT em cookie httpOnly
  (`sdr_session`); scripts podem mandar o mesmo token em `Authorization: Bearer`.
- Webhook da Evolution API: segredo compartilhado (`WEBHOOK_SECRET`) no header
  `X-Webhook-Token` ou no parâmetro `?token=` da URL configurada na Evolution.
"""
from __future__ import annotations
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List, Optional
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

SESSION_COOKIE = "sdr_session"
JWT_ALGORITHM = "HS256"
DEFAULT_ADMIN_PASSWORD = "troque-esta-senha"
DEFAULT_WEBHOOK_SECRET = "troque-este-segredo-do-webhook"
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 12

# Sem JWT_SECRET no env, cada processo gera o seu: sessões caem a cada restart (aceitável só em dev).
_EPHEMERAL_JWT_SECRET = secrets.token_urlsafe(48)


@dataclass(frozen=True)
class AuthSettings:
    admin_email: str
    admin_password: str
    jwt_secret: str
    jwt_secret_is_ephemeral: bool
    session_ttl_hours: int
    cookie_secure: bool
    webhook_secret: str

    @classmethod
    def from_env(cls) -> "AuthSettings":
        jwt_secret = os.getenv("JWT_SECRET", "").strip()
        return cls(
            admin_email=os.getenv("ADMIN_EMAIL", "").strip().lower(),
            admin_password=os.getenv("ADMIN_PASSWORD", ""),
            jwt_secret=jwt_secret or _EPHEMERAL_JWT_SECRET,
            jwt_secret_is_ephemeral=not jwt_secret,
            session_ttl_hours=int(os.getenv("SESSION_TTL_HOURS", "12")),
            cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
        )


def production_auth_problems(settings: AuthSettings) -> List[str]:
    """Configurações inseguras que impedem a API de subir com WhatsApp real."""
    problems: List[str] = []
    if not settings.admin_email:
        problems.append("ADMIN_EMAIL não definido")
    if settings.admin_password in ("", DEFAULT_ADMIN_PASSWORD) or len(settings.admin_password) < MIN_PASSWORD_LENGTH:
        problems.append(f"ADMIN_PASSWORD ausente, padrão ou com menos de {MIN_PASSWORD_LENGTH} caracteres")
    if settings.jwt_secret_is_ephemeral or len(settings.jwt_secret) < MIN_SECRET_LENGTH:
        problems.append(f"JWT_SECRET ausente ou com menos de {MIN_SECRET_LENGTH} caracteres")
    if settings.webhook_secret in ("", DEFAULT_WEBHOOK_SECRET) or len(settings.webhook_secret) < MIN_SECRET_LENGTH:
        problems.append(f"WEBHOOK_SECRET ausente, padrão ou com menos de {MIN_SECRET_LENGTH} caracteres")
    if not settings.cookie_secure:
        problems.append("COOKIE_SECURE precisa ser true (painel servido via HTTPS)")
    return problems


def _same_secret(provided: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())


class LoginThrottle:
    """Bloqueia força bruta: no máximo `max_failures` erros por IP dentro da janela."""

    def __init__(self, max_failures: int = 5, window_sec: int = 300) -> None:
        self.max_failures = max_failures
        self.window_sec = window_sec
        self._failures: Dict[str, Deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> Deque[float]:
        attempts = self._failures[key]
        while attempts and now - attempts[0] > self.window_sec:
            attempts.popleft()
        return attempts

    def is_blocked(self, key: str) -> bool:
        return len(self._prune(key, time.monotonic())) >= self.max_failures

    def register_failure(self, key: str) -> None:
        self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: Optional[str] = None) -> None:
        if key is None:
            self._failures.clear()
        else:
            self._failures.pop(key, None)


login_throttle = LoginThrottle()


def issue_session_token(email: str, settings: AuthSettings) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": email, "iat": now, "exp": now + timedelta(hours=settings.session_ttl_hours)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def _token_from_request(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


async def require_session(request: Request) -> str:
    """Dependência das rotas `/api/*`: devolve o e-mail do admin autenticado ou responde 401."""
    token = _token_from_request(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Faça login para continuar")
    settings = AuthSettings.from_env()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão expirada, faça login de novo")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão inválida")
    if payload["sub"] != settings.admin_email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão inválida")
    return payload["sub"]


async def require_webhook_token(request: Request) -> None:
    """Dependência do `/webhook/whatsapp`: só aceita eventos com o WEBHOOK_SECRET."""
    expected = AuthSettings.from_env().webhook_secret
    provided = request.headers.get("x-webhook-token") or request.query_params.get("token") or ""
    if not expected:
        logger.error("Webhook rejeitado: WEBHOOK_SECRET não configurado")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook não configurado")
    if not _same_secret(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token do webhook inválido")


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    client_key = request.client.host if request.client else "desconhecido"
    if login_throttle.is_blocked(client_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Muitas tentativas. Aguarde alguns minutos.")

    settings = AuthSettings.from_env()
    if not settings.admin_email or not settings.admin_password:
        logger.error("Login indisponível: ADMIN_EMAIL/ADMIN_PASSWORD não configurados")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Login não configurado no servidor")

    # Compara os dois campos sempre, para o tempo de resposta não revelar qual deles errou.
    email_ok = _same_secret(body.email.strip().lower(), settings.admin_email)
    password_ok = _same_secret(body.password, settings.admin_password)
    if not (email_ok and password_ok):
        login_throttle.register_failure(client_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha incorretos")

    login_throttle.reset(client_key)
    token = issue_session_token(settings.admin_email, settings)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"email": settings.admin_email, "token": token, "expires_in_hours": settings.session_ttl_hours}


@router.post("/logout")
async def logout(response: Response) -> dict:
    settings = AuthSettings.from_env()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
    return {"status": "ok"}


@router.get("/me")
async def me(email: str = Depends(require_session)) -> dict:
    return {"email": email}
