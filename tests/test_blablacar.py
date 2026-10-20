"""Test dell'adapter BlaBlaCar con i dati reali (struttura, non rete)."""
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, r"C:\Users\zampe\Documents\Progetto predefinito")

from app.adapters.base import SearchContext
from app.adapters.blablacar import BlaBlaCarAdapter, _prezzo
from app.services import blablacar_browser

ROME = timezone(timedelta(hours=2))


class PrezzoTests(unittest.TestCase):
    def test_tokenized_price(self):
        grezzo = {
            "prefix": None,
            "tokenizedPrice": [
                {"type": "INTEGER", "value": "29"},
                {"type": "FRACTION", "value": ",99"},
                {"type": "CURRENCY", "value": " €"},
            ],
        }
        self.assertEqual(_prezzo(grezzo), 29.99)

    def test_prezzo_intero(self):
        grezzo = {"tokenizedPrice": [{"type": "INTEGER", "value": "12"}]}
        self.assertEqual(_prezzo(grezzo), 12.0)

    def test_prezzo_manca(self):
        self.assertIsNone(_prezzo(None))
        self.assertIsNone(_prezzo({}))


class CacheExtractionTests(unittest.TestCase):
    HTML = (
        '<script>window["__INFRASTRUCTURE__cache"]='
        '{"/trip/search/v9?%7B%22a%22%3A1%7D":"{\\"searchUUID\\":\\"x\\",\\"resultsContent\\":'
        '{\\"searchItems\\":[{\\"type\\":\\"TRIP\\",\\"price\\":{\\"tokenizedPrice\\":'
        '[{\\"type\\":\\"INTEGER\\",\\"value\\":\\"10\\"}]},'
        '\\"travelDetails\\":{\\"itinerary\\":{\\"departure\\":{\\"time\\":\\"07:15\\"},'
        '\\"arrival\\":{\\"time\\":\\"09:00\\"}}}}]},\\"showMore\\":undefined}"}'
        ';</script>'
    )

    def test_estrae_la_ricerca(self):
        dati = blablacar_browser._estrai_cache(self.HTML)
        self.assertIsNotNone(dati)
        self.assertEqual(dati["searchUUID"], "x")
        voci = dati["resultsContent"]["searchItems"]
        self.assertEqual(len(voci), 1)
        self.assertEqual(voci[0]["price"]["tokenizedPrice"][0]["value"], "10")

    def test_html_senza_cache(self):
        self.assertIsNone(blablacar_browser._estrai_cache("<html>niente</html>"))


class OfferBuildingTests(unittest.TestCase):
    def _dati(self):
        return {
            "searchUUID": "abc",
            "resultsContent": {
                "searchItems": [
                    {"type": "SUMMARY_HEADER", "numberOfResults": "77 viaggi disponibili"},
                    {
                        "type": "TRIP",
                        "id": "TRIP_0",
                        "multimodalId": {"source": "CARPOOLING", "id": "xyz"},
                        "price": {
                            "tokenizedPrice": [
                                {"type": "INTEGER", "value": "12"},
                                {"type": "FRACTION", "value": ",50"},
                            ]
                        },
                        "scarcity": "Ultimi posti disponibili",
                        "travelDetails": {
                            "itinerary": {
                                "departure": {
                                    "primaryLocation": "Milano",
                                    "secondaryLocation": "Autostazione San Donato",
                                    "time": "07:15",
                                    "coordinates": {"latitude": 45.43, "longitude": 9.25},
                                },
                                "arrival": {
                                    "primaryLocation": "Roma",
                                    "secondaryLocation": "Autostazione Tiburtina",
                                    "time": "13:40",
                                    "coordinates": {"latitude": 41.90, "longitude": 12.52},
                                },
                            }
                        },
                    },
                ]
            },
        }

    def _ctx(self):
        return SearchContext(
            watch_id=1,
            from_place="Milano",
            to_place="Roma",
            from_lat=45.4642,
            from_lon=9.1900,
            to_lat=41.9028,
            to_lon=12.4964,
            depart_after=datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc),
            arrive_by=datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc),
            radius_km=10,
            passengers=1,
        )

    def test_costruisce_l_offerta(self):
        adapter = BlaBlaCarAdapter()
        offerte = adapter._offerte(self._dati(), self._ctx(), "https://esempio")
        self.assertEqual(len(offerte), 1)
        offerta = offerte[0]
        self.assertEqual(offerta.price, 12.50)
        self.assertEqual(offerta.mode, "rideshare")
        self.assertEqual(offerta.pickup_name, "Milano")
        self.assertEqual(offerta.details["punto_partenza"], "Autostazione San Donato")
        self.assertEqual(offerta.details["scarcita"], "Ultimi posti disponibili")
        # Orario e durata
        self.assertEqual(offerta.depart_at.hour, 7)
        self.assertEqual(offerta.depart_at.minute, 15)
        self.assertEqual(offerta.arrive_at.hour, 13)
        self.assertEqual(offerta.duration_min, 385)
        # Il punto di ritrovo con le coordinate
        self.assertAlmostEqual(offerta.pickup_lat, 45.43, places=3)

    def test_il_prezzo_e_un_dato_reale(self):
        # Non deve esserci nessuna stima: il prezzo viene letto, non calcolato.
        adapter = BlaBlaCarAdapter()
        offerte = adapter._offerte(self._dati(), self._ctx(), "https://esempio")
        self.assertFalse(offerte[0].price_is_estimate)
        self.assertNotIn("stima", offerte[0].details)

    def test_search_url(self):
        adapter = BlaBlaCarAdapter()
        url = adapter.search_url(self._ctx())
        self.assertIn("fn=Milano", url)
        self.assertIn("tn=Roma", url)
        self.assertIn("db=2026-10-24", url)


if __name__ == "__main__":
    unittest.main()
