"""Voli: Google Voli e Skyscanner restano link, mai prezzi inventati.

Le due fonti non possono leggere i risultati da un server (verificato sulla
VM il 22/10/2026: Google resta sul caricamento, Skyscanner risponde con la
pagina "sei una persona o un robot"). Qui si controlla che costruiscano il
link giusto, che non propongano offerte, e che stiano zitte quando la tratta
non ha aeroporti.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import SearchContext
from app.adapters.googleflights import GoogleFlightsAdapter
from app.adapters.googleflights import link_ricerca as link_google
from app.adapters.skyscanner import SkyscannerAdapter
from app.adapters.skyscanner import link_ricerca as link_skyscanner
from app.services import airports

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 25, 6, 0, tzinfo=ROME)
ARRIVE = datetime(2026, 10, 25, 12, 0, tzinfo=ROME)


def make_ctx(partenza="Milano", arrivo="Roma", from_coords=(45.4642, 9.1900),
             to_coords=(41.9028, 12.4964)):
    return SearchContext(
        watch_id=1,
        from_place=partenza,
        to_place=arrivo,
        from_lat=from_coords[0],
        from_lon=from_coords[1],
        to_lat=to_coords[0],
        to_lon=to_coords[1],
        depart_after=DEPART,
        arrive_by=ARRIVE,
        radius_km=5,
        passengers=2,
    )


class AeroportiTests(unittest.TestCase):
    def test_the_station_name_is_still_the_city(self):
        milano = airports.aeroporti_di("Milano Centrale")
        self.assertIsNotNone(milano)
        self.assertEqual(milano.principale, "MXP")
        self.assertEqual(milano.nome, "Milano")

    def test_the_airport_name_works_too(self):
        self.assertEqual(airports.aeroporti_di("Malpensa").principale, "MXP")  # type: ignore[union-attr]
        self.assertEqual(airports.aeroporti_di("Fiumicino").citta, "roma")  # type: ignore[union-attr]

    def test_the_code_works_too(self):
        self.assertEqual(airports.aeroporti_di("FCO").citta, "roma")  # type: ignore[union-attr]

    def test_a_town_without_airport_has_none(self):
        self.assertIsNone(airports.aeroporti_di("Bressanone"))
        self.assertIsNone(airports.aeroporti_di("Rovereto"))
        self.assertIsNone(airports.aeroporti_di(""))

    def test_two_words_city(self):
        self.assertEqual(airports.aeroporti_di("Monaco di Baviera").principale, "MUC")  # type: ignore[union-attr]
        self.assertEqual(airports.aeroporti_di("New York").principale, "JFK")  # type: ignore[union-attr]


class LinkTests(unittest.TestCase):
    def test_google_link_is_prefilled(self):
        link = link_google("Milano", "Roma", DEPART.date(), 2)
        self.assertIsNotNone(link)
        self.assertIn("google.com/travel/flights", link)
        self.assertIn("Milano", link)
        self.assertIn("Roma", link)
        self.assertIn("2026-10-25", link)
        self.assertIn("curr=EUR", link)

    def test_skyscanner_link_uses_airport_codes(self):
        link = link_skyscanner("Milano", "Roma", DEPART.date(), 2)
        self.assertIsNotNone(link)
        self.assertIn("/transport/flights/mxp/fco/261025/", link)
        self.assertIn("adultsv2=2", link)

    def test_no_airport_no_link(self):
        self.assertIsNone(link_google("Bressanone", "Rovereto", DEPART.date()))
        self.assertIsNone(link_skyscanner("Bressanone", "Rovereto", DEPART.date()))


class AdapterTests(unittest.TestCase):
    def test_the_link_is_built_from_the_watch(self):
        url = GoogleFlightsAdapter().search_url(make_ctx())
        self.assertIn("travel/flights", url or "")
        self.assertIn("mxp/fco", SkyscannerAdapter().search_url(make_ctx()) or "")

    def test_no_offers_only_a_hint(self):
        for adattatore in (GoogleFlightsAdapter(), SkyscannerAdapter()):
            risultato = asyncio.run(adattatore.search(make_ctx()))
            self.assertEqual(risultato.offers, [])
            self.assertEqual(len(risultato.hints), 1)
            self.assertIn("Link utili", risultato.hints[0])

    def test_silent_without_airports(self):
        ctx = make_ctx("Bressanone", "Rovereto", (46.716, 11.657), (45.889, 11.039))
        for adattatore in (GoogleFlightsAdapter(), SkyscannerAdapter()):
            risultato = asyncio.run(adattatore.search(ctx))
            self.assertEqual(risultato.offers, [])
            self.assertEqual(risultato.hints, [])

    def test_silent_for_short_trips(self):
        # Milano -> Bergamo: 45 km, in aereo non ha senso.
        ctx = make_ctx("Milano", "Bergamo", (45.4642, 9.1900), (45.6983, 9.6773))
        for adattatore in (GoogleFlightsAdapter(), SkyscannerAdapter()):
            risultato = asyncio.run(adattatore.search(ctx))
            self.assertEqual(risultato.hints, [])


class LinksTests(unittest.TestCase):
    def _watch(self, partenza="Milano", arrivo="Roma"):
        return {
            "from_place": partenza,
            "to_place": arrivo,
            "from_lat": 45.4642,
            "from_lon": 9.19,
            "to_lat": 41.9028,
            "to_lon": 12.4964,
            "depart_after": DEPART.isoformat(),
            "arrive_by": ARRIVE.isoformat(),
            "passengers": 1,
        }

    def test_the_links_appear_in_link_utili(self):
        from app.store import links_for_watch

        link = {voce["id"]: voce["url"] for voce in links_for_watch(self._watch())}
        self.assertIn("mxp/fco", link["skyscanner"])
        self.assertIn("travel/flights", link["googleflights"])

    def test_no_flight_links_without_airports(self):
        from app.store import links_for_watch

        ids = {voce["id"] for voce in links_for_watch(self._watch("Bressanone", "Rovereto"))}
        self.assertNotIn("skyscanner", ids)
        self.assertNotIn("googleflights", ids)


class RegistryTests(unittest.TestCase):
    def test_both_sources_are_available(self):
        from app.adapters import ADAPTERS

        self.assertIn("googleflights", ADAPTERS)
        self.assertIn("skyscanner", ADAPTERS)
        self.assertEqual(ADAPTERS["googleflights"].modes, ("flight",))
        self.assertEqual(ADAPTERS["skyscanner"].modes, ("flight",))


if __name__ == "__main__":
    unittest.main()
