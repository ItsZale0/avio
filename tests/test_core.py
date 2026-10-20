"""Test di base per logica pura: geo, filtri, scoring, link."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.geo import haversine_km, offline_lookup
from app.scoring import score_offers
from app.services.promos import _extract_offers
from app.services.search import _filter_offers
from app.store import links_for_watch


def make_context(**overrides) -> SearchContext:
    now = datetime.now(timezone.utc)
    data = dict(
        watch_id=1,
        from_place="Milano",
        to_place="Roma",
        from_lat=45.4642,
        from_lon=9.1900,
        to_lat=41.9028,
        to_lon=12.4964,
        depart_after=now + timedelta(hours=1),
        arrive_by=now + timedelta(hours=8),
        radius_km=5,
        passengers=1,
        max_price=50,
    )
    data.update(overrides)
    return SearchContext(**data)


def make_offer(price=20.0, **overrides) -> Offer:
    ctx = make_context()
    base = dict(
        source="test",
        external_id=f"x-{price}",
        mode="rideshare",
        operator="Test",
        depart_at=ctx.depart_after + timedelta(minutes=10),
        arrive_at=ctx.arrive_by - timedelta(hours=2),
        price=price,
        pickup_name="Milano",
        pickup_lat=45.4642,
        pickup_lon=9.1900,
        dropoff_name="Roma",
        dropoff_lat=41.9028,
        dropoff_lon=12.4964,
        duration_min=300,
    )
    base.update(overrides)
    return Offer(**base)


class GeoTest(unittest.TestCase):
    def test_haversine_milano_roma(self):
        distance = haversine_km(45.4642, 9.1900, 41.9028, 12.4964)
        self.assertGreater(distance, 450)
        self.assertLess(distance, 500)

    def test_offline_lookup(self):
        result = offline_lookup("Milano Centrale")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], 45.4642, places=2)

    def test_offline_lookup_unknown(self):
        self.assertIsNone(offline_lookup("Atlantide"))


class FilterTest(unittest.TestCase):
    def test_keeps_valid_offer(self):
        ctx = make_context()
        self.assertEqual(len(_filter_offers([make_offer()], ctx)), 1)

    def test_drops_over_budget_when_something_fits(self):
        # Se un'offerta sta nel budget, quelle fuori budget vengono scartate.
        ctx = make_context(max_price=15)
        offer = make_offer(price=25)
        cheap = make_offer(price=10, external_id="x-cheap")
        kept = _filter_offers([offer, cheap], ctx)
        self.assertEqual([o.external_id for o in kept], ["x-cheap"])

    def test_drops_over_budget_always(self):
        # Fuori budget si scarta sempre: l'utente vede solo i prezzi che
        # stanno dentro i filtri. Il prezzo reale della soluzione piu'
        # economica lo comunico la domanda "vuoi alzare il budget?".
        ctx = make_context(max_price=15)
        self.assertEqual(_filter_offers([make_offer(price=25)], ctx), [])

    def test_drops_offer_from_previous_day(self):
        # "Devo essere a Trento entro le 11 di quel giorno": un arrivo del
        # giorno prima non vale.
        ctx = make_context()
        ieri = make_offer(
            arrive_at=ctx.arrive_by - timedelta(days=1),
            depart_at=ctx.depart_after - timedelta(days=1),
        )
        self.assertEqual(_filter_offers([ieri], ctx), [])

    def test_keeps_offer_of_the_same_day(self):
        ctx = make_context()
        same_day = make_offer(
            arrive_at=ctx.arrive_by - timedelta(hours=2),
            depart_at=ctx.arrive_by - timedelta(hours=4),
        )
        self.assertEqual(len(_filter_offers([same_day], ctx)), 1)

    def test_drops_late_arrival(self):
        ctx = make_context()
        late = make_offer(arrive_at=ctx.arrive_by + timedelta(hours=1))
        self.assertEqual(_filter_offers([late], ctx), [])

    def test_drops_too_early_departure(self):
        ctx = make_context()
        early = make_offer(depart_at=ctx.depart_after - timedelta(hours=2))
        self.assertEqual(_filter_offers([early], ctx), [])

    def test_drops_far_pickup(self):
        ctx = make_context(radius_km=2)
        far = make_offer(pickup_lat=45.9, pickup_lon=9.5)
        self.assertEqual(_filter_offers([far], ctx), [])

    def test_keeps_transit_without_times(self):
        ctx = make_context()
        transit = make_offer(
            price=None,
            mode="transit",
            depart_at=None,
            arrive_at=None,
            pickup_lat=ctx.from_lat,
            pickup_lon=ctx.from_lon,
        )
        self.assertEqual(len(_filter_offers([transit], ctx)), 1)


class ScoringTest(unittest.TestCase):
    def test_cheapest_and_best_value(self):
        ctx = make_context()
        offers = [make_offer(price=10, external_id="a"), make_offer(price=25, external_id="b")]
        ranked = score_offers(offers, ctx)
        cheap = ranked[offers[0].fingerprint]
        self.assertIn("cheapest", cheap["badges"])
        self.assertGreater(cheap["score"], 60)
        self.assertEqual(cheap["details"]["costo_accesso_stimato"], 0.0)

    def test_estimate_does_not_take_cheapest_badge(self):
        ctx = make_context()
        normal = make_offer(price=20, external_id="normal")
        estimate = make_offer(price=5, external_id="estimate", price_is_estimate=True)
        ranked = score_offers([normal, estimate], ctx)
        self.assertNotIn("cheapest", ranked[estimate.fingerprint]["badges"])
        self.assertIn("cheapest", ranked[normal.fingerprint]["badges"])
        self.assertIn("estimate", ranked[estimate.fingerprint]["badges"])

    def test_transit_has_no_score(self):
        ctx = make_context()
        transit = make_offer(price=None, mode="transit", depart_at=None, arrive_at=None)
        ranked = score_offers([transit], ctx)
        self.assertIsNone(ranked[transit.fingerprint]["score"])

    def test_effective_cost_includes_pickup(self):
        ctx = make_context()
        far = make_offer(pickup_lat=45.55, pickup_lon=9.25)
        ranked = score_offers([far], ctx)
        details = ranked[far.fingerprint]["details"]
        self.assertGreater(details["costo_accesso_stimato"], 0)
        self.assertGreater(details["costo_effettivo"], far.price)


class LinksTest(unittest.TestCase):
    def test_links_include_main_providers(self):
        watch = {
            "id": 1,
            "from_place": "Milano",
            "to_place": "Roma",
            "depart_after": None,
            "arrive_by": datetime.now(timezone.utc).isoformat(),
            "from_lat": 45.4642,
            "from_lon": 9.19,
            "to_lat": 41.9028,
            "to_lon": 12.4964,
            "passengers": 1,
        }
        links = links_for_watch(watch)
        ids = {link["id"] for link in links}
        for expected in ("blablacar", "flixbus", "omio", "trenitalia", "gmaps"):
            self.assertIn(expected, ids)
        blablacar = next(link for link in links if link["id"] == "blablacar")
        self.assertIn("Milano", blablacar["url"])
        self.assertIn("Roma", blablacar["url"])

    def test_links_include_discount_pages(self):
        watch = {
            "id": 1,
            "from_place": "Milano",
            "to_place": "Roma",
            "depart_after": None,
            "arrive_by": datetime.now(timezone.utc).isoformat(),
            "passengers": 1,
        }
        ids = {link["id"] for link in links_for_watch(watch)}
        self.assertIn("codici-italo", ids)
        self.assertIn("codici-treni", ids)


class PromosTest(unittest.TestCase):
    def test_extract_offers_filters_nav_and_resolves_urls(self):
        page = {
            "operator": "Test",
            "url": "https://example.com/offerte",
            "href": r"^https://example\.com/offerte/[a-z0-9\-]+/?$",
            "deny": {"offerte treni"},
        }
        body = """
        <a href="/offerte/treni-last-minute">Treni last minute</a>
        <a href="/offerte">Offerte treni</a>
        <a href="https://example.com/altro">Altro</a>
        <a href="/offerte/studenti">Sconti per Studenti su Treni e Pullman</a>
        """
        offers = _extract_offers(page, body)
        titles = [offer["title"] for offer in offers]
        self.assertIn("Treni last minute", titles)
        self.assertIn("Sconti per Studenti su Treni e Pullman", titles)
        self.assertNotIn("Offerte treni", titles)
        self.assertTrue(all(offer["operator"] == "Test" for offer in offers))
        self.assertEqual(
            offers[0]["url"], "https://example.com/offerte/treni-last-minute"
        )


class GtfsTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from app.gtfs import db as gtfsdb

        self._Path = Path
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = gtfsdb.GTFS_DB_PATH
        gtfsdb.GTFS_DB_PATH = Path(self._tmp.name) / "gtfs.db"
        gtfsdb.init()

    def tearDown(self):
        from app.gtfs import db as gtfsdb

        gtfsdb.GTFS_DB_PATH = self._old_path
        self._tmp.cleanup()

    def _feed_zip(self):
        import io
        import zipfile

        files = {
            "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
            "A1,Azienda Test,https://example.org,Europe/Rome\n",
            "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
            "S1,Alfa,45.0000,11.0000\nS2,Beta,45.1000,11.1000\nS3,Gamma,45.2000,11.2000\n",
            "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
            "R1,A1,101,Alfa-Beta-Gamma,3\n",
            "trips.txt": "route_id,service_id,trip_id,trip_headsign\nR1,WK,T1,Gamma\n",
            "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,08:00:00,08:00:00,S1,1\nT1,08:30:00,08:30:00,S2,2\nT1,09:00:00,09:00:00,S3,3\n",
            "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\nWK,1,1,1,1,1,1,1,20260101,20261231\n",
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        path = self._Path(self._tmp.name) / "feed.zip"
        path.write_bytes(buffer.getvalue())
        return path

    def test_import_and_direct_connection(self):
        from app.gtfs import engine, importer

        importer.import_feed("test-feed", self._feed_zip())
        journeys = engine.find_connections(
            feeds=["test-feed"],
            from_lat=45.0, from_lon=11.0, to_lat=45.2, to_lon=11.2,
            day=datetime(2026, 6, 15).date(), radius_km=3.0,
        )
        self.assertTrue(journeys)
        self.assertEqual(journeys[0]["legs"][0]["dep_name"], "Alfa")
        self.assertEqual(journeys[0]["legs"][-1]["arr_name"], "Gamma")

    def test_pareto_drops_dominated(self):
        from app.gtfs import engine

        def journey(dep, arr):
            return {
                "transfers": 0,
                "legs": [{"trip_id": f"t{dep}", "dep_sec": dep}, {"trip_id": f"t{arr}", "arr_sec": arr}],
            }

        kept = engine._pareto([journey(100, 200), journey(100, 300), journey(150, 250)])
        arrivals = sorted(item["legs"][-1]["arr_sec"] for item in kept)
        self.assertEqual(arrivals, [200, 250])

    def _walk_feed_zip(self):
        """Due linee che si incontrano solo cambiando tra due fermate a pochi metri."""
        import io
        import zipfile

        files = {
            "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
            "A1,Azienda Test,https://example.org,Europe/Rome\n",
            "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
            "S1,Alfa,45.0000,11.0000\n"
            "S2,Beta,45.1000,11.1000\n"
            "S4,Beta Stazione,45.1005,11.1005\n"
            "S5,Delta,45.2500,11.2500\n",
            "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
            "R1,A1,101,Alfa-Beta,3\nR2,A1,202,Beta-Delta,3\n",
            "trips.txt": "route_id,service_id,trip_id,trip_headsign\n"
            "R1,WK,T1,Beta\nR2,WK,T2,Delta\n",
            "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,08:00:00,08:00:00,S1,1\nT1,08:30:00,08:30:00,S2,2\n"
            "T2,08:40:00,08:40:00,S4,1\nT2,09:10:00,09:10:00,S5,2\n",
            "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\nWK,1,1,1,1,1,1,1,20260101,20261231\n",
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        path = self._Path(self._tmp.name) / "walk.zip"
        path.write_bytes(buffer.getvalue())
        return path

    def test_transfer_between_nearby_stops(self):
        from app.gtfs import engine, importer

        importer.import_feed("walk-feed", self._walk_feed_zip())
        journeys = engine.find_connections(
            feeds=["walk-feed"],
            from_lat=45.0, from_lon=11.0, to_lat=45.25, to_lon=11.25,
            day=datetime(2026, 6, 15).date(), radius_km=2.0,
        )
        self.assertTrue(journeys)
        with_change = [item for item in journeys if item["transfers"] == 1]
        self.assertTrue(with_change)
        second = with_change[0]["legs"][1]
        self.assertEqual(second["dep_name"], "Beta Stazione")
        self.assertGreater(second.get("walk_km") or 0, 0)


class GtfsScoringTest(unittest.TestCase):
    def test_schedule_gets_score_without_price(self):
        ctx = make_context()
        schedule = make_offer(
            price=None, source="gtfs", external_id="s1", mode="bus", details={"orario_gtfs": True}
        )
        ranked = score_offers([schedule], ctx)
        self.assertIsNotNone(ranked[schedule.fingerprint]["score"])
        self.assertIn("orario", ranked[schedule.fingerprint]["badges"])


class PushTest(unittest.TestCase):
    """Notifiche push: iscrizioni dei dispositivi e canale 'push' negli avvisi."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from app import db

        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "push.db"
        db.init_db()

    def tearDown(self):
        from app import db

        db.DB_PATH = self._old_path
        self._tmp.cleanup()

    def _subscription(self, endpoint="https://push.example/1", p256dh="x" * 20):
        return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": "y" * 12}, "label": "telefono"}

    def test_subscribe_resubscribe_and_unsubscribe(self):
        from app.services import push

        self.assertEqual(push.count(), 0)
        self.assertEqual(push.subscribe(self._subscription()), 1)
        # stesso endpoint: aggiorna, non duplica
        push.subscribe(self._subscription(p256dh="z" * 20))
        self.assertEqual(push.count(), 1)
        self.assertEqual(push.subscriptions()[0]["p256dh"], "z" * 20)
        self.assertEqual(push.unsubscribe("https://push.example/1"), 0)
        self.assertEqual(push.count(), 0)

    def test_subscribe_needs_keys(self):
        from app.services import push

        with self.assertRaises(ValueError):
            push.subscribe({"endpoint": "https://push.example/2", "keys": {}})

    def test_public_key_is_base64url(self):
        from app.services import push

        if not push.available():
            self.skipTest("pywebpush non installato")
        key = push.public_key()
        self.assertTrue(key)
        self.assertNotIn("=", key)
        self.assertTrue(all(char.isalnum() or char in "-_" for char in key))

    def test_send_without_devices_does_not_raise(self):
        import asyncio

        from app import notify

        results = asyncio.run(notify.send_push("testo", "oggetto", ["push"]))
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertIn("nessun dispositivo", results[0]["error"].lower())


if __name__ == "__main__":
    unittest.main()
