"""Dipendenze FastAPI per l'autenticazione e il controllo degli accessi.

Regole:
- le rotte sotto `/api/` richiedono una sessione valida, tranne quelle
  esplicitamente pubbliche (login, registrazione, stato del bootstrap);
- ogni utente vede solo le proprie ricerche, offerte e avvisi;
- il ruolo admin serve per approvare gli account e gestire le impostazioni
  comuni (chiave Groq, VAPID, orari GTFS).
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, Request

from . import auth

# Endpoint che devono restare raggiungibili senza sessione.
PUBLIC_PATHS = {
    # Lo sblocco: e' proprio lui a creare la sessione.
    "/api/auth/unlock",
    "/api/auth/status",
    "/api/auth/logout",
    "/api/status",
    "/api/sources",
    # Segnale di vita per il sorvegliante esterno: deve rispondere
    # anche con l'accesso chiuso, altrimenti non serve a nessuno.
    "/api/ping",
}

# --------------------------------------------------------------------------- #
# Rate limiting dello sblocco
# --------------------------------------------------------------------------- #
# La porta d'ingresso dell'app e' pubblica su internet: senza un tetto, la
# password si puo' scoprire a forza bruta. Si contano i tentativi FALLITI per
# indirizzo IP; uno sblocco riuscito azzera il conto (un utente legittimo che
# sbaglia la password due volte non deve restare bloccato).
TENTATIVI_MAX = 10
TENTATIVI_WINDOW = 600  # secondi
_tentativi: dict[str, list[float]] = {}


def client_ip(request: Request) -> str:
    """L'indirizzo del client, per quanto possibile reale.

    Dietro Nginx l'IP vero arriva in X-Forwarded-For; Nginx lo riscrive con
    l'indirizzo del client (non lo appende), quindi l'ultimo valore e' quello
    reale. Se la connessione non passa da un proxy locale, l'header non e'
    attendibile e si usa l'IP del socket.
    """
    peer = request.client.host if request.client else ""
    if peer in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("x-forwarded-for") or ""
        if forwarded:
            return forwarded.split(",")[-1].strip() or peer
    return peer


def _cronologia(ip: str) -> list[float]:
    adesso = time.time()
    cronologia = [t for t in _tentativi.get(ip, []) if adesso - t < TENTATIVI_WINDOW]
    _tentativi[ip] = cronologia
    return cronologia


def sblocco_troppo_spedito(request: Request) -> bool:
    """True se questo indirizzo ha esaurito i tentativi di sblocco."""
    return len(_cronologia(client_ip(request))) >= TENTATIVI_MAX


def nota_sblocco_fallito(request: Request) -> None:
    """Registra un tentativo fallito."""
    _cronologia(client_ip(request)).append(time.time())


def pulisci_sblocco(request: Request) -> None:
    """Uno sblocco riuscito azzera il conto dei tentativi."""
    _tentativi.pop(client_ip(request), None)


def _token_from_request(request: Request) -> str | None:
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        return token
    # Fallback per client non-browser (curl, script): header Bearer.
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def is_secure_request(request: Request) -> bool:
    """True se la connessione con il browser e' cifrata.

    Dietro un reverse proxy (Cloudflare Tunnel, Nginx su Oracle) uvicorn vede
    `http` anche se l'utente e' su HTTPS: in quel caso il cookie di sessione
    verrebbe creato senza il flag `Secure` e il browser lo rifiuterebbe.
    Per questo si guarda anche l'header `X-Forwarded-Proto`.
    """
    if request.url.scheme == "https":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").strip().lower()
    return forwarded == "https"

def auth_enabled() -> bool:
    """True se l'app chiede la password.

    La semplice presenza di una password d'accesso basta: non c'e' un
    interruttore separato da ricordarsi. Senza password l'app si apre
    direttamente.
    """
    from . import access

    return access.is_locked()


def current_user(request: Request) -> dict[str, Any] | None:
    """Utente della sessione corrente, o None se non c'e'."""
    return auth.resolve_session(_token_from_request(request))


def require_user(request: Request) -> dict[str, Any]:
    """Utente obbligatorio.

    Con l'accesso disattivato (impostazione predefinita) restituisce il
    proprietario implicito, cosi' l'app funziona senza login.
    """
    user = current_user(request)
    if user:
        return user
    if not auth_enabled():
        owner = auth.implicit_owner()
        if owner:
            return owner
    raise HTTPException(status_code=401, detail="Accesso richiesto")


def require_admin(request: Request) -> dict[str, Any]:
    """Utente obbligatorio con ruolo admin: 403 se non lo e'."""
    user = require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Serve il ruolo amministratore")
    return user


def set_session_cookie(response, token: str, *, secure: bool = False) -> None:
    """Imposta il cookie di sessione (HttpOnly).

    `secure=True` (HTTPS) aggiunge il flag `Secure`: il cookie non viaggia
    in chiaro. Il `SameSite` resta "lax": l'app e l'API sono sulla stessa
    origine (la VM serve entrambe), quindi non serve "none" — che
    manterrebbe vivo il CSRFcross-site.
    """
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
