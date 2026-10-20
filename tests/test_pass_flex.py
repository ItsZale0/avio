"""Test del pass altoadigemobilita Flex (tariffa chilometrica)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from app.adapters.base import Offer
from app.services import passes as passes_service

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)

# Merano -> Bolzano: circa 25 km, tutta in Alto Adige.
MERANO = (46.6727, 11.1590)
BOLZANO = (46.4983, 11.3548)
# Bolzano -> Trento: la meta' e' in Trentino, quindi fuori Alto Adige.
TRENTO = (46.0748, 11.1217)


def make_train(price=5.9, da=MERANO, a=BOLZANO, treni="Regionale"):
    return Offer(
        source="trenitalia",
        external_id="t1",
        mode="train",
        operator="Regionale",
        depart_at=DEPART,
        arrive_at=DEPART + timedelta(hours=1),
        price=price,
        pickup_name="Merano",
        dropoff_name="Bolzano",
        pickup_lat=da[0],
        pickup_lon=da[1],
        dropoff_lat=a[0],
        dropoff_lon=a[1],
        duration_min=60,
        details={"cambi": 0, "treni": treni, "tappe": []},
    )


class FlexCatalogueTests(unittest.TestCase):
    def test_the_flex_pass_is_in_the_catalogue(self):
        for chiave in ("altoadigemobilita_flex", "altoadigemobilita_flexfamily"):
            self.assertIn(chiave, passes_service.PASS_TYPES, chiave)

    def test_the_km_tariff_is_the_official_one(self):
        tariffa = passes_service.PASS_TYPES["altoadigemobilita_flex"]["tariffa_km"]
        fasce = tariffa["fasce"]
        self.assertEqual(fasce[0], (1000, 0.13))
        self.assertEqual(fasce[1], (2000, 0.08))
        self.assertEqual(fasce[2], (10000, 0.03))
        self.assertEqual(fasce[3], (20000, 0.02))
        self.assertEqual(tariffa["min_km"], 10)
        self.assertEqual(tariffa["tetto_giornaliero"], 22.0)

    def test_the_family_pass_is_cheaper_per_km(self):
        flex = passes_service.PASS_TYPES["altoadigemobilita_flex"]["tariffa_km"]
        family = passes_service.PASS_TYPES["altoadigemobilita_flexfamily"]["tariffa_km"]
        self.assertLess(family["fasce"][0][1], flex["fasce"][0][1])
        self.assertEqual(family["fasce"][0], (1000, 0.11))


class FlexRecognitionTests(unittest.TestCase):
    def test_the_flex_is_recognised_by_name(self):
        self.assertEqual(
            passes_service.trova_per_testo("ho il pass flex"), "altoadigemobilita_flex"
        )

    def test_the_flex_wins_over_the_plain_pass(self):
        self.assertEqual(
            passes_service.trova_per_testo("ho l'abbonamento alto adige mobilita flex"),
            "altoadigemobilita_flex",
        )

    def test_the_family_pass_is_recognised(self):
        self.assertEqual(
            passes_service.trova_per_testo("il mio flexfamily pass"),
            "altoadigemobilita_flexfamily",
        )

    def test_the_plain_pass_is_still_recognised(self):
        self.assertEqual(
            passes_service.trova_per_testo("ho l'abbonamento alto adige mobilita"),
            "altoadigemobilita",
        )


class FlexCostTests(unittest.TestCase):
    def test_the_flex_covers_the_train_but_does_not_make_it_free(self):
        offer = make_train(price=5.9)
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        self.assertIsNotNone(offer.net_cost)
        # Circa 24 km tariffati a 0,13 EUR/km: si paga il chilometro.
        self.assertGreater(offer.net_cost, 2.5)
        self.assertLess(offer.net_cost, offer.price)
        self.assertIn("pass_tariffa", offer.details)
        self.assertAlmostEqual(offer.details["pass_costo_km"], 3.18, places=2)

    def test_the_minimum_10_km_is_applied(self):
        # Un viaggio di pochi metri: il minimo e' comunque 10 km tariffati.
        offer = make_train(price=2.5, da=BOLZANO, a=(46.51, 11.36))
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        self.assertEqual(offer.details["pass_km"], 10)
        self.assertEqual(offer.details["pass_costo_km"], 1.3)

    def test_a_trip_outside_alto_adige_is_not_covered(self):
        # Trento -> Verona: fuori Alto Adige, il Flex non copre niente.
        offer = make_train(price=12.0, da=TRENTO, a=(45.4384, 10.9916))
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        self.assertIsNone(offer.net_cost)

    def test_the_brennero_line_is_only_partly_covered(self):
        # Bolzano -> Trento: la parte in Trentino non e' coperta, ma la
        # stima e' geografica (la linea ferroviaria segue la valle, non la
        # retta fra le due citta').
        offer = make_train(price=12.0, da=BOLZANO, a=TRENTO)
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        quota = offer.details.get("pass_quota", 0)
        self.assertGreater(quota, 30)
        self.assertLess(quota, 90)

    def test_the_expired_flex_covers_nothing(self):
        offer = make_train()
        passes_service.apply_passes(
            [{"tipo": "altoadigemobilita_flex", "valido_alla": "2026-10-01"}], offer
        )
        self.assertIsNone(offer.net_cost)


if __name__ == "__main__":
    unittest.main()
