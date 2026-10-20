"""Test della finestra temporale e delle chat Telegram (gruppi).

I test girano su un database temporaneo: il `data/carpool.db` reale non
viene toccato.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import db
from app.adapters.base import Offer, SearchContext
from app.ai import intelligence
from app.ai.planner import build_candidates

ROME = timezone(timedelta(hours=2))


def make_ctx(
    arrive_by: datetime,
    *,
    depart_after=None,
    max_price=None,
    radius=5.0,
    allow_day_before=False,
) -> SearchContext:
    return SearchContext(
        watch_id=1,
        from_place="Thiene",
        to_place="Trento",
        from_lat=45.6877202,
        from_lon=11.4861438,
        to_lat=46.1029536,
        to_lon=11.1297425,
        depart_after=depart_after,
        arrive_by=arrive_by,
        radius_km=radius,
        passengers=1,
        max_price=max_price,
        allow_day_before=allow_day_before,
    )


def make_offer(
    external_id: str,
    depart_at: datetime,
    arrive_at: datetime,
    price: float,
) -> Offer:
    return Offer(
        source="test",
        external_id=external_id,
        mode="bus",
        operator="Test",
        depart_at=depart_at,
        arrive_at=arrive_at,
        price=price,
        pickup_name="Thiene",
        dropoff_name="Trento",
        pickup_lat=45.6877202,
        pickup_lon=11.4861438,
        dropoff_lat=46.1029536,
        dropoff_lon=11.1297425,
        duration_min=int((arrive_at - depart_at).total_seconds() // 60),
    )


class WindowTests(unittest.TestCase):
    """La ricerca riguarda il giorno indicato, non quello precedente."""

    def setUp(self):
        # 22 ottobre 2026, 11:00 (ora italiana) = 09:00 UTC
        self.arrive_by = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)

    def test_window_start_is_midnight_of_the_requested_day(self):
        ctx = make_ctx(self.arrive_by)
        start = ctx.window_start
        # Mezzanotte del 22 ottobre, ora italiana (+02:00) = 22/10 00:00 CEST
        self.assertEqual(start.astimezone(ROME).date().isoformat(), "2026-10-22")
        self.assertEqual(start.astimezone(ROME).hour, 0)

    def test_depart_after_wins(self):
        moment = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
        ctx = make_ctx(self.arrive_by, depart_after=moment)
        self.assertEqual(ctx.window_start, moment)

    def test_planner_ignores_previous_day_offer(self):
        ctx = make_ctx(self.arrive_by)
        ieri_partenza = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)
        ieri_arrivo = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
        offer = make_offer("ieri", ieri_partenza, ieri_arrivo, 12.0)
        self.assertEqual(build_candidates([offer], ctx), [])

    def test_planner_keeps_same_day_offer(self):
        ctx = make_ctx(self.arrive_by)
        partenza = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
        arrivo = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
        offer = make_offer("oggi", partenza, arrivo, 12.0)
        candidates = build_candidates([offer], ctx)
        self.assertEqual(len(candidates), 1)

    def test_planner_respects_budget(self):
        ctx = make_ctx(self.arrive_by, max_price=10)
        partenza = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
        arrivo = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
        offer = make_offer("caro", partenza, arrivo, 12.0)
        self.assertEqual(build_candidates([offer], ctx), [])


class QueryStartTests(unittest.TestCase):
    """L'API Trenitalia dà solo ~10 soluzioni: bisogna interrogarla due volte."""

    def test_two_days_window_asks_also_the_arrival_day(self):
        from app.adapters.trenitalia import query_starts

        # Arrivo mercoledi 22 alle 11, partenza minima martedi 21 alle 6.
        ctx = make_ctx(
            datetime(2026, 10, 22, 11, 0, tzinfo=ROME),
            depart_after=datetime(2026, 10, 21, 6, 0, tzinfo=ROME),
        )
        starts = query_starts(ctx)
        self.assertEqual(len(starts), 2)
        # La sera prima (treni notturni) e il giorno di arrivo.
        self.assertEqual(starts[0], datetime(2026, 10, 21, 18, 0, tzinfo=ROME))
        self.assertEqual(starts[1], datetime(2026, 10, 22, 0, 0, tzinfo=ROME))

    def test_same_day_departure_asks_once(self):
        from app.adapters.trenitalia import query_starts

        ctx = make_ctx(
            datetime(2026, 10, 22, 11, 0, tzinfo=ROME),
            depart_after=datetime(2026, 10, 22, 6, 0, tzinfo=ROME),
        )
        starts = query_starts(ctx)
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0], datetime(2026, 10, 22, 6, 0, tzinfo=ROME))

    def test_without_departure_asks_the_arrival_day(self):
        from app.adapters.trenitalia import query_starts

        ctx = make_ctx(datetime(2026, 10, 22, 11, 0, tzinfo=ROME))
        starts = query_starts(ctx)
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0], datetime(2026, 10, 22, 0, 0, tzinfo=ROME))


class RelaxQuestionTests(unittest.TestCase):
    """Le domande citano il prezzo reale quando il budget e' troppo basso."""

    def setUp(self):
        self.arrive_by = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)

    def _offer(self, price: float, day: int = 22) -> Offer:
        partenza = datetime(2026, 10, day, 6, 0, tzinfo=timezone.utc)
        arrivo = datetime(2026, 10, day, 8, 0, tzinfo=timezone.utc)
        return make_offer(f"o{day}", partenza, arrivo, price)

    def test_asks_to_raise_budget_with_real_price(self):
        ctx = make_ctx(self.arrive_by, max_price=8)
        offer = self._offer(11.9)
        questions = intelligence._relax_questions([offer], ctx, {})
        budget = next((q for q in questions if q["key"] == "budget_flex"), None)
        self.assertIsNotNone(budget)
        self.assertIn("11.90", budget["text"])
        raise_opt = next(o for o in budget["options"] if o["id"] == "raise")
        self.assertGreater(raise_opt["prefs"]["budget_raise"], 8)

    def test_ignores_previous_day_offer_for_budget(self):
        # Il prezzo citato deve essere quello di una corsa nella finestra.
        ctx = make_ctx(self.arrive_by, max_price=8)
        ieri = self._offer(9.0, day=21)
        oggi = self._offer(15.0, day=22)
        questions = intelligence._relax_questions([ieri, oggi], ctx, {})
        budget = next((q for q in questions if q["key"] == "budget_flex"), None)
        self.assertIsNotNone(budget)
        self.assertIn("15.00", budget["text"])

    def test_offers_arriving_the_evening_before(self):
        # Se non c'e' niente in tempo, l'agente propone la sera prima.
        ctx = make_ctx(self.arrive_by)
        sera = self._offer(12.0, day=21)
        questions = intelligence._relax_questions([sera], ctx, {})
        giorno = next((q for q in questions if q["key"] == "arrivo_giorno_prima"), None)
        self.assertIsNotNone(giorno)
        yes = next(o for o in giorno["options"] if o["id"] == "yes")
        self.assertTrue(yes["prefs"]["arrivo_giorno_prima"])

    def test_evening_before_option_opens_the_window(self):
        # Con la preferenza attiva, la corsa della sera prima e' ammessa.
        ctx = make_ctx(self.arrive_by, allow_day_before=True)
        sera = self._offer(12.0, day=21)
        candidates = build_candidates([sera], ctx)
        self.assertEqual(len(candidates), 1)


class TelegramChatsTests(unittest.TestCase):
    """Le chat Telegram (privati, gruppi, canali) si registrano e si elencano."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "chats.db"
        db.init_db()
        from app import auth

        self.user = auth.create_user(
            "proprietario", "password-lunga-1", is_admin=True, status=auth.STATUS_ACTIVE
        )

    def tearDown(self):
        db.DB_PATH = self._old_path
        self._tmp.cleanup()

    def test_register_and_list(self):
        from app.services import telegram_chats

        telegram_chats.register_chat(
            int(self.user["id"]), "-100123", chat_type="supergroup", title="Amici"
        )
        chats = telegram_chats.list_chats(int(self.user["id"]))
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["chat_type"], "supergroup")
        self.assertEqual(chats[0]["title"], "Amici")

    def test_group_types(self):
        from app.services import telegram_chats

        self.assertIn("group", telegram_chats.GROUP_TYPES)
        self.assertIn("supergroup", telegram_chats.GROUP_TYPES)

    def test_set_default(self):
        from app.services import telegram_chats

        uid = int(self.user["id"])
        telegram_chats.register_chat(uid, "-1", chat_type="private", title="Privato")
        telegram_chats.register_chat(uid, "-2", chat_type="supergroup", title="Gruppo")
        self.assertTrue(telegram_chats.set_default(uid, "-2"))
        self.assertEqual(telegram_chats.chat_ids(uid, only_default=True), ["-2"])
        # Senza filtro tornano tutte, la predefinita per prima.
        self.assertEqual(telegram_chats.chat_ids(uid), ["-2", "-1"])

    def test_remove(self):
        from app.services import telegram_chats

        uid = int(self.user["id"])
        telegram_chats.register_chat(uid, "-1", chat_type="private")
        self.assertTrue(telegram_chats.remove_chat(uid, "-1"))
        self.assertEqual(telegram_chats.list_chats(uid), [])

    def test_find_by_chat(self):
        from app.services import telegram_chats

        uid = int(self.user["id"])
        telegram_chats.register_chat(uid, "-42", chat_type="group", title="G")
        found = telegram_chats.find_by_chat("-42")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["user_id"], uid)


if __name__ == "__main__":
    unittest.main()
