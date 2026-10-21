"""Il cammino a piedi: percorso reale dalle mappe, solo da punti esatti."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.ai import planner
from app.routing import percorso_a_piedi

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


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    """Il percorso a piedi arriva dal servizio di mappe, non da una formula."""

    async def asyncSetUp(self):
        from app import routing

        self.routing = routing
        self._fetch = routing._fetch
        self._cache = routing._CACHE
        routing._CACHE = {}

    async def asyncTearDown(self):
        self.routing._fetch = self._fetch
        self.routing._CACHE = self._cache

    async def test_usa_il_percorso_reale_delle_mappe(self):
        async def finto(url):
            return {"routes": [{"distance": 2044.8, "duration": 1638}]}

        self.routing._fetch = finto
        percorso = await percorso_a_piedi(
            CENTRO[0], CENTRO[1], STAZIONE[0], STAZIONE[1]
        )
        self.assertIsNotNone(percorso)
        self.assertAlmostEqual(percorso["km"], 2.04, places=2)
        self.assertEqual(percorso["minuti"], 27)

    async def test_se_il_servizio_non_risponde_non_si_inventa(self):
        async def finto(url):
            raise RuntimeError("servizio non raggiungibile")

        self.routing._fetch = finto
        self.assertIsNone(
            await percorso_a_piedi(CENTRO[0], CENTRO[1], STAZIONE[0], STAZIONE[1])
        )

    async def test_senza_coordinate_non_c_e_percorso(self):
        self.assertIsNone(await percorso_a_piedi(None, 11.4, 45.7, 11.4))

    async def test_pochi_metri_sono_zero(self):
        percorso = await percorso_a_piedi(CENTRO[0], CENTRO[1], CENTRO[0], CENTRO[1])
        self.assertEqual(percorso, {"km": 0.0, "minuti": 0})


class WalkTimesTests(unittest.TestCase):
    def test_usa_i_minuti_del_percorso_reale(self):
        offer = make_offer()
        offer.details["piedi_pickup"] = {"km": 1.1, "minuti": 15}
        out_min, in_min, out_km, in_km = planner._walk_times([offer], make_ctx())
        self.assertEqual(out_min, 15)
        self.assertAlmostEqual(out_km, 1.1)
        self.assertEqual(in_min, 0)

    def test_senza_percorso_reale_non_si_stima(self):
        # L'utente ha indicato solo la citta': il centro citta' non e' la
        # sua porta di casa, quindi non si aggiunge nessun cammino.
        out_min, in_min, out_km, in_km = planner._walk_times([make_offer()], make_ctx())
        self.assertEqual(out_min, 0)
        self.assertEqual(in_min, 0)
        self.assertIsNone(out_km)
        self.assertIsNone(in_km)


class LeaveHomeTests(unittest.TestCase):
    def test_the_plan_says_when_to_leave_home(self):
        ctx = make_ctx()
        offer = make_offer()
        offer.details["piedi_pickup"] = {"km": 1.1, "minuti": 15}
        candidates = planner.build_candidates([offer], ctx)
        self.assertTrue(candidates)
        piano = candidates[0].as_dict()
        self.assertEqual(piano["walk_out_min"], 15)
        # Si esce prima: il treno parte dopo i minuti di cammino.
        partenza = datetime.fromisoformat(piano["depart_at"])
        uscita = datetime.fromisoformat(piano["leave_home_at"])
        self.assertEqual((partenza - uscita).total_seconds() / 60, piano["walk_out_min"])


class AnnotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_annota_solo_con_un_punto_esatto(self):
        from app import routing

        chiamate: list[tuple] = []

        async def finto(lat1, lon1, lat2, lon2):
            chiamate.append((lat1, lon1, lat2, lon2))
            return {"km": 0.4, "minuti": 5}

        originale = routing.percorso_a_piedi
        routing.percorso_a_piedi = finto

        async def ripristina():
            routing.percorso_a_piedi = originale

        self.addAsyncCleanup(ripristina)

        offer = make_offer()
        await routing.annota_offerte([offer], make_ctx())
        # Solo la citta': nessun cammino inventato dal centro.
        self.assertNotIn("piedi_pickup", offer.details)
        self.assertEqual(chiamate, [])

        ctx = make_ctx(from_point=(45.7000, 11.4800))
        await routing.annota_offerte([offer], ctx)
        self.assertIn("piedi_pickup", offer.details)
        self.assertEqual(offer.details["piedi_pickup"]["minuti"], 5)
        self.assertEqual(len(chiamate), 1)


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
