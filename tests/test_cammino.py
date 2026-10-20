"""Il cammino a piedi: distanza reale, stazione vera, uscita di casa."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.ai import planner
from app.geo import walking_km, walking_minutes

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
ARRIVE = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)

# La stazione di Thiene e il centro di Thiene.
STAZIONE = (45.6797, 11.4783)
CENTRO = (45.6877, 11.4861)


def make_ctx(from_point=None, to_point=None):
    return SearchContext(
        watch_id=1,
        from_place="Thiene",
        to_place="Trento",
        from_lat=CENTRO[0],
        from_lon=CENTRO[1],
        to_lat=46.1030,
        to_lon=11.1297,
        depart_after=DEPART,
        arrive_by=ARRIVE,
        radius_km=5,
        passengers=1,
        max_price=None,
        prefs={},
        from_point_lat=from_point[0] if from_point else None,
        from_point_lon=from_point[1] if from_point else None,
        to_point_lat=to_point[0] if to_point else None,
        to_point_lon=to_point[1] if to_point else None,
    )


def make_offer(pickup=STAZIONE, dropoff=(46.0722, 11.1193)):
    return Offer(
        source="trenitalia",
        external_id="t1",
        mode="train",
        operator="Regionale",
        depart_at=DEPART,
        arrive_at=ARRIVE,
        price=13.1,
        pickup_name="Thiene",
        pickup_lat=pickup[0],
        pickup_lon=pickup[1],
        dropoff_name="Trento",
        dropoff_lat=dropoff[0],
        dropoff_lon=dropoff[1],
        duration_min=180,
        details={"cambi": 0, "treni": "Regionale", "tappe": []},
    )


class DetourTests(unittest.TestCase):
    def test_the_walk_is_longer_than_the_straight_line(self):
        # 1 km in linea d'aria sono circa 1,25 km a piedi.
        self.assertAlmostEqual(walking_km(1.0), 1.25, places=3)

    def test_a_few_metres_stay_the_same(self):
        self.assertAlmostEqual(walking_km(0.05), 0.05, places=3)

    def test_none_stays_none(self):
        self.assertIsNone(walking_km(None))

    def test_the_minutes_follow_the_walking_distance(self):
        # 1,25 km a 4,5 km/h sono 17 minuti, non 13.
        self.assertEqual(walking_minutes(walking_km(1.0)), 17)


class WalkTimesTests(unittest.TestCase):
    def test_the_walk_to_the_pickup_is_counted(self):
        from app.geo import haversine_km

        ctx = make_ctx()
        out_min, in_min, out_km, in_km = planner._walk_times([make_offer()], ctx)
        self.assertGreater(out_min, 0)
        dritta = haversine_km(CENTRO[0], CENTRO[1], STAZIONE[0], STAZIONE[1])
        self.assertAlmostEqual(out_km, walking_km(dritta), places=3)
        # E il cammino e' piu' lungo della retta.
        self.assertGreater(out_km, dritta)

    def test_no_walk_when_the_station_is_the_destination(self):
        # L'utente sceglie la stazione di Trento come destinazione: il
        # treno arriva li', quindi non c'e' cammino.
        stazione_trento = (46.0722, 11.1193)
        ctx = make_ctx(to_point=stazione_trento)
        offer = make_offer(dropoff=stazione_trento)
        out_min, in_min, out_km, in_km = planner._walk_times([offer], ctx)
        self.assertEqual(in_min, 0)
        self.assertAlmostEqual(in_km or 0.0, 0.0, places=3)

    def test_the_walk_is_not_counted_backwards(self):
        # Se la destinazione e' la stazione, il centro citta' non c'entra.
        stazione_trento = (46.0722, 11.1193)
        ctx = make_ctx(to_point=stazione_trento)
        offer = make_offer(dropoff=(46.1030, 11.1297))  # centro citta'
        _, in_min, _, _ = planner._walk_times([offer], ctx)
        self.assertGreater(in_min, 0)


class LeaveHomeTests(unittest.TestCase):
    def test_the_plan_says_when_to_leave_home(self):
        ctx = make_ctx()
        offer = make_offer()
        candidates = planner.build_candidates([offer], ctx)
        self.assertTrue(candidates)
        piano = candidates[0].as_dict()
        self.assertTrue(piano["walk_out_min"] > 0)
        # Si esce prima: il treno parte dopo i minuti di cammino.
        partenza = datetime.fromisoformat(piano["depart_at"])
        uscita = datetime.fromisoformat(piano["leave_home_at"])
        self.assertEqual((partenza - uscita).total_seconds() / 60, piano["walk_out_min"])


class TrenitaliaStationTests(unittest.TestCase):
    def test_the_offer_uses_the_station_coordinates(self):
        from app.adapters.trenitalia import TrenitaliaAdapter

        adapter = TrenitaliaAdapter()
        ctx = make_ctx()
        stazione = {
            "id": "x",
            "name": "Thiene",
            "lat": STAZIONE[0],
            "lon": STAZIONE[1],
        }
        arrivo = {"id": "y", "name": "Trento", "lat": 46.0722, "lon": 11.1193}
        data = {
            "solutions": [
                {
                    "solution": {
                        "id": "s1",
                        "status": "SALEABLE",
                        "price": {"amount": 13.1},
                        "departureTime": "2026-10-22T08:00:00+02:00",
                        "arrivalTime": "2026-10-22T10:00:00+02:00",
                        "trains": [{"trainCategory": "REG"}],
                        "origin": "Thiene",
                        "destination": "Trento",
                    }
                }
            ]
        }
        offerte = adapter._parse(
            data, ctx, stazione, arrivo, from_shifted=False, to_shifted=False, from_km=0, to_km=0
        )
        self.assertTrue(offerte)
        self.assertAlmostEqual(offerte[0].pickup_lat, STAZIONE[0], places=4)
        self.assertAlmostEqual(offerte[0].pickup_lon, STAZIONE[1], places=4)
        self.assertAlmostEqual(offerte[0].dropoff_lat, 46.0722, places=4)

    def test_without_coordinates_it_falls_back_to_the_city(self):
        from app.adapters.trenitalia import TrenitaliaAdapter

        adapter = TrenitaliaAdapter()
        ctx = make_ctx()
        stazione = {"id": "x", "name": "Thiene", "lat": None, "lon": None}
        arrivo = {"id": "y", "name": "Trento", "lat": None, "lon": None}
        data = {
            "solutions": [
                {
                    "solution": {
                        "id": "s1",
                        "status": "SALEABLE",
                        "price": {"amount": 13.1},
                        "departureTime": "2026-10-22T08:00:00+02:00",
                        "arrivalTime": "2026-10-22T10:00:00+02:00",
                        "trains": [{"trainCategory": "REG"}],
                    }
                }
            ]
        }
        offerte = adapter._parse(
            data, ctx, stazione, arrivo, from_shifted=False, to_shifted=False, from_km=0, to_km=0
        )
        self.assertTrue(offerte)
        self.assertAlmostEqual(offerte[0].pickup_lat, ctx.from_lat, places=4)


if __name__ == "__main__":
    unittest.main()
