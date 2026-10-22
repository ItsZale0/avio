"""Prezzi dei voli via SerpApi: lettura, cache e tetto mensile.

La risposta usata nei test e' una risposta vera di SerpApi (Milano -> Roma,
25 ottobre 2026), ripulita dai campi che contengono la chiave.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db
from app.adapters.base import SearchContext
from app.adapters.serpapi import (
    SerpApiFlightsAdapter,
    _pulita,
    chiave_cache,
    offerte_da_risposta,
    quota_usata,
)
from app.timeutil import now_utc
from tests.base_db import BaseDbTest

FIXTURE = Path(__file__).parent / "fixtures" / "serpapi_voli.json"
DATI = json.loads(FIXTURE.read_text(encoding="utf-8"))


def contesto(partenza="Milano", arrivo="Roma", passeggeri=1,
             giorni=30, da_coord=(45.4642, 9.1900), a_coord=(41.9028, 12.4964)):
    """Una ricerca con una data futura, cosi' i test non scadono."""
    giorno = (now_utc() + timedelta(days=giorni)).replace(microsecond=0)
    return SearchContext(
        watch_id=1,
        from_place=partenza,
        to_place=arrivo,
        from_lat=da_coord[0],
        from_lon=da_coord[1],
        to_lat=a_coord[0],
        to_lon=a_coord[1],
        depart_after=giorno,
        arrive_by=giorno + timedelta(hours=12),
        radius_km=5,
        passengers=passeggeri,
    )


class LetturaTests(unittest.TestCase):
    def test_la_risposta_diventa_offerte(self):
        offerte = offerte_da_risposta(DATI, now_utc())
        self.assertEqual(len(offerte), 4)
        self.assertEqual(min(o.price for o in offerte), 61)
        prima = offerte[0]
        self.assertEqual(prima.source, "serpapi")
        self.assertEqual(prima.mode, "flight")
        self.assertEqual(prima.operator, "ITA")
        self.assertEqual(prima.details["scali"], 0)
        self.assertEqual(prima.pickup_name, "Aeroporto di Milano Linate")
        self.assertIn("google.com/travel/flights", prima.deeplink or "")
        self.assertFalse(prima.price_is_estimate)
        self.assertIn("letto_il", prima.details)

    def test_il_volo_con_scalo_ha_due_tratte(self):
        offerte = offerte_da_risposta(DATI, now_utc())
        con_scalo = next(o for o in offerte if o.operator == "Iberia")
        self.assertEqual(con_scalo.details["scali"], 1)
        self.assertEqual(len(con_scalo.details["tappe"]), 2)
        self.assertEqual(con_scalo.price, 111)
        self.assertEqual(con_scalo.duration_min, 840)

    def test_la_chiave_non_finisce_nel_database(self):
        sporca = {
            "search_metadata": {
                "status": "Success",
                "json_endpoint": "https://serpapi.com/searches/x.json?api_key=SEGRETO",
                "raw_html_file": "https://serpapi.com/searches/x.html?api_key=SEGRETO",
            },
            "search_parameters": {"api_key": "SEGRETO", "currency": "EUR"},
        }
        pulita = _pulita(sporca)
        testo = json.dumps(pulita)
        self.assertNotIn("SEGRETO", testo)
        self.assertEqual(pulita["search_metadata"]["status"], "Success")


class RicercaTests(BaseDbTest):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {"SERPAPI_KEY": "chiave-di-prova"})
        self.env.start()
        self.addCleanup(self.env.stop)
        with db.db() as conn:
            conn.execute("DELETE FROM voli_cache")
            conn.execute("DELETE FROM voli_quota")

    def test_senza_chiave_non_chiama(self):
        with patch.dict(os.environ, {"SERPAPI_KEY": ""}):
            with patch("app.adapters.serpapi._chiama", new_callable=AsyncMock) as chiama:
                risultato = asyncio.run(SerpApiFlightsAdapter().search(contesto()))
        self.assertEqual(risultato.offers, [])
        self.assertIn("chiave", risultato.hints[0])
        chiama.assert_not_called()

    def test_la_stessa_rotta_non_si_paga_due_volte(self):
        with patch("app.adapters.serpapi._chiama", new_callable=AsyncMock) as chiama:
            chiama.return_value = DATI
            primo = asyncio.run(SerpApiFlightsAdapter().search(contesto()))
            secondo = asyncio.run(SerpApiFlightsAdapter().search(contesto()))
        self.assertEqual(len(primo.offers), 4)
        self.assertEqual(len(secondo.offers), 4)
        self.assertEqual(chiama.await_count, 1)
        self.assertEqual(quota_usata(), 1)

    def test_gli_aeroporti_della_citta_viaggiano_insieme(self):
        with patch("app.adapters.serpapi._chiama", new_callable=AsyncMock) as chiama:
            chiama.return_value = DATI
            asyncio.run(SerpApiFlightsAdapter().search(contesto()))
        parametri = chiama.await_args.args[0]
        self.assertEqual(parametri["departure_id"], "MXP,LIN,BGY")
        self.assertEqual(parametri["arrival_id"], "FCO,CIA")
        self.assertEqual(parametri["type"], "2")
        self.assertEqual(parametri["currency"], "EUR")

    def test_a_tetto_raggiunto_resta_il_link(self):
        from app.adapters.serpapi import _mese, _tetto

        with db.db() as conn:
            conn.execute(
                "INSERT INTO voli_quota(mese, usate) VALUES (?, ?)",
                (_mese(), _tetto()),
            )
        with patch("app.adapters.serpapi._chiama", new_callable=AsyncMock) as chiama:
            risultato = asyncio.run(SerpApiFlightsAdapter().search(contesto()))
        self.assertEqual(risultato.offers, [])
        self.assertIn("tetto", risultato.hints[0])
        chiama.assert_not_called()

    def test_tratta_corta_resta_zitta(self):
        ctx = contesto("Milano", "Bergamo", da_coord=(45.4642, 9.1900), a_coord=(45.6983, 9.6773))
        with patch("app.adapters.serpapi._chiama", new_callable=AsyncMock) as chiama:
            risultato = asyncio.run(SerpApiFlightsAdapter().search(ctx))
        self.assertEqual(risultato.offers, [])
        self.assertEqual(risultato.hints, [])
        chiama.assert_not_called()

    def test_senza_aeroporti_resta_zitta(self):
        ctx = contesto("Bressanone", "Rovereto", da_coord=(46.716, 11.657), a_coord=(45.889, 11.039))
        with patch("app.adapters.serpapi._chiama", new_callable=AsyncMock) as chiama:
            risultato = asyncio.run(SerpApiFlightsAdapter().search(ctx))
        self.assertEqual(risultato.hints, [])
        chiama.assert_not_called()

    def test_la_chiave_della_cache_e_una_sola(self):
        self.assertEqual(
            chiave_cache("MXP,LIN", "FCO", datetime(2026, 10, 25).date(), 2),
            "MXP,LIN>FCO>2026-10-25>a2",
        )


class RegistroTests(unittest.TestCase):
    def test_la_fonte_e_disponibile(self):
        from app.adapters import ADAPTERS

        self.assertIn("serpapi", ADAPTERS)
        self.assertEqual(ADAPTERS["serpapi"].modes, ("flight",))


if __name__ == "__main__":
    unittest.main()
