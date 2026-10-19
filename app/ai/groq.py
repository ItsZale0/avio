"""Client minimale per Groq (endpoint compatibile OpenAI chat/completions).

Nessuna dipendenza extra: usa httpx come il resto del progetto. Se manca la
chiave API o il provider risponde male, il chiamante degrada con grazia.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..services import settings as app_settings

API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS_URL = "https://api.groq.com/openai/v1/models"

# Modelli gratuiti Groq usati di default. Sovrascrivibili dalle impostazioni.
# Nota: il free tier cambia nel tempo; la lista di fallback include i modelli
# piu' usati, cosi' l'agente continua a funzionare anche se il default esce.
DEFAULT_SMART = "openai/gpt-oss-120b"
DEFAULT_FAST = "openai/gpt-oss-20b"
FALLBACK_MODELS = [
    DEFAULT_SMART,
    "qwen/qwen3.8-27b",
    DEFAULT_FAST,
    "allam-2-7b",
]


@dataclass
class ChatResult:
    text: str = ""
    model: str = ""
    tokens: int = 0
    ok: bool = False
    error: str | None = None
    latency_ms: int = 0

    def json(self) -> dict[str, Any] | None:
        """Estrae un oggetto JSON dalla risposta, anche se sporca di testo."""
        return extract_json(self.text)


def extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None
    return None


class GroqError(RuntimeError):
    pass


class GroqClient:
    def __init__(self, api_key: str, model_smart: str | None = None, model_fast: str | None = None):
        self.api_key = (api_key or "").strip()
        self.model_smart = (model_smart or DEFAULT_SMART).strip() or DEFAULT_SMART
        self.model_fast = (model_fast or DEFAULT_FAST).strip() or DEFAULT_FAST
        self.last_model: str | None = None
        self.total_tokens = 0

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _model_order(self, requested: str | None) -> list[str]:
        order = [requested or self.model_smart]
        for candidate in (self.model_smart, self.model_fast, *FALLBACK_MODELS):
            if candidate and candidate not in order:
                order.append(candidate)
        return order

    async def list_models(self) -> list[str]:
        return [model["id"] for model in await self.list_models_detailed()]

    async def list_models_detailed(self) -> list[dict]:
        """Modelli disponibili con contesto e stato, gia' filtrati.

        Si escludono i modelli che non servono a chattare: audio (whisper,
        tts), embedding e filtri (guard, safeguard).
        """
        if not self.available:
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    MODELS_URL, headers={"Authorization": f"Bearer {self.api_key}"}
                )
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return []

        models: list[dict] = []
        for item in payload.get("data", []) if isinstance(payload, dict) else []:
            name = str(item.get("id") or "")
            lowered = name.lower()
            if any(bad in lowered for bad in ("whisper", "tts", "guard", "embed", "safeguard")):
                continue
            if not name:
                continue
            models.append(
                {
                    "id": name,
                    "context_length": item.get("context_length"),
                    "active": bool(item.get("active", True)),
                }
            )
        return sorted(models, key=lambda m: m["id"])

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        json_mode: bool = False,
        max_tokens: int = 900,
        temperature: float = 0.2,
        timeout: float = 40.0,
    ) -> ChatResult:
        if not self.available:
            return ChatResult(ok=False, error="Chiave Groq non configurata")

        errors: list[str] = []
        fatal = False
        for candidate in self._model_order(model):
            result = await self._once(
                messages, candidate, json_mode, max_tokens, temperature, timeout
            )
            if result.ok and not result.text.strip():
                # Il modello ha esaurito i token nel ragionamento e non ha
                # scritto niente: si considera un fallimento e si prova il
                # modello successivo.
                result = ChatResult(
                    ok=False,
                    error="risposta vuota (token esauriti nel ragionamento)",
                    model=result.model,
                    latency_ms=result.latency_ms,
                )
            if result.ok:
                self.last_model = result.model
                self.total_tokens += result.tokens
                return result
            errors.append(f"{candidate}: {result.error}")
            if result.error and any(
                token in result.error for token in ("401", "403", "non configurata")
            ):
                fatal = True
                break
        detail = "; ".join(errors[:3])
        return ChatResult(ok=False, error=detail or "errore sconosciuto", model=model or "")

    async def _once(
        self,
        messages: list[dict[str, str]],
        model: str,
        json_mode: bool,
        max_tokens: int,
        temperature: float,
        timeout: float,
        attempt: int = 0,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except Exception as exc:
            return ChatResult(ok=False, error=f"rete: {type(exc).__name__}", model=model)

        latency = int((time.perf_counter() - started) * 1000)

        if response.status_code == 429 and attempt < 1:
            wait = 1.5
            try:
                wait = min(3.0, float(response.headers.get("retry-after", "1.5")))
            except (TypeError, ValueError):
                pass
            await asyncio.sleep(wait)
            return await self._once(
                messages, model, json_mode, max_tokens, temperature, timeout, attempt + 1
            )

        if response.status_code == 400 and json_mode:
            # Alcuni modelli rifiutano response_format: riprovo in testo libero.
            return await self._once(
                messages, model, False, max_tokens, temperature, timeout, attempt + 1
            )

        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("error", {}).get("message") or body)[:180]
            except Exception:
                detail = response.text[:180]
            return ChatResult(
                ok=False, error=f"{response.status_code} {detail}", model=model, latency_ms=latency
            )

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"] or ""
            tokens = int(body.get("usage", {}).get("total_tokens") or 0)
        except Exception as exc:
            return ChatResult(ok=False, error=f"risposta illeggibile: {type(exc).__name__}", model=model)

        return ChatResult(text=text, model=model, tokens=tokens, ok=True, latency_ms=latency)


def client_from_settings() -> GroqClient:
    values = app_settings.get_all()
    return GroqClient(
        values.get("groq_api_key", ""),
        model_smart=values.get("ai_model_smart"),
        model_fast=values.get("ai_model_fast"),
    )


def ai_enabled() -> bool:
    return app_settings.get_all().get("ai_enabled", "1") == "1"


def log_call(
    task: str,
    *,
    watch_id: int | None,
    run_id: int | None,
    result: ChatResult | None,
    error: str | None = None,
) -> None:
    """Registra l'uso dell'AI (token, latenza) per controllo costi."""
    from .. import db
    from ..timeutil import now_utc

    try:
        with db.db() as conn:
            conn.execute(
                "INSERT INTO ai_log(watch_id, run_id, task, model, tokens, latency_ms, ok, error, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    watch_id,
                    run_id,
                    task,
                    (result.model if result else None),
                    (result.tokens if result else 0),
                    (result.latency_ms if result else 0),
                    1 if (result and result.ok) else 0,
                    error or (result.error if result else None),
                    now_utc().isoformat(),
                ),
            )
    except Exception:
        pass
