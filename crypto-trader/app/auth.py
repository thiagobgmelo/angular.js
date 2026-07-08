"""Autenticação por token de API.

Quando a env `API_TOKEN` está definida, toda rota `/api/*` exige
`Authorization: Bearer <token>` (ou `?token=` — necessário para SSE, já que
EventSource não envia headers). Sem `API_TOKEN`, nada é exigido — modo de
desenvolvimento local, onde o bind em 127.0.0.1 é a proteção.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Request


def token_configured() -> bool:
    return bool(os.environ.get("API_TOKEN"))


def request_authorized(request: Request) -> bool:
    expected = os.environ.get("API_TOKEN")
    if not expected:
        return True
    provided: str | None = None
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        provided = header[7:].strip()
    if provided is None:
        provided = request.query_params.get("token")
    if provided is None:
        return False
    return secrets.compare_digest(provided, expected)
