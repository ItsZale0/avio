"""Smoke test for partenze alternative (no network)."""
import unittest

from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.services import nearby_origins, split_tickets

ROME = timezone(timedelta(hours=2))


def make_ctx():
    return SearchContext(
        watch_id=1,
        from_place="Thiene",
        to_place="Bressanone",
        from_lat=45.6983,
        from_lon=11.4819,
        to_lat=46.7160,
        to_lon=11.6570,
        depart_after=datetime(2026, 10, 25, 5, 0, tzinfo=ROME),
        arrive_by=datetime(2026, 10, 25, 20, 0, tzinfo=ROME),
        radius_km=30,
        passengers=1,
        max_price=None,
    )


def soluzione(prezzo, partenza, arrivo, cambi, treni="Regionale", origin="", destination=""):
    return {
        "price": prezzo,
        "depart_at": partenza,
        "arrive_at": arrivo,
        "durata_min": int((arrivo - partenza).total_seconds() // 60),
        "cambi": cambi,
        "treni": treni,
        "regionale": treni == "Regionale",
        "origin": origin,
        "destination": destination,
        "id": f"{prezzo}-{partenza:%H%M}",
    }


class NearbyOriginsSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._lookup = split_tickets._lookup_station
        self._price = split_tickets._segment_price
        self._orig_lookup = nearby_origins.split_tickets._lookup_station
        self._orig_price = nearby_origins.split_tickets._segment_price

    async def asyncTearDown(self):
        split_tickets._lookup_station = self._lookup
        split_tickets._segment_price = self._price

    async def test_trova_partenza_alternativa_più_economica(self):
        ctx = make_ctx()

        async def lookup(client, name, lat=None, lon=None):
            return {"id": name, "name": name}

        async def price(client, a, b, when):
            # accesso Thiene -> Vicenza
            if (a, b) == ("Thiene", "Vicenza"):
                return [soluzione(3.55, datetime(2026, 10, 25, 6, 0, tzinfo=ROME),
                                  datetime(2026, 10, 25, 6, 25, tzinfo=ROME), 0, origin="Thiene", destination="Vicenza")]
            # tratta principale Vicenza -> Bressanone
            if (a, b) == ("Vicenza", "Bressanone"):
                return [soluzione(12.00, datetime(2026, 10, 25, 7, 0, tzinfo=ROME),
                                  datetime(2026, 10, 25, 9, 30, tzinfo=ROME), 1, origin="Vicenza", destination="Bressanone")]
            return []

        split_tickets._lookup_station = lookup
        split_tickets._segment_price = price

        # riferimento costoso: il biglietto unico
        ref = Offer(source="trenitalia", external_id="direct", mode="train", operator="T",
                    depart_at=datetime(2026, 10, 25, 7, 21, tzinfo=ROME),
                    arrive_at=datetime(2026, 10, 25, 12, 32, tzinfo=ROME),
                    price=37.55, pickup_name="Thiene", dropoff_name="Bressanone Brixen",
                    pickup_lat=45.7, pickup_lon=11.48, dropoff_lat=46.7, dropoff_lon=11.65)

        res = await nearby_origins.analyze_nearby_origins([ref], ctx, passes=[])
        self.assertTrue(res.offers, "nessuna partenza alternativa trovata")
        self.assertAlmostEqual(res.offers[0].net_cost, 15.55, places=2)
        self.assertIn("Vicenza", res.offers[0].operator)

    async def test_salta_se_non_conviene(self):
        ctx = make_ctx()

        async def lookup(client, name, lat=None, lon=None):
            return {"id": name, "name": name}

        async def price(client, a, b, when):
            if (a, b) == ("Thiene", "Vicenza"):
                return [soluzione(3.55, datetime(2026, 10, 25, 6, 0, tzinfo=ROME),
                                  datetime(2026, 10, 25, 6, 25, tzinfo=ROME), 0)]
            if (a, b) == ("Vicenza", "Bressanone"):
                return [soluzione(40.00, datetime(2026, 10, 25, 7, 0, tzinfo=ROME),
                                  datetime(2026, 10, 25, 9, 30, tzinfo=ROME), 1)]
            return []

        split_tickets._lookup_station = lookup
        split_tickets._segment_price = price

        ref = Offer(source="trenitalia", external_id="direct", mode="train", operator="T",
                    depart_at=datetime(2026, 10, 25, 7, 21, tzinfo=ROME),
                    arrive_at=datetime(2026, 10, 25, 12, 32, tzinfo=ROME),
                    price=37.55, pickup_name="Thiene", dropoff_name="Bressanone Brixen",
                    pickup_lat=45.7, pickup_lon=11.48, dropoff_lat=46.7, dropoff_lon=11.65)

        res = await nearby_origins.analyze_nearby_origins([ref], ctx, passes=[])
        self.assertFalse(res.offers, "non dovrebbe aggiungere offerte piu' care")
        self.assertTrue(res.confronti, "il confronto va comunque registrato")
        self.assertFalse(res.confronti[0]["conveniente"])

    async def test_salta_accesso_se_non_connesso(self):
        ctx = make_ctx()

        async def lookup(client, name, lat=None, lon=None):
            return {"id": name, "name": name}

        async def price(client, a, b, when):
            return []  # nessuna corsa

        split_tickets._lookup_station = lookup
        split_tickets._segment_price = price

        res = await nearby_origins.analyze_nearby_origins([], ctx, passes=[])
        self.assertFalse(res.offers)
        self.assertFalse(res.confronti)


if __name__ == "__main__":
    unittest.main()
