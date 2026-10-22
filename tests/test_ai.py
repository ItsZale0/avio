"""Test del pianificatore economico e della degradazione senza AI."""

from __future__ import annotations

import asyncio
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.ai import intelligence
from app.ai.planner import build_candidates
from app.timeutil import ROME, iso, now_utc

from base_db import prepara_db_di_prova


def reference(hours: float = 2) -> datetime:
    """Momento di riferimento stabile per i test.

    Non si usa "adesso": se la finestra attraversa la mezzanotte, l'offerta
    arriverebbe nel giorno sbagliato e il test fallirebbe a seconda dell'ora
    in cui gira. Partiamo da domani mattina alle 8 locali: tutta la
    finestra resta dentro lo stesso giorno.
    """
    local = (now_utc() + timedelta(days=1)).astimezone(ROME)
    base = local.replace(hour=8, minute=0, second=0, microsecond=0)
    return (base + timedelta(hours=hours)).astimezone(timezone.utc)


# Due snodi lontani dalla destinazione: costringono a usare prima e ultima tratta.
HUB_A = (46.20, 11.75)
HUB_B = (46.01, 11.41)

# Modelli Groq pubblicati al momento (free tier). Se il piano cambia, la
# lista si aggiorna: serve a verificare che i default esistano davvero.
GROQ_CHAT_MODELS = {
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "allam-2-7b",
}


def make_offer(
    source: str,
    external_id: str,
    mode: str,
    price: float,
    depart: datetime,
    duration_min: int,
    pickup=(45.7, 11.7),
    dropoff=(46.0, 11.4),
    estimate: bool = False,
) -> Offer:
    return Offer(
        source=source,
        external_id=external_id,
        mode=mode,
        operator=f"{source}-{external_id}",
        depart_at=depart,
        arrive_at=depart + timedelta(minutes=duration_min),
        price=price,
        pickup_name="Partenza",
        dropoff_name="Arrivo",
        pickup_lat=pickup[0],
        pickup_lon=pickup[1],
        dropoff_lat=dropoff[0],
        dropoff_lon=dropoff[1],
        duration_min=duration_min,
        price_is_estimate=estimate,
    )


def context(arrive_by: datetime, *, max_price: float | None = None) -> SearchContext:
    return SearchContext(
        watch_id=1,
        from_place="Thiene",
        to_place="Trento",
        from_lat=45.7,
        from_lon=11.7,
        to_lat=46.0,
        to_lon=11.4,
        depart_after=None,
        arrive_by=arrive_by,
        radius_km=5,
        max_price=max_price,
    )


class PlannerTests(unittest.TestCase):
    def test_cheapest_single_wins(self):
        base = reference(3)
        arrive_by = base + timedelta(hours=6)
        offers = [
            make_offer("itabus", "a", "bus", 20.0, base, 120),
            make_offer("flixbus", "b", "bus", 8.0, base, 180),
        ]
        candidates = build_candidates(offers, context(arrive_by))
        self.assertTrue(candidates)
        self.assertEqual(candidates[0].id, "c1")
        self.assertAlmostEqual(candidates[0].total_price, 8.0)

    def test_two_leg_combo_can_be_cheaper(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        station = (46.3, 11.8)
        car = make_offer("carsharing", "car", "carsharing", 4.0, base, 60, dropoff=station)
        bus = make_offer(
            "flixbus",
            "bus",
            "bus",
            5.0,
            base + timedelta(minutes=90),
            90,
            pickup=station,
        )
        direct = make_offer("flixbus", "direct", "bus", 12.0, base, 150)
        candidates = build_candidates([car, bus, direct], context(arrive_by))
        combo = next((c for c in candidates if c.kind == "combo"), None)
        self.assertIsNotNone(combo)
        self.assertAlmostEqual(combo.total_price, 9.0)
        # Il combo economico deve battere la tratta diretta in classifica.
        self.assertEqual(candidates[0].kind, "combo")

    def test_combo_rejected_when_departure_before_arrival(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        station = (46.0, 11.4)
        car = make_offer("carsharing", "car", "carsharing", 4.0, base, 120, dropoff=station)
        bus = make_offer("flixbus", "bus", "bus", 5.0, base + timedelta(minutes=30), 90, pickup=station)
        candidates = build_candidates([car, bus], context(arrive_by))
        self.assertFalse([c for c in candidates if c.kind == "combo"])

    def test_combo_rejected_when_transfer_too_far(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        car = make_offer("carsharing", "car", "carsharing", 4.0, base, 60, dropoff=(41.9, 12.5))
        bus = make_offer("flixbus", "bus", "bus", 5.0, base + timedelta(minutes=90), 90, pickup=(46.0, 11.4))
        candidates = build_candidates([car, bus], context(arrive_by), max_transfer_km=3)
        self.assertFalse([c for c in candidates if c.kind == "combo"])

    def _three_legs(self, base):
        """carsharing -> treno -> autobus per l'ultimo miglio."""
        car = make_offer("carsharing", "car", "carsharing", 3.0, base, 90, dropoff=HUB_A)
        train = make_offer(
            "trenitalia",
            "train",
            "train",
            4.0,
            base + timedelta(minutes=120),
            60,
            pickup=HUB_A,
            dropoff=HUB_B,
        )
        last = make_offer(
            "gtfs", "last", "bus", 2.0, base + timedelta(minutes=210), 30, pickup=HUB_B
        )
        direct = make_offer("flixbus", "direct", "bus", 12.0, base, 240)
        return [car, train, last, direct]

    def test_three_leg_trip_with_total_cost(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        candidates = build_candidates(self._three_legs(base), context(arrive_by))
        triple = next((c for c in candidates if c.kind == "triple"), None)
        self.assertIsNotNone(triple)
        self.assertEqual(len(triple.legs), 3)
        self.assertAlmostEqual(triple.total_price, 9.0)
        # Il costo totale somma le tre tratte: batte la corsa diretta (12 EUR).
        direct = next(c for c in candidates if c.kind == "single" and c.legs[0].external_id == "direct")
        self.assertLess(triple.effective_cost, direct.effective_cost)
        self.assertEqual(triple.depart_at, base)
        self.assertEqual(triple.arrive_at, base + timedelta(minutes=240))

    def test_three_leg_plan_has_three_steps(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        candidates = build_candidates(self._three_legs(base), context(arrive_by))
        triple = next(c for c in candidates if c.kind == "triple")
        plan = intelligence._plan_from_candidate(triple, None)
        self.assertEqual(len(plan["steps"]), 3)
        self.assertEqual([step["leg"] for step in plan["steps"]], [1, 2, 3])
        self.assertEqual(plan["effective_cost"], 9.0)
        explanation = intelligence._templated_explanation(triple, {})
        self.assertIn("carsharing", explanation.lower())
        self.assertIn("trenitalia", explanation.lower())

    def test_three_leg_rejected_when_last_mile_far_from_destination(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        offers = self._three_legs(base)
        # L'ultimo miglio non arriva a destinazione: niente viaggio a 3 tratte.
        offers[2] = make_offer(
            "gtfs",
            "last",
            "bus",
            2.0,
            base + timedelta(minutes=210),
            30,
            pickup=HUB_B,
            dropoff=(41.9, 12.5),
        )
        candidates = build_candidates(offers, context(arrive_by))
        self.assertFalse([c for c in candidates if c.kind == "triple"])

    def test_three_leg_rejected_when_transfer_too_short(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        offers = self._three_legs(base)
        # L'ultimo miglio parte prima che il treno sia arrivato.
        offers[2] = make_offer("gtfs", "last", "bus", 2.0, base + timedelta(minutes=150), 30, pickup=HUB_B)
        candidates = build_candidates(offers, context(arrive_by))
        self.assertFalse([c for c in candidates if c.kind == "triple"])

    def test_multi_leg_can_be_disabled(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        candidates = build_candidates(
            self._three_legs(base), context(arrive_by), allow_multi_leg=False
        )
        self.assertFalse([c for c in candidates if c.kind in ("combo", "triple")])
        self.assertTrue(all(c.kind == "single" for c in candidates))

    def test_combo_with_unpriced_leg_is_partial(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        hub = (46.20, 11.75)
        access = make_offer("flixbus", "access", "bus", 3.0, base, 60, dropoff=hub)
        # Ultimo miglio solo orario (GTFS): nessun prezzo pubblico.
        last = make_offer("gtfs", "last", "bus", None, base + timedelta(minutes=90), 60, pickup=hub)
        complete = make_offer("itabus", "direct", "bus", 20.0, base, 240)
        candidates = build_candidates([access, last, complete], context(arrive_by))
        partial = next((c for c in candidates if c.partial), None)
        self.assertIsNotNone(partial)
        self.assertEqual(partial.kind, "combo")
        self.assertEqual(partial.unpriced_legs, 1)
        # Il totale e' solo la somma dei prezzi noti.
        self.assertAlmostEqual(partial.total_price, 3.0)
        # Un piano con tutti i prezzi viene comunque prima.
        self.assertEqual(candidates[0].kind, "single")
        self.assertEqual(candidates[0].total_price, 20.0)
        plan = intelligence._plan_from_candidate(partial, None)
        self.assertTrue(plan["partial"])
        self.assertIsNone(plan["steps"][1]["price"])
        self.assertTrue(any("tariffa" in w.lower() for w in plan["watchouts"]))
        self.assertIn("parziale", intelligence._templated_explanation(partial, {}).lower())

    def test_combo_of_two_unpriced_legs_is_dropped(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        hub = (46.20, 11.75)
        first = make_offer("gtfs", "first", "bus", None, base, 60, dropoff=hub)
        second = make_offer("gtfs", "second", "bus", None, base + timedelta(minutes=90), 60, pickup=hub)
        # Un'offerta con prezzo ma fuori destinazione: tiene in vita il planner.
        elsewhere = make_offer("itabus", "far", "bus", 9.0, base, 90, dropoff=(41.9, 12.5))
        candidates = build_candidates([first, second, elsewhere], context(arrive_by))
        self.assertFalse([c for c in candidates if c.kind in ("combo", "triple")])

    def test_legs_pool_unlocks_triples_dropped_by_filters(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=8)
        # Solo la tratta diretta "passa i filtri"; le tratte intermedie arrivano
        # a snodi lontani dalla destinazione e stanno nel pool grezzo.
        direct = make_offer("flixbus", "direct", "bus", 12.0, base, 240)
        pool = self._three_legs(base)
        without_pool = build_candidates([direct], context(arrive_by))
        self.assertTrue(all(c.kind == "single" for c in without_pool))
        with_pool = build_candidates([direct], context(arrive_by), pool=pool)
        self.assertTrue([c for c in with_pool if c.kind == "triple"])


class IntelligenceFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        prepara_db_di_prova(self)

        class _DisabledClient:
            available = False

            async def chat(self, *a, **kw):
                raise RuntimeError("client disabled in test")

        self._patcher = unittest.mock.patch.object(
            intelligence, "client_from_settings", lambda: _DisabledClient()
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    async def test_no_ai_still_returns_cheapest_plan(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=6)
        offers = [
            make_offer("itabus", "a", "bus", 15.0, base, 100),
            make_offer("flixbus", "b", "bus", 7.5, base, 140),
        ]
        ctx = context(arrive_by)
        watch = {
            "id": 99,
            "from_place": "Thiene",
            "to_place": "Trento",
            "arrive_by": iso(arrive_by),
            "prefs": {},
            "passengers": 1,
        }
        result = await intelligence.analyze(watch, offers, ctx, ranked=None)
        self.assertIsNotNone(result.plan)
        self.assertFalse(result.used_ai)
        self.assertAlmostEqual(result.plan["total_price"], 7.5)
        self.assertTrue(result.plan["explanation"])

    async def test_question_when_arrival_is_late(self):
        base = reference(2)
        arrive_by = base + timedelta(hours=3)
        late = make_offer("flixbus", "late", "bus", 6.0, base, 300)  # arriva dopo il limite
        ctx = context(arrive_by)
        watch = {
            "id": 100,
            "from_place": "Thiene",
            "to_place": "Trento",
            "arrive_by": iso(arrive_by),
            "prefs": {},
            "passengers": 1,
        }
        result = await intelligence.analyze(watch, [late], ctx)
        self.assertIsNone(result.plan)
        self.assertTrue(any(q["key"] == "flexible_days" for q in result.questions))
        # La domanda sugli abbonamenti ha la precedenza (se la tratta e'
        # coperta il budget non e' un problema).
        flessione = next(q for q in result.questions if q["key"] == "flexible_days")
        self.assertTrue(flessione["options"][0].get("prefs") == {"flexible_days": 0})

    async def test_parse_without_key_is_graceful(self):
        result = await intelligence.parse_trip_text("Milano -> Roma domani")
        self.assertFalse(result.get("ok"))
        self.assertIn("Groq", result.get("error", ""))


class LateWindowTests(unittest.TestCase):
    def test_flexible_days_keeps_late_offers(self):
        from app.services.search import _filter_offers

        base = reference(2)
        arrive_by = base + timedelta(hours=2)
        late = make_offer("flixbus", "late", "bus", 6.0, base, 300)
        ctx = context(arrive_by)
        self.assertEqual(len(_filter_offers([late], ctx)), 0)
        kept = _filter_offers([late], ctx, late_minutes=1440)
        self.assertEqual(len(kept), 1)
        self.assertIn("oltre_limite_min", kept[0].details)


class GroqModelTests(unittest.IsolatedAsyncioTestCase):
    """Scelta del modello Groq e gestione delle risposte vuote."""

    def test_default_models_are_real_groq_ids(self):
        from app.ai.groq import DEFAULT_FAST, DEFAULT_SMART

        # I modelli salvati in precedenza non esistono piu' nel free tier:
        # i default devono essere fra quelli pubblicati.
        self.assertIn(DEFAULT_SMART, GROQ_CHAT_MODELS)
        self.assertIn(DEFAULT_FAST, GROQ_CHAT_MODELS)

    def test_empty_model_falls_back_to_default(self):
        from app.ai.groq import DEFAULT_FAST, DEFAULT_SMART, GroqClient

        client = GroqClient("chiave", model_smart="", model_fast="  ")
        self.assertEqual(client.model_smart, DEFAULT_SMART)
        self.assertEqual(client.model_fast, DEFAULT_FAST)

    def test_model_order_puts_chosen_first(self):
        from app.ai.groq import GroqClient

        client = GroqClient("chiave", model_smart="qwen/qwen3.8-27b")
        order = client._model_order(None)
        self.assertEqual(order[0], "qwen/qwen3.8-27b")
        # I fallback restano disponibili se il primo non risponde.
        self.assertGreater(len(order), 1)

    def test_list_models_filters_non_chat_models(self):
        """Audio, embedding e filtri non devono comparire fra i modelli usabili."""
        from app.ai.groq import GroqClient

        payload = {
            "data": [
                {"id": "openai/gpt-oss-120b", "context_length": 131072},
                {"id": "whisper-large-v3", "context_length": 448},
                {"id": "meta-llama/llama-prompt-guard-2-86m", "context_length": 512},
                {"id": "openai/gpt-oss-safeguard-20b", "context_length": 131072},
            ]
        }

        class _Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return payload

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers=None):
                return _Response()

        with unittest.mock.patch("app.ai.groq.httpx.AsyncClient", return_value=_Client()):
            client = GroqClient("chiave")
            models = asyncio.run(client.list_models_detailed())

        self.assertEqual([m["id"] for m in models], ["openai/gpt-oss-120b"])
        self.assertEqual(models[0]["context_length"], 131072)

    async def test_empty_answer_is_treated_as_failure(self):
        """Un modello reasoning che esaurisce i token non deve sembrare riuscito."""
        from app.ai.groq import ChatResult, GroqClient

        client = GroqClient("chiave", model_smart="modello-che-non-risponde")
        empty = ChatResult(text="", model="modello-che-non-risponde", ok=True)

        async def fake_once(self, messages, model, json_mode, max_tokens, temperature, timeout, attempt=0):
            if model == "modello-che-non-risponde":
                return empty
            return ChatResult(text="risposta", model=model, ok=True, tokens=10)

        with unittest.mock.patch.object(GroqClient, "_once", fake_once):
            result = await client.chat([{"role": "user", "content": "ciao"}])

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "risposta")
        # Il modello vuoto e' stato scartato.
        self.assertEqual(result.model, client.model_fast)

    async def test_fallback_chain_tries_next_model(self):
        from app.ai.groq import ChatResult, GroqClient

        client = GroqClient("chiave", model_smart="primo")
        calls: list[str] = []

        async def fake_once(self, messages, model, json_mode, max_tokens, temperature, timeout, attempt=0):
            calls.append(model)
            if model == "primo":
                return ChatResult(ok=False, error="non risponde", model=model)
            return ChatResult(text="ok", model=model, ok=True)

        with unittest.mock.patch.object(GroqClient, "_once", fake_once):
            result = await client.chat([{"role": "user", "content": "ciao"}])

        self.assertTrue(result.ok)
        self.assertNotEqual(result.model, "primo")
        self.assertGreaterEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
