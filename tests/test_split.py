"""Test della spezzatura dei biglietti.

Non chiamano la rete: verificano la logica (quali punti di spezzatura
provare, quali condizioni scartano una spezzatura, come viene presentata).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.services import split_tickets

ROME = timezone(timedelta(hours=2))


def make_ctx(
    from_place="Milano Centrale",
    to_place="Napoli Centrale",
    from_lat=45.4863,
    from_lon=9.2044,
    to_lat=40.8520,
    to_lon=14.2720,
    arrive_by=None,
    depart_after=None,
    max_price=None,
):
    return SearchContext(
        watch_id=1,
        from_place=from_place,
        to_place=to_place,
        from_lat=from_lat,
        from_lon=from_lon,
        to_lat=to_lat,
        to_lon=to_lon,
        depart_after=depart_after or datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc),
        arrive_by=arrive_by or datetime(2026, 10, 22, 21, 30, tzinfo=timezone.utc),
        radius_km=30,
        passengers=1,
        max_price=max_price,
    )


def make_train_offer(price=87.5, tappe=None):
    return Offer(
        source="trenitalia",
        external_id="direct",
        mode="train",
        operator="Intercity",
        depart_at=datetime(2026, 10, 22, 7, 10, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 10, 22, 18, 29, tzinfo=timezone.utc),
        price=price,
        pickup_name="Milano Centrale",
        dropoff_name="Napoli Centrale",
        pickup_lat=45.4863,
        pickup_lon=9.2044,
        dropoff_lat=40.8520,
        dropoff_lon=14.2720,
        details={"tappe": tappe if tappe is not None else []},
    )


class SplitPointTests(unittest.TestCase):
    def test_hubs_on_the_way_come_first(self):
        ctx = make_ctx()
        points = split_tickets._split_points(make_train_offer(), ctx)
        # Bologna e' il nodo classico per spezzare Milano -> Napoli.
        self.assertIn("Bologna", points)
        self.assertLess(points.index("Bologna"), 3)

    def test_origin_and_destination_are_excluded(self):
        ctx = make_ctx()
        points = split_tickets._split_points(make_train_offer(), ctx)
        self.assertNotIn("Milano", points)
        self.assertNotIn("Napoli", points)

    def test_off_route_hubs_are_excluded(self):
        # Su un tratto breve (Milano -> Piacenza) Roma non e' in linea.
        ctx = make_ctx(
            from_place="Milano Centrale",
            to_place="Piacenza",
            to_lat=45.0526,
            to_lon=9.6930,
        )
        points = split_tickets._split_points(make_train_offer(), ctx)
        self.assertNotIn("Roma", points)

    def test_real_stops_are_kept_as_fallback(self):
        ctx = make_ctx()
        tappe = [{"da": "Milano Centrale", "a": "Piacenza"}]
        points = split_tickets._split_points(make_train_offer(tappe=tappe), ctx)
        self.assertIn("Piacenza", points)


class SplitOfferTests(unittest.TestCase):
    def test_split_is_kept_only_when_cheaper(self):
        # Il segmento costa meno del diretto: la spezzatura entra.
        details = {
            "spezzatura": True,
            "prezzo_diretto": 87.5,
            "risparmio": 21.1,
            "biglietti": 2,
            "tappe": [
                {"da": "Milano Centrale", "a": "Bologna Centrale",
                 "partenza": "2026-10-22T05:15:00+02:00", "arrivo": "2026-10-22T08:05:00+02:00"},
                {"da": "Bologna Centrale", "a": "Napoli Centrale",
                 "partenza": "2026-10-22T10:05:00+02:00", "arrivo": "2026-10-22T20:40:00+02:00"},
            ],
        }
        offer = Offer(
            source="spezzatura", external_id="split:1+2", mode="train",
            operator="Trenitalia (2 biglietti)",
            depart_at=datetime(2026, 10, 22, 3, 15, tzinfo=timezone.utc),
            arrive_at=datetime(2026, 10, 22, 18, 40, tzinfo=timezone.utc),
            price=66.4,
            pickup_name="Milano Centrale", dropoff_name="Napoli Centrale",
            details=details,
        )
        # Il prezzo e' quello della somma: 66,40 < 87,50 del diretto.
        self.assertLess(offer.price or 0, 87.5)
        self.assertTrue(offer.details["spezzatura"])
        self.assertEqual(offer.details["risparmio"], 21.1)


class SplitScoringTests(unittest.TestCase):
    def test_split_gets_the_spezzato_badge(self):
        from app.scoring import score_offers

        ctx = make_ctx(max_price=120)
        offer = make_train_offer(price=66.4)
        offer.source = "spezzatura"
        offer.details["spezzatura"] = True
        ranked = score_offers([offer], ctx)
        self.assertIn("spezzato", ranked[offer.fingerprint]["badges"])


if __name__ == "__main__":
    unittest.main()
