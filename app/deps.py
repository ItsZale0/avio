"""Dipendenze FastAPI per l'autenticazione e il controllo degli accessi.

Regole:
- le rotte sotto `/api/` richiedono una sessione valida, tranne quelle
  esplicitamente pubbliche (login, registrazione, stato del bootstrap);
- ogni utente vede solo le proprie ricerche, offerte e avvisi;
- il ruolo admin serve per approvare gli account e gestire le impostazioni
  comuni (chiave Groq, VAPID, orari GTFS).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from . import auth

# Endpoint che devono restare raggiungibili senza sessione.
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/status",
    "/api/auth/logout",
    "/api/status",
    "/api/sources",
}


def _token_from_request(request: Request) -> str | None:
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        return token
    # Fallback per client non-browser (curl, script): header Bearer.
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def current_user(request: Request) -> dict[str, Any] | None:
    """Utente della sessione corrente, o None se non c'e'."""
    return auth.resolve_session(_token_from_request(request))


def require_user(request: Request) -> dict[str, Any]:
    """Utente obbligatorio: 401 se non autenticato."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Devi effettuare l'accesso")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    """Utente obbligatorio con ruolo admin: 403 se non lo e'."""
    user = require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Serve il ruolo amministratore")
    return user


def set_session_cookie(response, token: str, *, secure: bool = False) -> None:
    """Imposta il cookie di sessione (HttpOnly, SameSite=Lax)."""
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
