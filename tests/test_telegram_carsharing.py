"""Test minimi per l'adattatore Telegram carsharing (senza rete)."""
import unittest

from app.adapters import telegram_carsharing as tc


class TelegramCarsharingAdapterTests(unittest.TestCase):
    def test_senza_sessione_da_un_hint(self):
        import asyncio

        adapter = tc.TelegramCarsharingAdapter()
        from app.adapters.base import SearchContext
        from datetime import datetime, timedelta, timezone

        ctx = SearchContext(
            watch_id=1,
            from_place="Milano",
            to_place="Roma",
            from_lat=45.4642,
            from_lon=9.19,
            to_lat=41.9028,
            to_lon=12.4964,
            depart_after=datetime.now(timezone.utc),
            arrive_by=datetime.now(timezone.utc) + timedelta(hours=8),
            radius_km=5,
        )
        result = asyncio.run(adapter.search(ctx))
        self.assertTrue(result.hints)
        self.assertIn("login", " ".join(result.hints).lower())
