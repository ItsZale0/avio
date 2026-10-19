"""Test del pianificatore economico e della degradazione senza AI."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.adapters.base import Offer, SearchContext
from app.ai import intelligence
from app.ai.planner import build_candidates
from app.timeutil import ROME, iso, now_utc


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
        base = now_utc() + timedelta(hours=3)
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
        base = now_utc() + timedelta(hours=2)
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
        base = now_utc() + timedelta(hours=2)
        arrive_by = base + timedelta(hours=8)
        station = (46.0, 11.4)
        car = make_offer("carsharing", "car", "carsharing", 4.0, base, 120, dropoff=station)
        bus = make_offer("flixbus", "bus", "bus", 5.0, base + timedelta(minutes=30), 90, pickup=station)
        candidates = build_candidates([car, bus], context(arrive_by))
        self.assertFalse([c for c in candidates if c.kind == "combo"])

    def test_combo_rejected_when_transfer_too_far(self):
        base = now_utc() + timedelta(hours=2)
        arrive_by = base + timedelta(hours=8)
        car = make_offer("carsharing", "car", "carsharing", 4.0, base, 60, dropoff=(41.9, 12.5))
        bus = make_offer("flixbus", "bus", "bus", 5.0, base + timedelta(minutes=90), 90, pickup=(46.0, 11.4))
        candidates = build_candidates([car, bus], context(arrive_by), max_transfer_km=3)
        self.assertFalse([c for c in candidates if c.kind == "combo"])


class IntelligenceFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_ai_still_returns_cheapest_plan(self):
        base = now_utc() + timedelta(hours=2)
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
        base = now_utc() + timedelta(hours=2)
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
        self.assertTrue(result.questions[0]["options"][0].get("prefs") == {"flexible_days": 0})

    async def test_parse_without_key_is_graceful(self):
        result = await intelligence.parse_trip_text("Milano -> Roma domani")
        self.assertFalse(result.get("ok"))
        self.assertIn("Groq", result.get("error", ""))


class LateWindowTests(unittest.TestCase):
    def test_flexible_days_keeps_late_offers(self):
        from app.services.search import _filter_offers

        base = now_utc() + timedelta(hours=2)
        arrive_by = base + timedelta(hours=2)
        late = make_offer("flixbus", "late", "bus", 6.0, base, 300)
        ctx = context(arrive_by)
        self.assertEqual(len(_filter_offers([late], ctx)), 0)
        kept = _filter_offers([late], ctx, late_minutes=1440)
        self.assertEqual(len(kept), 1)
        self.assertIn("oltre_limite_min", kept[0].details)


if __name__ == "__main__":
    unittest.main()
