"""Test dei punti di ritrovo, del tempo a piedi e di BlaBlaCar.

Senza API pubblica, BlaBlaCar non puo' dare prezzi reali: qui si verifica
che la stima sia dichiarata come tale e che il link vero ci sia.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.adapters.blablacar import BlaBlaCarAdapter
from app.ai import intelligence
from app.ai.planner import build_candidates
from app.geo import walking_minutes, walking_text

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
ARRIVE = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)


def make_ctx(**overrides) -> SearchContext:
    data = dict(
        watch_id=1,
        from_place="Thiene",
        to_place="Trento",
        from_lat=45.6877,
        from_lon=11.4861,
        to_lat=46.1029,
        to_lon=11.1297,
        depart_after=DEPART,
        arrive_by=ARRIVE,
        radius_km=5,
        passengers=1,
        max_price=30,
    )
    data.update(overrides)
    return SearchContext(**data)


class WalkingTests(unittest.TestCase):
    def test_one_kilometer_is_about_thirteen_minutes(self):
        self.assertEqual(walking_minutes(1.0), 13)

    def test_nothing_to_walk(self):
        self.assertEqual(walking_minutes(0.0), 0)
        self.assertEqual(walking_minutes(0.01), 0)

    def test_unknown_distance(self):
        self.assertIsNone(walking_minutes(None))

    def test_text(self):
        self.assertIn("min a piedi", walking_text(1.0))
        self.assertEqual(walking_text(None), "")


class BlaBlaCarTests(unittest.TestCase):
    def _search(self, **overrides):
        import asyncio

        ctx = make_ctx(**overrides)
        result = asyncio.run(BlaBlaCarAdapter().search(ctx))
        return ctx, result

    def test_it_produces_an_estimate_with_the_real_link(self):
        ctx, result = self._search()
        self.assertTrue(result.offers)
        offer = result.offers[0]
        # Il prezzo e' una stima: mai presentata come prezzo reale.
        self.assertTrue(offer.price_is_estimate)
        self.assertTrue(offer.details.get("stima"))
        self.assertIn("blablacar.it/search", offer.deeplink or "")
        self.assertIn("Thiene", offer.deeplink or "")

    def test_the_estimate_grows_with_the_distance(self):
        _, corto = self._search()
        # Una destinazione molto piu' lontana (Bologna) costa di piu'.
        _, lungo = self._search(to_lat=44.4949, to_lon=11.3426)
        self.assertGreater(
            lungo.offers[0].price, corto.offers[0].price
        )

    def test_the_pickup_is_the_requested_point(self):
        ctx, result = self._search(
            from_point_lat=45.70, from_point_lon=11.47
        )
        offer = result.offers[0]
        self.assertAlmostEqual(offer.pickup_lat, 45.70, places=3)
        # Il cammino dal punto richiesto e' zero: il passaggio parte da li'.
        self.assertEqual(offer.pickup_distance_km(ctx), 0)

    def test_it_says_it_is_a_guess(self):
        _, result = self._search()
        self.assertTrue(
            any("stim" in str(h).lower() for h in result.hints),
            result.hints,
        )


class DoorToDoorTests(unittest.TestCase):
    def _offer(self, pickup_lat, pickup_lon, price=12.0, external_id="t1"):
        return Offer(
            source="trenitalia",
            external_id=external_id,
            mode="train",
            operator="Treno",
            depart_at=DEPART,
            arrive_at=ARRIVE,
            price=price,
            pickup_name="Thiene",
            pickup_lat=pickup_lat,
            pickup_lon=pickup_lon,
            dropoff_name="Trento",
            dropoff_lat=46.1029,
            dropoff_lon=11.1297,
            duration_min=240,
            details={"cambi": 1, "tappe": []},
        )

    def test_the_walk_is_added_to_the_total(self):
        # Il treno parte 3 km piu' lontano dal punto richiesto.
        ctx = make_ctx(from_point_lat=45.6877, from_point_lon=11.4861)
        offer = self._offer(45.72, 11.50)
        candidates = build_candidates([offer], ctx)
        self.assertTrue(candidates)
        candidate = candidates[0]
        self.assertGreater(candidate.walk_out_min, 0)
        self.assertEqual(
            candidate.door_to_door_min,
            candidate.total_duration_min + candidate.walk_out_min + candidate.walk_in_min,
        )

    def test_without_a_point_the_walk_is_zero(self):
        ctx = make_ctx()
        offer = self._offer(45.6877, 11.4861)
        candidates = build_candidates([offer], ctx)
        self.assertEqual(candidates[0].walk_out_min, 0)
        self.assertEqual(
            candidates[0].door_to_door_min, candidates[0].total_duration_min
        )


class PointQuestionTests(unittest.TestCase):
    def _rideshare(self):
        return Offer(
            source="blablacar",
            external_id="r1",
            mode="rideshare",
            operator="Auto",
            depart_at=DEPART,
            arrive_at=ARRIVE,
            price=5.0,
            pickup_name="Thiene",
            pickup_lat=45.6877,
            pickup_lon=11.4861,
            dropoff_name="Trento",
            dropoff_lat=46.1029,
            dropoff_lon=11.1297,
            duration_min=60,
            details={"cambi": 0, "tappe": []},
        )

    def test_it_asks_where_to_pick_up(self):
        ctx = make_ctx()
        watch = {"from_place": "Thiene", "to_place": "Trento", "id": 1}
        questions = intelligence._point_questions([self._rideshare()], ctx, watch)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["key"], "punto_partenza")

    def test_it_does_not_ask_twice(self):
        ctx = make_ctx()
        watch = {
            "from_place": "Thiene", "to_place": "Trento", "id": 1,
            "from_point": "Stazione di Thiene",
        }
        questions = intelligence._point_questions([self._rideshare()], ctx, watch)
        self.assertEqual(questions, [])

    def test_no_ask_without_a_pickup_point(self):
        ctx = make_ctx()
        watch = {"from_place": "Thiene", "to_place": "Trento", "id": 1}
        treno = self._rideshare()
        treno.mode = "train"
        questions = intelligence._point_questions([treno], ctx, watch)
        self.assertEqual(questions, [])

    def test_the_answer_sets_the_point(self):
        import asyncio

        result = asyncio.run(
            intelligence.interpret_answer(
                {"key": "punto_partenza", "text": "Dove?", "options": []},
                "Piazza Vittoria, Thiene",
            )
        )
        self.assertEqual(result["prefs"]["set_from_point"], "Piazza Vittoria, Thiene")


if __name__ == "__main__":
    unittest.main()
