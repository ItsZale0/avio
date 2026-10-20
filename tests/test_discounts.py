"""Le condizioni reali dello sconto X-GO di Trenitalia.

Non vale su tutto: solo sui treni regionali, non nelle regioni escluse,
non dopo il 30 novembre 2026, e solo sui treni selezionati dalla promozione.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer
from app.services import discounts

ROME = timezone(timedelta(hours=2))


def make_train(
    price=20.0,
    category="Regionale",
    compagnia="Trenitalia",
    external_id="t1",
    giorno=22,
    partenza=(45.4642, 9.1900),   # Milano
    arrivo=(45.5387, 10.2118),     # Bergamo
):
    return Offer(
        source="trenitalia",
        external_id=external_id,
        mode="train",
        operator=compagnia,
        depart_at=datetime(2026, 10, giorno, 6, 0, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 10, giorno, 10, 0, tzinfo=timezone.utc),
        price=price,
        pickup_name="A",
        pickup_lat=partenza[0],
        pickup_lon=partenza[1],
        dropoff_name="B",
        dropoff_lat=arrivo[0],
        dropoff_lon=arrivo[1],
        duration_min=240,
        details={"cambi": 2, "treni": category, "compagnia": compagnia, "tappe": []},
    )


class XgoConditionsTests(unittest.TestCase):
    def test_a_regional_train_gets_the_discount(self):
        offer = make_train(price=20.0)
        discounts.apply_xgo([offer], {"xgo": "giovane"})
        self.assertAlmostEqual(offer.price, 16.0)
        self.assertAlmostEqual(offer.details["prezzo_listino"], 20.0)
        self.assertTrue(offer.price_is_estimate)

    def test_high_speed_trains_do_not_get_it(self):
        for categoria in ("Frecciarossa", "Frecciargento", "Frecciabianca", "Intercity",
                          "Intercity Notte", "EuroCity", "Frecce Regionale"):
            offer = make_train(price=48.0, category=categoria)
            discounts.apply_xgo([offer], {"xgo": "giovane"})
            self.assertEqual(offer.price, 48.0, categoria)
            self.assertIn("solo sui treni regionali", offer.details.get("xgo_non_applicabile", ""))

    def test_private_operator_trains_do_not_get_it(self):
        offer = make_train(price=30.0, compagnia="Italo")
        discounts.apply_xgo([offer], {"xgo": "giovane"})
        self.assertEqual(offer.price, 30.0)

    def test_regions_without_the_promotion(self):
        # Campania, Abruzzo, Sardegna, Puglia, Trento e Bolzano sono escluse.
        casi = [
            ("Napoli", "Salerno", (40.8518, 14.2681), (40.6822, 14.7681)),
            ("L'Aquila", "Pescara", (42.3499, 13.3995), (42.1107, 14.1937)),
            ("Cagliari", "Sassari", (39.2238, 9.1217), (40.7272, 8.5602)),
            ("Bari", "Lecce", (41.1171, 16.8719), (40.3528, 18.1741)),
            ("Trento", "Bolzano", (46.0674, 11.1214), (46.4983, 11.3548)),
        ]
        for nome, arrivo, partenza, arrivo_coord in casi:
            offer = make_train(price=20.0, partenza=partenza, arrivo=arrivo_coord)
            discounts.apply_xgo([offer], {"xgo": "senior"})
            self.assertEqual(offer.price, 20.0, nome)
            self.assertIn("non vale in", offer.details.get("xgo_non_applicabile", ""), nome)

    def test_the_promotion_ends_on_30_november_2026(self):
        offer = make_train(price=20.0)
        offer.depart_at = datetime(2026, 12, 5, 6, 0, tzinfo=timezone.utc)
        discounts.apply_xgo([offer], {"xgo": "giovane"})
        self.assertEqual(offer.price, 20.0)
        self.assertIn("30 novembre 2026", offer.details.get("xgo_non_applicabile", ""))

    def test_no_discount_without_the_program(self):
        offer = make_train(price=20.0)
        discounts.apply_xgo([offer], {})
        self.assertEqual(offer.price, 20.0)
        self.assertFalse(offer.price_is_estimate)

    def test_the_note_explains_the_conditions(self):
        offer = make_train(price=20.0)
        discounts.apply_xgo([offer], {"xgo": "giovane"})
        nota = offer.details.get("nota_sconto", "")
        self.assertIn("20%", nota)
        self.assertIn("iscritti", nota)
        self.assertIn("30 novembre 2026", nota)


class RegionTests(unittest.TestCase):
    def test_the_city_recognises_its_own_region(self):
        from app.geo import CITIES
        from app.services.passes import regione_di

        casi = {
            "bari": "puglia",
            "cagliari": "sardegna",
            "bolzano": "alto_adige",
            "napoli": "campania",
            "l'aquila": "abruzzo",
            "trento": "trentino",
            "milano": "lombardia",
        }
        for citta, regione in casi.items():
            coords = CITIES.get(citta)
            self.assertIsNotNone(coords, citta)
            self.assertEqual(regione_di(*coords), regione, citta)


if __name__ == "__main__":
    unittest.main()
