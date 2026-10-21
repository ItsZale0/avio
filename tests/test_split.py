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


class StationChoiceTests(unittest.TestCase):
    """La stazione giusta: niente aeroporti, niente stazioni dei bus."""

    ITEMS = [
        {"name": "VERONA AEROPORTO", "locationId": "1",
         "geographicCoordinates": {"latitude": 45.4384, "longitude": 10.9075}},
        {"name": "VERONA BUS", "locationId": "2",
         "geographicCoordinates": {"latitude": 45.4305, "longitude": 10.9873}},
        {"name": "VERONA OSPEDALE BORGO TRENTO", "locationId": "3",
         "geographicCoordinates": {"latitude": 45.4460, "longitude": 10.9950}},
        {"name": "Verona Porta Nuova", "locationId": "4",
         "geographicCoordinates": {"latitude": 45.9983, "longitude": 10.9824}},
    ]

    def test_city_resolves_to_the_main_station(self):
        from app.adapters.trenitalia import _choose_station

        chosen = _choose_station(self.ITEMS, "verona", 45.4384, 10.9916)
        self.assertEqual(chosen["name"], "Verona Porta Nuova")

    def test_exact_match_wins(self):
        from app.adapters.trenitalia import _choose_station

        chosen = _choose_station(self.ITEMS, "verona bus")
        self.assertEqual(chosen["name"], "VERONA BUS")

    def test_airport_requested_explicitly(self):
        from app.adapters.trenitalia import _choose_station

        chosen = _choose_station(self.ITEMS, "verona aeroporto")
        self.assertEqual(chosen["name"], "VERONA AEROPORTO")


class ComparisonTests(unittest.TestCase):
    """Il confronto fra tratte singole e biglietto diretto."""

    def _confronto(self, **overrides):
        base = {
            "via": "Verona Porta Nuova",
            "tratte": [
                {"da": "Thiene", "a": "Verona Porta Nuova", "prezzo": 7.9,
                 "partenza": "2026-10-22T04:21:00+00:00",
                 "arrivo": "2026-10-22T05:40:00+00:00", "durata_min": 79},
                {"da": "Verona Porta Nuova", "a": "Trento", "prezzo": 7.85,
                 "partenza": "2026-10-22T06:06:00+00:00",
                 "arrivo": "2026-10-22T07:30:00+00:00", "durata_min": 84},
            ],
            "totale": 15.75,
            "diretto_prezzo": 11.90,
            "delta_prezzo": 3.85,
            "durata_min": 189,
            "diretto_durata_min": 248,
            "delta_minuti": -59,
            "partenza": "2026-10-22T04:21:00+00:00",
            "arrivo": "2026-10-22T07:30:00+00:00",
            "diretto_partenza": "2026-10-22T06:21:00+00:00",
            "diretto_arrivo": "2026-10-22T10:29:00+00:00",
            "cambi": 1,
            "conveniente": False,
        }
        base.update(overrides)
        return base

    def test_delta_price_is_positive_when_it_costs_more(self):
        confronto = self._confronto()
        self.assertGreater(confronto["delta_prezzo"], 0)
        self.assertFalse(confronto["conveniente"])

    def test_delta_minutes_is_negative_when_it_arrives_earlier(self):
        confronto = self._confronto()
        self.assertLess(confronto["delta_minuti"], 0)
        self.assertLess(confronto["durata_min"], confronto["diretto_durata_min"])

    def test_internal_data_is_not_exposed(self):
        from app.services.split_tickets import _comparison

        confronto = self._confronto(leg1={"a": 1}, offer={"b": 2})
        pubblico = _comparison(confronto)
        self.assertNotIn("leg1", pubblico)
        self.assertNotIn("offer", pubblico)
        self.assertIn("delta_prezzo", pubblico)
        self.assertIn("tratte", pubblico)

    def test_sum_of_the_two_legs(self):
        confronto = self._confronto()
        somma = sum(t["prezzo"] for t in confronto["tratte"])
        self.assertAlmostEqual(somma, confronto["totale"], places=2)


def make_brennero_direct(price=23.81):
    """Bressanone -> Rovereto: regionale che esce dall'Alto Adige."""
    return Offer(
        source="trenitalia",
        external_id="brennero",
        mode="train",
        operator="Regionale",
        depart_at=datetime(2026, 10, 22, 7, 0, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc),
        price=price,
        pickup_name="Bressanone/Brixen",
        dropoff_name="Rovereto",
        pickup_lat=46.7084,
        pickup_lon=11.6556,
        dropoff_lat=45.8909,
        dropoff_lon=11.0393,
        details={"tappe": [], "treni": "Regionale"},
    )


class PassiSullaSpezzaturaTests(unittest.TestCase):
    """Con un abbonamento ogni biglietto della spezzatura si prezza da solo."""

    FLEX = [{"tipo": "altoadigemobilita_flex"}]

    def _confronto(self):
        return {
            "via": "Bolzano/Bozen",
            "tratte": [
                {"da": "Bressanone/Brixen", "a": "Bolzano/Bozen", "prezzo": 8.00},
                {"da": "Bolzano/Bozen", "a": "Rovereto", "prezzo": 12.55},
            ],
            "totale": 20.55,
            "diretto_prezzo": 23.81,
            "delta_prezzo": -3.26,
            "durata_min": 150,
            "delta_minuti": 10,
            "conveniente": True,
            "offer": make_brennero_direct(),
        }

    def test_the_flex_is_applied_to_the_single_ticket(self):
        confronto = self._confronto()
        split_tickets.applica_abbonamenti([confronto], self.FLEX)  # type: ignore[list-item]
        prima, seconda = confronto["tratte"]
        # Bressanone-Bolzano col Flex: 38,13 km tariffari a 0,13 = 4,96.
        self.assertAlmostEqual(prima["prezzo_effettivo"], 4.96, places=2)
        self.assertIn("Flex", prima["pass"])
        # Bolzano-Rovereto esce dall'Alto Adige: prezzo intero.
        self.assertEqual(seconda["prezzo_effettivo"], 12.55)
        self.assertNotIn("pass", seconda)
        self.assertAlmostEqual(confronto["totale_effettivo"], 17.51, places=2)
        # Il diretto non e' coperto per intero: resta a listino.
        self.assertEqual(confronto["diretto_effettivo"], 23.81)
        self.assertAlmostEqual(confronto["delta_prezzo"], -6.30, places=2)
        self.assertTrue(confronto["conveniente"])

    def test_xgo_and_flex_together_take_the_cheapest(self):
        confronto = self._confronto()
        split_tickets.applica_abbonamenti(  # type: ignore[list-item]
            [confronto], self.FLEX, sconto_xgo=0.2
        )
        prima, seconda = confronto["tratte"]
        # X-GO porta 8,00 a 6,40, ma il Flex (4,96) e' meglio.
        self.assertAlmostEqual(prima["prezzo_effettivo"], 4.96, places=2)
        self.assertEqual(prima["sconto_xgo"], 1.60)
        # Seconda tratta: X-GO 20% (10,04), il Flex non la copre.
        self.assertEqual(seconda["prezzo_effettivo"], 10.04)
        self.assertAlmostEqual(confronto["totale_effettivo"], 15.00, places=2)

    def test_without_passes_the_ticket_prices_are_kept(self):
        confronto = self._confronto()
        split_tickets.applica_abbonamenti([confronto], [])  # type: ignore[list-item]
        self.assertAlmostEqual(confronto["totale_effettivo"], 20.55, places=2)
        self.assertEqual(confronto["diretto_effettivo"], 23.81)
        self.assertAlmostEqual(confronto["delta_prezzo"], -3.26, places=2)


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
