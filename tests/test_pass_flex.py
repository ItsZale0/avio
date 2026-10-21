"""Test del pass altoadigemobilita Flex (tariffa chilometrica)."""

from __future__ import annotations

import math
import unittest
from datetime import date, datetime, timedelta, timezone

from app.adapters.base import Offer
from app.services import passes as passes_service

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)

# Merano -> Bolzano: 31,5 km di ferrovia (tabella RFI), tutta in Alto Adige.
MERANO = (46.6727, 11.1590)
BOLZANO = (46.4983, 11.3548)
BRESSANONE = (46.7084, 11.6556)
ROVERETO = (45.8909, 11.0393)
TRENTO = (46.0748, 11.1217)
BRUNICO = (46.7975, 12.0839)


def make_train(
    price=5.9,
    da=MERANO,
    a=BOLZANO,
    treni="Regionale",
    *,
    da_nome="Merano",
    a_nome="Bolzano",
):
    return Offer(
        source="trenitalia",
        external_id="t1",
        mode="train",
        operator="Regionale",
        depart_at=DEPART,
        arrive_at=DEPART + timedelta(hours=1),
        price=price,
        pickup_name=da_nome,
        dropoff_name=a_nome,
        pickup_lat=da[0],
        pickup_lon=da[1],
        dropoff_lat=a[0],
        dropoff_lon=a[1],
        duration_min=60,
        details={"cambi": 0, "treni": treni, "tappe": []},
    )


def make_bus(
    price=1.5,
    da=BOLZANO,
    a=(46.51, 11.36),
    *,
    da_nome="Bolzano",
    a_nome="Bolzano",
):
    return Offer(
        source="suedtirolmobil",
        external_id="b1",
        mode="bus",
        operator="SAD",
        depart_at=DEPART,
        arrive_at=DEPART + timedelta(minutes=15),
        price=price,
        pickup_name=da_nome,
        dropoff_name=a_nome,
        pickup_lat=da[0],
        pickup_lon=da[1],
        dropoff_lat=a[0],
        dropoff_lon=a[1],
        duration_min=15,
        details={"linea": "10A", "tappe": []},
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
        # Tariffa 41/3: minimo solo per le connessioni urbane (10 km a
        # Bolzano e Merano, 5 km nelle altre zone), non per ogni viaggio.
        self.assertEqual(tariffa["min_km"], 0)
        self.assertEqual(tariffa["urbano_km"], 10.0)
        self.assertEqual(tariffa["urbano_km_altre"], 5.0)
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
        # Merano-Bolzano: 31,48 km tariffari (tabella RFI), non 24,5 di
        # linea d'aria, a 0,13 EUR/km = 4,09.
        self.assertGreater(offer.net_cost, 2.5)
        self.assertLess(offer.net_cost, offer.price)
        self.assertIn("pass_tariffa", offer.details)
        self.assertAlmostEqual(offer.details["pass_costo_km"], 4.09, places=2)
        self.assertAlmostEqual(offer.details["pass_km"], 31.5, places=1)

    def test_the_urban_connection_counts_10_km_in_bolzano(self):
        # Connessione urbana a Bolzano: si contano 10 km tariffari.
        offer = make_bus(price=1.5)
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        self.assertEqual(offer.details["pass_km"], 10.0)
        self.assertEqual(offer.details["pass_costo_km"], 1.3)

    def test_the_urban_connection_counts_5_km_elsewhere(self):
        # Connessione urbana fuori da Bolzano/Merano: 5 km tariffari.
        offer = make_bus(
            price=1.5,
            da=BRUNICO,
            a=(46.80, 12.09),
            da_nome="Brunico",
            a_nome="Brunico",
        )
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        self.assertEqual(offer.details["pass_km"], 5.0)
        self.assertEqual(offer.details["pass_costo_km"], 0.65)

    def test_a_trip_outside_alto_adige_is_not_covered(self):
        # Trento -> Verona: fuori Alto Adige, il Flex non copre niente.
        offer = make_train(
            price=12.0,
            da=TRENTO,
            a=(45.4384, 10.9916),
            da_nome="Trento",
            a_nome="Verona Porta Nuova",
        )
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        self.assertIsNone(offer.net_cost)

    def test_bolzano_trento_is_fully_covered(self):
        # Il Flex vale sui treni regionali fino a Trento: 55,44 km tutti
        # coperti, 7,21 EUR. Prima l'app si fermava a Salorno e scontava
        # solo il 69% del biglietto.
        offer = make_train(
            price=12.0, da=BOLZANO, a=TRENTO, da_nome="Bolzano", a_nome="Trento"
        )
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        self.assertEqual(offer.details.get("pass_quota"), 100)
        self.assertAlmostEqual(offer.details["pass_km"], 55.4, places=1)
        self.assertAlmostEqual(offer.net_cost, 7.21, places=2)

    def test_beyond_trento_is_only_partly_covered(self):
        # Bolzano -> Rovereto: coperto fino a Trento (55,44 su 79,01 km),
        # il resto e' Trentino.
        offer = make_train(
            price=12.55,
            da=BOLZANO,
            a=ROVERETO,
            da_nome="Bolzano/Bozen",
            a_nome="Rovereto",
        )
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        quota = offer.details.get("pass_quota", 0)
        self.assertGreater(quota, 60)
        self.assertLess(quota, 80)

    def test_a_trip_inside_trentino_is_not_covered(self):
        # Rovereto -> Trento: nessuna parte in Alto Adige.
        offer = make_train(
            price=4.7,
            da=ROVERETO,
            a=TRENTO,
            da_nome="Rovereto",
            a_nome="Trento",
        )
        passes_service.apply_passes([{"tipo": "altoadigemobilita_flex"}], offer)
        self.assertIsNone(offer.net_cost)

    def test_the_expired_flex_covers_nothing(self):
        offer = make_train()
        passes_service.apply_passes(
            [{"tipo": "altoadigemobilita_flex", "valido_alla": "2026-10-01"}], offer
        )
        self.assertIsNone(offer.net_cost)


class StationNameTests(unittest.TestCase):
    """I nomi delle stazioni arrivano bilingui: vanno riconosciuti."""

    def test_bilingual_names_are_understood(self):
        self.assertEqual(
            passes_service._station_coords("Bressanone/Brixen"), BRESSANONE
        )
        self.assertEqual(passes_service._station_coords("Bolzano/Bozen"), BOLZANO)

    def test_the_two_names_glued_together_are_understood(self):
        self.assertEqual(
            passes_service._station_coords("Bressanone Brixen"), BRESSANONE
        )
        self.assertEqual(passes_service._station_coords("Bolzano Bozen"), BOLZANO)

    def test_trentino_stations_are_known(self):
        self.assertEqual(passes_service._station_coords("Rovereto"), ROVERETO)


class RailKmTests(unittest.TestCase):
    """I km tariffari vengono dalla tabella RFI, non dalla linea d'aria."""

    def test_the_brenner_line_distances_are_the_official_ones(self):
        from app.services import rail_km

        self.assertAlmostEqual(rail_km.km("Bressanone/Brixen", "Bolzano/Bozen"), 38.13, places=2)
        self.assertAlmostEqual(rail_km.km("Bolzano/Bozen", "Trento"), 55.44, places=2)
        self.assertAlmostEqual(rail_km.km("Bressanone", "Rovereto"), 117.14, places=2)

    def test_the_other_valley_lines_are_in_the_table(self):
        from app.services import rail_km

        # Merano-Bolzano 31,48 km; Silandro-Merano 37,77; Bressanone-Brunico
        # via Fortezza 10,18 + 32,44.
        self.assertAlmostEqual(rail_km.km("Merano", "Bolzano"), 31.48, places=2)
        self.assertAlmostEqual(rail_km.km("Silandro", "Merano"), 37.77, places=2)
        self.assertAlmostEqual(rail_km.km("Bressanone", "Brunico"), 42.62, places=2)
        self.assertAlmostEqual(rail_km.km("Bolzano", "Vipiteno"), 67.13, places=2)

    def test_the_single_ticket_price_confirms_the_km(self):
        from app.services import rail_km

        # Il biglietto ordinario costa 0,20 EUR/km arrotondati a 0,50:
        # 38,13 km -> 7,63 -> 8,00, esattamente il prezzo Trenitalia.
        km = rail_km.km("Bressanone/Brixen", "Bolzano/Bozen")
        prezzo = math.ceil(km * 0.20 * 2) / 2
        self.assertEqual(prezzo, 8.0)

    def test_coverage_stops_at_trento_but_not_before(self):
        from app.services import rail_km

        coperti, totali, completo = rail_km.copertura("Bolzano", "Rovereto")
        self.assertAlmostEqual(coperti, 55.44, places=2)
        self.assertAlmostEqual(totali, 79.01, places=2)
        self.assertFalse(completo)
        # Trento->Rovereto e' tutto in Trentino: niente copertura.
        self.assertEqual(rail_km.copertura("Rovereto", "Trento")[0], 0.0)


class SingleTicketTests(unittest.TestCase):
    """Ogni biglietto della spezzatura si prezza da solo."""

    FLEX = [{"tipo": "altoadigemobilita_flex"}]

    def test_bressanone_bolzano_costs_the_flex_km_not_the_ticket(self):
        # Il biglietto Trenitalia costa 8,00 (= 38,13 km tariffari a
        # 0,20 EUR/km arrotondati); col Flex sono 38,13 km a 0,13 = 4,96.
        costo, nome = passes_service.netto_tratta(
            self.FLEX, "Bressanone/Brixen", "Bolzano/Bozen", 8.0
        )
        self.assertAlmostEqual(costo, 4.96, places=2)
        self.assertIn("Flex", nome or "")
        self.assertGreaterEqual(costo, 4.90)
        self.assertLessEqual(costo, 5.00)

    def test_a_ticket_leaving_alto_adige_stays_full_price(self):
        # Bolzano->Rovereto esce dalla provincia: sul biglietto separato
        # il Flex non vale e non si sconta niente.
        costo, nome = passes_service.netto_tratta(
            self.FLEX, "Bolzano/Bozen", "Rovereto", 12.55
        )
        self.assertEqual(costo, 12.55)
        self.assertIsNone(nome)

    def test_without_passes_the_ticket_price_is_kept(self):
        costo, nome = passes_service.netto_tratta(
            [], "Bressanone/Brixen", "Bolzano/Bozen", 8.0
        )
        self.assertEqual(costo, 8.0)
        self.assertIsNone(nome)

    def test_the_flex_can_beat_the_xgo_discount(self):
        # Con X-GO il biglietto scende a 6,40; il Flex (4,96) resta meglio.
        costo, nome = passes_service.netto_tratta(
            self.FLEX, "Bressanone/Brixen", "Bolzano/Bozen", 6.40
        )
        self.assertAlmostEqual(costo, 4.96, places=2)
        self.assertIn("Flex", nome or "")


if __name__ == "__main__":
    unittest.main()
