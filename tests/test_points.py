"""Test dei punti di ritrovo, del tempo a piedi e di BlaBlaCar.

Senza API pubblica, BlaBlaCar non puo' dare prezzi reali: qui si verifica
che la stima sia dichiarata come tale e che il link vero ci sia.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.adapters.blablacar import BlaBlaCarAdapter
from app.ai import intelligence
from app.ai.planner import build_candidates
from app.geo import walking_minutes, walking_text

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
ARRIVE = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)


def make_ctx(**overrides) -> SearchContext:
    data = dict(
        watch_id=1,
        from_place="Thiene",
        to_place="Trento",
        from_lat=45.6877,
        from_lon=11.4861,
        to_lat=46.1029,
        to_lon=11.1297,
        depart_after=DEPART,
        arrive_by=ARRIVE,
        radius_km=5,
        passengers=1,
        max_price=30,
    )
    data.update(overrides)
    return SearchContext(**data)


class WalkingTests(unittest.TestCase):
    def test_one_kilometer_is_about_thirteen_minutes(self):
        self.assertEqual(walking_minutes(1.0), 13)

    def test_nothing_to_walk(self):
        self.assertEqual(walking_minutes(0.0), 0)
        self.assertEqual(walking_minutes(0.01), 0)

    def test_unknown_distance(self):
        self.assertIsNone(walking_minutes(None))

    def test_text(self):
        self.assertIn("min a piedi", walking_text(1.0))
        self.assertEqual(walking_text(None), "")


class BlaBlaCarTests(unittest.TestCase):
    """BlaBlaCar non stima piu': legge i dati veri dal sito."""

    def _ctx(self):
        return make_ctx()

    def test_it_uses_the_real_data_not_an_estimate(self):
        from app.adapters.blablacar import BlaBlaCarAdapter

        adapter = BlaBlaCarAdapter()
        # Senza browser l'adapter non propone nulla: mai prezzi inventati.
        offerta = None
        for o in adapter._offerte({}, self._ctx(), "https://esempio"):
            offerta = o
        self.assertIsNone(offerta)

    def test_the_price_is_never_estimated(self):
        from app.adapters.blablacar import BlaBlaCarAdapter, _prezzo

        # Il prezzo si legge, non si calcola.
        self.assertEqual(
            _prezzo({"tokenizedPrice": [{"type": "INTEGER", "value": "29"},
                                         {"type": "FRACTION", "value": ",99"}]}),
            29.99,
        )
        self.assertIsNone(_prezzo({}))
        adapter = BlaBlaCarAdapter()
        self.assertIn("blablacar.it/search", adapter.search_url(self._ctx()))


