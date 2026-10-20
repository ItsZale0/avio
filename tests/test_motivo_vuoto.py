"""Il motore deve spiegare PERCHE' la ricerca e' vuota."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.ai import intelligence

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
ARRIVE = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


def make_ctx(max_price=8.0):
    return SearchContext(
        watch_id=1,
        from_place="Bressanone",
        to_place="Rovereto",
        from_lat=46.7084,
        from_lon=11.6556,
        to_lat=45.8906,
        to_lon=11.0363,
        depart_after=DEPART,
        arrive_by=ARRIVE,
        radius_km=5,
        passengers=1,
        max_price=max_price,
        prefs={},
    )


def make_offer(price=9.99, depart=DEPART, arrive=ARRIVE, operator="BlaBlaCar", external_id=""):
    return Offer(
        source="blablacar",
        external_id=external_id or f"x{price}",
        mode="rideshare",
        operator=operator,
        depart_at=depart,
        arrive_at=arrive,
        price=price,
        pickup_name="Bressanone",
        dropoff_name="Rovereto",
        pickup_lat=46.7084,
        pickup_lon=11.6556,
        dropoff_lat=45.8906,
        dropoff_lon=11.0363,
        duration_min=180,
        details={"cambi": 0, "tappe": []},
    )


class MotivoTests(unittest.TestCase):
    def test_the_budget_is_named(self):
        ctx = make_ctx(max_price=8.0)
        offers = [make_offer(price=9.99)]
        testo = intelligence._motivo_vincolo(offers, ctx, {})
        self.assertIn("8.00", testo)
        self.assertIn("9.99", testo)
        self.assertIn("budget", testo.lower())

    def test_the_cheapest_is_named(self):
        ctx = make_ctx(max_price=8.0)
        offers = [make_offer(price=9.99), make_offer(price=12.5, external_id="y")]
        testo = intelligence._motivo_vincolo(offers, ctx, {})
        self.assertIn("9.99", testo)
        self.assertNotIn("12.50", testo)

    def test_when_nothing_arrives_in_time(self):
        ctx = make_ctx(max_price=None)
        offers = [
            make_offer(
                price=9.99,
                depart=DEPART + timedelta(hours=10),
                arrive=ARRIVE + timedelta(hours=10),
            )
        ]
        testo = intelligence._motivo_vincolo(offers, ctx, {})
        self.assertIn("orario", testo.lower())

    def test_within_budget_there_is_no_complaint(self):
        ctx = make_ctx(max_price=20.0)
        offers = [make_offer(price=9.99)]
        testo = intelligence._motivo_vincolo(offers, ctx, {})
        self.assertIn("vincoli", testo.lower())


if __name__ == "__main__":
    unittest.main()
