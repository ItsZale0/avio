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

    def test_drops_over_budget(self):
        ctx = make_context(max_price=15)
        self.assertEqual(_filter_offers([make_offer(price=25)], ctx), [])

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


if __name__ == "__main__":
    unittest.main()
