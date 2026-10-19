"""Omio (GoEuro) - aggregatore reale di treni, autobus e voli.

L'app Omio espone le stesse offerte di decine di compagnie italiane ed estere
(Trenitalia, Italo, FlixBus, Itabus, Marino, BlaBlaCar Bus, compagnie aeree...).
Usiamo l'endpoint pubblico che usa l'app mobile (non documentato, puo' cambiare).

Nessun dato viene inventato: ogni offerta riporta prezzo, orari e stazioni
restituiti dal servizio. In caso di errore restituiamo solo un hint, cosi' le
altre fonti restano utilizzabili.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx

from ..geo import CITIES, haversine_km
from ..timeutil import parse_dt
from .base import Adapter, AdapterResult, Offer, SearchContext, tappa

POSITION_URL = "https://www.omio.com/GoEuroAPI/rest/api/v5/position"
SEARCH_URL = "https://www.omio.com/GoEuroAPI/rest/api//v5/searches"
RESULTS_URL = "https://www.omio.com/GoEuroAPI/rest/api/v5/results"

HEADERS = {
    "user-agent": "GoEuroIOS /9.17.1 (iPhone; iOS 17.2.1; Scale/3.00)",
    "goeuro-client": "GoEuroIOS /9.17.1 (iPhone; iOS 17.2.1; Scale/3.00)",
    "content-type": "application/json",
    "accept": "application/json",
}

# Compagnie note: id Omio -> (modo, nome leggibile). Per le altre usiamo il nome
# che Omio restituisce nella mappa `companies`.
KNOWN_COMPANIES: dict[str, tuple[str, str]] = {
    "3137": ("train", "Trenitalia Regionale"),
    "2468": ("train", "Frecciarossa"),
    "3139": ("train", "Intercity"),
    "3140": ("train", "Intercity Notte"),
    "1276": ("train", "Italo"),
    "1045": ("bus", "FlixBus"),
}

MAX_FALLBACK_KM = 60
FLIGHT_MIN_KM = 450.0  # gli aerei convengono solo sulle tratte lunghe
MAX_OFFERS = 80

_pos_cache: dict[str, dict | None] = {}


def _candidates(place: str, lat: float | None, lon: float | None) -> list[tuple[str, float]]:
    """Nome richiesto + citta' vicine servite (per non perdere copertura)."""
    names: list[tuple[str, float]] = [(place, 0.0)]
    if lat is None or lon is None:
        return names
    ranked = sorted(CITIES.items(), key=lambda item: haversine_km(lat, lon, item[1][0], item[1][1]))
    for name, coords in ranked:
        if name.lower() == place.strip().lower():
            continue
        distance = haversine_km(lat, lon, coords[0], coords[1])
        if distance > MAX_FALLBACK_KM:
            break
        names.append((name.title(), round(distance, 1)))
        if len(names) >= 3:
            break
    return names


async def _lookup_position(client: httpx.AsyncClient, name: str) -> dict | None:
    key = name.strip().lower()
    if key in _pos_cache:
        return _pos_cache[key]
    result: dict | None = None
    try:
        response = await client.get(
            POSITION_URL, params={"term": name, "locale": "en", "limit": "8"}
        )
        response.raise_for_status()
        items = response.json()
        if isinstance(items, list) and items:
            italiano = [item for item in items if str(item.get("countryCode", "")).upper() == "IT"]
            pool = italiano or items
            exact = [item for item in pool if str(item.get("displayName", "")).lower() == key]
            chosen = (exact or pool)[0]
            result = {
                "id": chosen.get("locationPositionId") or chosen.get("positionId"),
                "name": chosen.get("displayName") or chosen.get("defaultName") or name,
                "lat": chosen.get("latitude"),
                "lon": chosen.get("longitude"),
            }
    except Exception:
        result = None
    _pos_cache[key] = result
    return result


async def _resolve(client: httpx.AsyncClient, place: str, lat: float | None, lon: float | None):
    """Trova la posizione Omio piu' adatta; ripiega su citta' vicine se serve."""
    for candidate, distance in _candidates(place, lat, lon):
        found = await _lookup_position(client, candidate)
        if found and found.get("id"):
            shifted = candidate.strip().lower() != place.strip().lower()
            return found, (distance if shifted else 0.0), shifted
    return None, 0.0, False


def _search_dates(ctx: SearchContext) -> list[str]:
    start = ctx.window_start
    end = min(ctx.arrive_by, start + timedelta(days=2))
    days: list[str] = []
    current = start
    while current.date() <= end.date() and len(days) < 3:
        days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return days or [start.strftime("%Y-%m-%d")]


def _payload(dep_id, arr_id, date: str, modes: list[str]) -> dict:
    return {
        "searchOptions": {
            "departureDate": date,
            "departurePosition": {"id": dep_id},
            "arrivalPosition": {"id": arr_id},
            "passengers": [{"discountCards": [], "type": "adult", "age": 30}],
            "travelModes": modes,
            "userInfo": {"identifier": "-", "domain": "com", "locale": "en", "currency": "EUR"},
            "includeRoutedConnections": True,
        }
    }


async def _await_results(client: httpx.AsyncClient, search_id: str, modes: list[str]) -> dict:
    params = {
        "direction": "outbound",
        "search_id": search_id,
        "sort_by": "updateTime",
        "include_segment_positions": "true",
        "use_recommendation": "true",
        "use_stats": "true",
        "sort_variants": "smart",
        "exclude_offsite_bus_results": "true",
        "exclude_offsite_train_results": "true",
        "updated_since": "0",
    }
    data: dict = {}
    for _ in range(12):
        response = await client.get(RESULTS_URL, params=params)
        response.raise_for_status()
        data = response.json()
        modes_state = (data.get("query") or {}).get("searchModes") or {}
        pending = any((modes_state.get(modo) or {}).get("status") == "inprogress" for modo in modes)
        if data.get("outbounds") and not pending:
            break
        await asyncio.sleep(0.8)
    return data


def _time(value) -> datetime | None:
    return parse_dt(value)


def _company(data: dict, company_id: str) -> tuple[str | None, str, str]:
    """Restituisce (modo noto, etichetta, nome grezzo) per una compagnia Omio."""
    companies = data.get("companies") or {}
    info = companies.get(str(company_id)) or {}
    raw = (info.get("name") or "").strip()
    known = KNOWN_COMPANIES.get(str(company_id))
    if known:
        mode, label = known
        return mode, (label or raw), raw
    return None, raw, raw


def _place_label(pos: dict) -> str:
    """Nome preciso della fermata/stazione, con la citta' quando aggiunge info."""
    if not isinstance(pos, dict):
        return ""
    name = str(pos.get("name") or "").strip()
    city = str(pos.get("cityName") or "").strip()
    if name and city and city.lower() not in name.lower():
        return f"{name} ({city})"
    return name or city


def _dedupe(offers: list[Offer]) -> list[Offer]:
    """Omio restituisce varianti dello stesso viaggio: teniamo la piu' economica."""
    best: dict[tuple, Offer] = {}
    for offer in offers:
        key = (offer.mode, offer.operator, offer.depart_at, offer.arrive_at)
        current = best.get(key)
        if current is None or (offer.price or 9e9) < (current.price or 9e9):
            best[key] = offer
    return list(best.values())


class OmioAdapter(Adapter):
    id = "omio"
    label = "Omio (treni, bus, voli)"
    description = "Aggregatore multi-compagnia: Trenitalia, Italo, FlixBus, Itabus, Marino e voli."
    modes = ("train", "bus", "flight")

    async def search(self, ctx: SearchContext) -> AdapterResult:
        days = _search_dates(ctx)
        offers: list[Offer] = []
        hints: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=25, headers=HEADERS) as client:
                from_pos, from_shift, from_shifted = await _resolve(
                    client, ctx.from_place, ctx.from_lat, ctx.from_lon
                )
                to_pos, to_shift, to_shifted = await _resolve(
                    client, ctx.to_place, ctx.to_lat, ctx.to_lon
                )
                if not from_pos or not to_pos:
                    return AdapterResult(
                        hints=[f"Omio: localita' non riconosciuta ({ctx.from_place} / {ctx.to_place})."]
                    )
                if from_shifted:
                    hints.append(
                        f"Omio: {ctx.from_place} non servita, ho cercato da {from_pos['name']} (~{from_shift:.0f} km)."
                    )
                if to_shifted:
                    hints.append(
                        f"Omio: {ctx.to_place} non servita, ho cercato fino a {to_pos['name']} (~{to_shift:.0f} km)."
                    )

                modes = ["train", "bus"]
                distance = ctx.distance_km
                if distance is not None and distance > FLIGHT_MIN_KM:
                    modes.append("flight")

                for day in days:
                    response = await client.post(
                        SEARCH_URL, json=_payload(from_pos["id"], to_pos["id"], day, modes)
                    )
                    if response.status_code >= 300:
                        continue
                    search_id = (response.json() or {}).get("searchId")
                    if not search_id:
                        continue
                    data = await _await_results(client, search_id, modes)
                    offers.extend(
                        self._parse(
                            data,
                            ctx,
                            from_pos,
                            to_pos,
                            from_shifted=from_shifted,
                            to_shifted=to_shifted,
                            from_km=from_shift,
                            to_km=to_shift,
                        )
                    )
        except Exception as exc:
            return AdapterResult(hints=[f"Omio non raggiungibile: {type(exc).__name__}"])

        if not offers:
            hints.append("Omio: nessun collegamento trovato per questa data.")
        return AdapterResult(offers=_dedupe(offers)[:MAX_OFFERS], hints=hints)

    def _parse(
        self,
        data: dict,
        ctx: SearchContext,
        from_pos: dict,
        to_pos: dict,
        *,
        from_shifted: bool,
        to_shifted: bool,
        from_km: float,
        to_km: float,
    ) -> list[Offer]:
        outbounds = data.get("outbounds") or {}
        segments = data.get("segmentDetails") or {}
        positions = data.get("positions") or {}
        offers: list[Offer] = []

        def position(pid) -> dict:
            return positions.get(str(pid)) or {}

        for outbound in outbounds.values():
            if not isinstance(outbound, dict):
                continue
            if str(outbound.get("status")) != "available":
                continue
            try:
                price = float(outbound.get("price")) / 100.0
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue

            depart_at = _time(outbound.get("departureTime"))
            arrive_at = _time(outbound.get("arrivalTime"))
            if not depart_at:
                continue

            known_mode, operator, raw_name = _company(data, str(outbound.get("companyId")))
            mode = known_mode
            omio_mode = str(outbound.get("mode") or "")
            if omio_mode in ("train", "bus", "flight"):
                mode = omio_mode
            if not mode:
                mode = "bus"
            if not operator:
                operator = "Volo" if mode == "flight" else "Compagnia"

            seg_ids = outbound.get("segments") or []
            first_seg = segments.get(str(seg_ids[0])) if seg_ids else {}
            last_seg = segments.get(str(seg_ids[-1])) if seg_ids else {}
            dep = position((first_seg or {}).get("departurePosition")) or from_pos
            arr = position((last_seg or {}).get("arrivalPosition")) or to_pos

            tappe: list[dict] = []
            for seg_id in seg_ids:
                seg = segments.get(str(seg_id)) or {}
                seg_dep = position(seg.get("departurePosition")) or {}
                seg_arr = position(seg.get("arrivalPosition")) or {}
                _, seg_operator, _ = _company(data, str(seg.get("company")))
                tappe.append(
                    tappa(
                        _place_label(seg_dep),
                        _place_label(seg_arr),
                        seg.get("departureTime"),
                        seg.get("arrivalTime"),
                        seg.get("transportId") or seg_operator or seg.get("type") or "",
                    )
                )

            duration = outbound.get("duration")
            try:
                duration_min = int(duration) if duration is not None else None
            except (TypeError, ValueError):
                duration_min = None

            try:
                changes = int(outbound.get("stops"))
            except (TypeError, ValueError):
                changes = None

            tickets = outbound.get("ticketsLeft")
            seats = int(tickets) if isinstance(tickets, int) and 0 < tickets < 1000 else None

            offers.append(
                Offer(
                    source=self.id,
                    external_id=str(outbound.get("outboundId") or outbound.get("journeyId")),
                    mode=mode,
                    operator=operator,
                    depart_at=depart_at,
                    arrive_at=arrive_at,
                    price=round(price, 2),
                    pickup_name=_place_label(dep) or from_pos["name"],
                    pickup_lat=dep.get("latitude", ctx.from_lat),
                    pickup_lon=dep.get("longitude", ctx.from_lon),
                    dropoff_name=_place_label(arr) or to_pos["name"],
                    dropoff_lat=arr.get("latitude", ctx.to_lat),
                    dropoff_lon=arr.get("longitude", ctx.to_lon),
                    duration_min=duration_min,
                    seats=seats,
                    deeplink="https://www.omio.com/",
                    details={
                        "fonte": "Omio",
                        "compagnia": raw_name or operator,
                        "cambi": changes,
                        "tappe": tappe,
                        "partenza_alternativa": from_pos["name"] if from_shifted else "",
                        "arrivo_alternativo": to_pos["name"] if to_shifted else "",
                        "distanza_partenza_km": from_km if from_shifted else 0,
                        "distanza_arrivo_km": to_km if to_shifted else 0,
                    },
                )
            )
        return offers
