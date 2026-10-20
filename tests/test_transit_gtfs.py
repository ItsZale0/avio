"""Fermate vicine senza Overpass: il ripiego sui feed GTFS locali."""

from __future__ import annotations

import unittest

from app.adapters.transit import _gtfs_stops


class FermateGtfsTests(unittest.TestCase):
    def setUp(self):
        from app.gtfs import db as gtfs_db

        if not gtfs_db.has_data():
            self.skipTest("nessun feed GTFS importato")

    def test_trento_ha_fermate_a_pochi_passi(self):
        fermate = _gtfs_stops(46.0679, 11.1211)
        self.assertTrue(fermate, "il feed del Trentino dovrebbe avere fermate a Trento")
        self.assertLess(fermate[0]["distance_km"], 1.6)
        self.assertTrue(fermate[0]["name"])
        self.assertTrue(str(fermate[0]["id"]).startswith("gtfs:"))
        self.assertIsNotNone(fermate[0]["lat"])
        self.assertIsNotNone(fermate[0]["lon"])

    def test_sono_ordinate_per_distanza(self):
        fermate = _gtfs_stops(46.0679, 11.1211)
        distanze = [f["distance_km"] for f in fermate]
        self.assertEqual(distanze, sorted(distanze))

    def test_in_mezzo_al_mare_non_ci_sono_fermate(self):
        self.assertEqual(_gtfs_stops(35.0, 12.0), [])

    def test_il_raggio_conta(self):
        strette = _gtfs_stops(46.0679, 11.1211, radius_km=0.1)
        larghe = _gtfs_stops(46.0679, 11.1211, radius_km=2.0)
        self.assertLessEqual(len(strette), len(larghe))


if __name__ == "__main__":
    unittest.main()
