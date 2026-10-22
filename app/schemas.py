"""Modelli Pydantic per API e validazione input."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_SOURCES = [
    "blablacar",
    "flixbus",
    "itabus",
    "trenitalia",
    "omio",
    "googleflights",
    "skyscanner",
    "carsharing",
    "transit",
    "gtfs",
]
DEFAULT_NOTIFY = ["web", "telegram"]


class WatchIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    from_place: str = Field(..., min_length=2, max_length=160)
    from_lat: float | None = Field(None, ge=-90, le=90)
    from_lon: float | None = Field(None, ge=-180, le=180)
    # Punto di ritrovo esatto (quartiere, piazza, stazione): serve per
    # calcolare il tempo a piedi e chiedere il posto all'agente.
    from_point: str | None = Field(None, max_length=160)
    from_point_lat: float | None = Field(None, ge=-90, le=90)
    from_point_lon: float | None = Field(None, ge=-180, le=180)
    to_place: str = Field(..., min_length=2, max_length=160)
    to_lat: float | None = Field(None, ge=-90, le=90)
    to_lon: float | None = Field(None, ge=-180, le=180)
    to_point: str | None = Field(None, max_length=160)
    to_point_lat: float | None = Field(None, ge=-90, le=90)
    to_point_lon: float | None = Field(None, ge=-180, le=180)
    depart_after: datetime | None = None
    arrive_by: datetime
    max_price: float | None = Field(None, ge=0, le=10000)
    passengers: int = Field(1, ge=1, le=8)
    radius_km: float = Field(5, ge=0.5, le=100)
    sources: list[str] = Field(default_factory=lambda: list(DEFAULT_SOURCES))
    notify: list[str] = Field(default_factory=lambda: list(DEFAULT_NOTIFY))
    interval_min: int = Field(60, ge=10, le=1440)
    active: bool = True
    prefs: dict[str, Any] | None = None
    # Cartella di appartenenza (il raggruppamento per viaggio).
    group_id: int | None = None

    @field_validator("sources")
    @classmethod
    def _clean_sources(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        return list(dict.fromkeys(cleaned)) or list(DEFAULT_SOURCES)

    @field_validator("notify")
    @classmethod
    def _clean_notify(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        return list(dict.fromkeys(cleaned)) or ["web"]

    @model_validator(mode="after")
    def _check_window(self):
        if self.depart_after and self.depart_after >= self.arrive_by:
            raise ValueError("La partenza minima deve essere precedente all'arrivo massimo")
        return self


class WatchUpdate(BaseModel):
    name: str | None = None
    from_place: str | None = None
    from_lat: float | None = None
    from_lon: float | None = None
    to_place: str | None = None
    to_lat: float | None = None
    to_lon: float | None = None
    depart_after: datetime | None = None
    arrive_by: datetime | None = None
    max_price: float | None = None
    passengers: int | None = Field(None, ge=1, le=8)
    radius_km: float | None = Field(None, ge=0.5, le=100)
    sources: list[str] | None = None
    notify: list[str] | None = None
    interval_min: int | None = Field(None, ge=10, le=1440)
    active: bool | None = None
    prefs: dict[str, Any] | None = None
    group_id: int | None = None


class WatchOut(BaseModel):
    id: int
    name: str | None = None
    from_place: str
    from_lat: float | None = None
    from_lon: float | None = None
    from_point: str | None = None
    from_point_lat: float | None = None
    from_point_lon: float | None = None
    to_place: str
    to_lat: float | None = None
    to_lon: float | None = None
    to_point: str | None = None
    to_point_lat: float | None = None
    to_point_lon: float | None = None
    depart_after: str | None = None
    arrive_by: str
    max_price: float | None = None
    passengers: int = 1
    radius_km: float = 5
    sources: list[str] = Field(default_factory=list)
    notify: list[str] = Field(default_factory=list)
    interval_min: int = 60
    active: bool = True
    created_at: str
    last_run_at: str | None = None
    next_run_at: str | None = None
    offers_count: int = 0
    best_price: float | None = None
    prefs: dict[str, Any] = Field(default_factory=dict)
    ai_summary: str | None = None
    ai_best_price: float | None = None
    ai_updated_at: str | None = None
    group_id: int | None = None


class WatchGroupIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class WatchGroupOut(BaseModel):
    id: int
    name: str
    position: int = 0
    count: int = 0


class OfferOut(BaseModel):
    id: int
    watch_id: int
    fingerprint: str
    source: str
    mode: str
    operator: str | None = None
    depart_at: str | None = None
    arrive_at: str | None = None
    price: float | None = None
    currency: str = "EUR"
    price_is_estimate: bool = False
    pickup_name: str | None = None
    pickup_lat: float | None = None
    pickup_lon: float | None = None
    dropoff_name: str | None = None
    dropoff_lat: float | None = None
    dropoff_lon: float | None = None
    duration_min: int | None = None
    seats: int | None = None
    rating: float | None = None
    deeplink: str | None = None
    score: float | None = None
    badges: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    first_seen: str
    last_seen: str
    prev_price: float | None = None
    active: bool = True


class SettingsIn(BaseModel):
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    smtp_tls: str | None = None
    quiet_from: str | None = None
    quiet_to: str | None = None
    notify_enabled: str | None = None
    groq_api_key: str | None = None
    ai_enabled: str | None = None
    ai_model_smart: str | None = None
    ai_model_fast: str | None = None
    gtfs_enabled: str | None = None
    # Lista JSON degli abbonamenti in corso (globali per l'utente).
    passes: str | None = None
    # Agevolazione X-GO: "giovane" / "senior" / "".
    xgo: str | None = None


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=8)
    auth: str = Field(..., min_length=8)


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(..., min_length=8, max_length=1000)
    keys: PushKeys
    label: str | None = Field(None, max_length=80)


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(..., min_length=8, max_length=1000)


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=800)


class AnswerIn(BaseModel):
    answer: str = Field(..., min_length=1, max_length=600)
    source: str = "web"


class AskIn(BaseModel):
    """Una domanda o richiesta libera all'agente."""

    text: str = Field(..., min_length=1, max_length=1000)


class QuestionOut(BaseModel):
    id: int
    watch_id: int
    run_id: int | None = None
    key: str
    text: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    allow_free_text: bool = True
    status: str = "pending"
    answer: str | None = None
    answer_source: str | None = None
    created_at: str
    answered_at: str | None = None


class PlanOut(BaseModel):
    id: int | None = None
    watch_id: int
    run_id: int | None = None
    kind: str = "cheapest"
    title: str
    total_price: float | None = None
    total_duration_min: int | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str | None = None
    watchouts: list[str] = Field(default_factory=list)
    confidence: float | None = None
    fingerprint: str | None = None
    created_at: str | None = None
    is_current: bool = True


class RunOut(BaseModel):
    id: int
    watch_id: int
    trigger: str
    status: str
    started_at: str
    finished_at: str | None = None
    offers_found: int = 0
    alerts_sent: int = 0
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    ai_used: int = 0
    ai_tokens: int = 0
    plan_price: float | None = None


# --------------------------------------------------------------------------- #
# Accesso
# --------------------------------------------------------------------------- #
class UnlockIn(BaseModel):
    """Password d'accesso (o chiave segreta)."""

    password: str = Field(..., min_length=1, max_length=300)


class AccessIn(BaseModel):
    """Nuova password d'accesso. Stringa vuota = toglie la password."""

    password: str = Field("", max_length=300)
