"""Test del ciclo di vita delle domande dell'agente.

Fra le cose verificate: la stessa domanda non viene riproposta di continuo,
ma puo' tornare dopo qualche ora (i prezzi cambiano).

I test girano su un database temporaneo: il `data/carpool.db` reale non
viene toccato.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app import db
from app.services import questions as questions_service
from app.timeutil import now_utc


class QuestionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "domande.db"
        db.init_db()
        self.watch_id = self._make_watch()

    def tearDown(self):
        db.DB_PATH = self._old_path
        self._tmp.cleanup()

    def _make_watch(self) -> int:
        with db.db() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, is_admin, created_at) "
                "VALUES (?,?,1,?) "
                "ON CONFLICT(username) DO UPDATE SET is_admin=1",
                ("test_domande", "x", now_utc().isoformat()),
            )
            owner = conn.execute(
                "SELECT id FROM users WHERE username = ?", ("test_domande",)
            ).fetchone()
            cursor = conn.execute(
                "INSERT INTO watches(user_id, from_place, to_place, arrive_by, next_run_at,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (
                    owner["id"],
                    "Milano",
                    "Roma",
                    now_utc().isoformat(),
                    now_utc().isoformat(),
                    now_utc().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def _question(self, key="budget_flex", text="Vuoi alzare il budget?"):
        return {
            "key": key,
            "text": text,
            "options": [
                {"id": "keep", "label": "No", "prefs": {}},
                {"id": "raise", "label": "Si", "prefs": {"budget_raise": 15}},
            ],
        }

    def test_same_key_is_not_asked_twice(self):
        first = questions_service.create_many(self.watch_id, None, [self._question()])
        self.assertEqual(len(first), 1)
        second = questions_service.create_many(self.watch_id, None, [self._question()])
        self.assertEqual(second, [])

    def test_same_text_different_key_is_not_asked_twice(self):
        first = questions_service.create_many(self.watch_id, None, [self._question("a")])
        self.assertEqual(len(first), 1)
        second = questions_service.create_many(
            self.watch_id, None, [self._question("b")]
        )
        self.assertEqual(second, [])

    def test_question_can_come_back_after_a_while(self):
        questions_service.create_many(self.watch_id, None, [self._question()])
        # L'utente risponde "no": la domanda e' chiusa.
        with db.db() as conn:
            conn.execute(
                "UPDATE questions SET status='answered', answer='keep', answered_at=? "
                "WHERE watch_id = ?",
                (now_utc().isoformat(), self.watch_id),
            )
        # Subito dopo non si ripropone.
        self.assertEqual(
            questions_service.create_many(self.watch_id, None, [self._question()]), []
        )
        # Ma dopo REASK_AFTER_HOURS si ripropone: i prezzi possono essere cambiati.
        with db.db() as conn:
            conn.execute(
                "UPDATE questions SET answered_at=? WHERE watch_id = ?",
                (
                    (
                        now_utc()
                        - timedelta(hours=questions_service.REASK_AFTER_HOURS + 1)
                    ).isoformat(),
                    self.watch_id,
                ),
            )
        again = questions_service.create_many(self.watch_id, None, [self._question()])
        self.assertEqual(len(again), 1)

    def test_max_two_pending(self):
        questions_service.create_many(self.watch_id, None, [self._question("a")])
        questions_service.create_many(self.watch_id, None, [self._question("b")])
        third = questions_service.create_many(self.watch_id, None, [self._question("c")])
        self.assertEqual(third, [])

    def test_prefs_are_saved_from_the_option(self):
        question = {
            "key": "priorita",
            "text": "Preferisci arrivare prima?",
            "options": [
                {"id": "veloce", "label": "Prima", "prefs": {"prefer_fast": True}},
                {"id": "economico", "label": "Meno", "prefs": {"prefer_fast": False}},
            ],
        }
        questions_service.create_many(self.watch_id, None, [question])
        with db.db() as conn:
            watch = conn.execute(
                "SELECT * FROM watches WHERE id = ?", (self.watch_id,)
            ).fetchone()
        watch = dict(watch)
        merged = questions_service.merge_prefs(watch, {"prefer_fast": True})
        self.assertTrue(merged["prefs"]["prefer_fast"])


if __name__ == "__main__":
    unittest.main()
