"""
Tira segredos de URL dos logs. A Evolution só sabe mandar o token do webhook na query string
(`/webhook/whatsapp?token=...`), e o log de acesso do uvicorn grava o caminho completo: sem este filtro,
qualquer pessoa com acesso aos logs teria o segredo do webhook.
"""
from __future__ import annotations
import logging
import re

SECRET_QUERY_PARAM = re.compile(r"((?:^|[?&])(?:token|access_token|apikey|key)=)[^&\s\"]+", re.IGNORECASE)


def redact(text: str) -> str:
    return SECRET_QUERY_PARAM.sub(r"\1***", text)


class RedactSecretsFilter(logging.Filter):
    """Troca o valor de `token=` (e afins) por `***` na mensagem e nos argumentos do registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True


def install() -> None:
    """Aplica o filtro no log de acesso do uvicorn (e no raiz, por garantia)."""
    for name in ("uvicorn.access", ""):
        target = logging.getLogger(name)
        if not any(isinstance(f, RedactSecretsFilter) for f in target.filters):
            target.addFilter(RedactSecretsFilter())
