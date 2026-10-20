"""La sezione "Chiedi": l'agente risponde e applica le richieste."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from app.ai import chat
from app.services import questions as questions_service

ROME = timezone(timedelta(hours=2))


class PatchDeterministicTests(unittest.TestCase):
    WATCH = {
        "id": 1,
        "from_place": "Bressanone",
        "to_place": "Rovereto",
        "max_price": 8.0,
        "prefs": {},
    }

    def test_the_budget_is_recognised(self):
        patch = chat._patch_deterministica(self.WATCH, "alzo il budget a 15 euro")
        self.assertEqual(patch.get("budget_raise"), 15.0)

    def test_the_train_is_recognised(self):
        patch = chat._patch_deterministica(self.WATCH, "preferisco viaggiare in treno")
        self.assertEqual(patch.get("prefer_modes"), ["treno"])

    def test_no_changes_is_recognised(self):
        patch = chat._patch_deterministica(self.WATCH, "voglio viaggiare senza cambi")
        self.assertTrue(patch.get("no_changes"))

    def test_arriving_earlier_is_recognised(self):
        patch = chat._patch_deterministica(self.WATCH, "preferisco arrivare prima")
        self.assertTrue(patch.get("prefer_fast"))

    def test_the_pass_is_recognised(self):
        patch = chat._patch_deterministica(self.WATCH, "ho l'abbonamento IVOL")
        self.assertEqual(patch.get("add_pass"), "ivol_lombardia")

    def test_a_pure_question_changes_nothing(self):
        patch = chat._patch_deterministica(self.WATCH, "quanto costa?")
        self.assertEqual(patch, {})


class JsonReadingTests(unittest.TestCase):
    def test_the_json_is_read(self):
        reply, patch = chat._leggi_json(
            json.dumps({"reply": "Ciao", "prefs": {"budget_raise": 12}, "rerun": True}),
            {},
            "ciao",
        )
        self.assertEqual(reply["reply"], "Ciao")
        self.assertEqual(patch.get("budget_raise"), 12)

    def test_the_code_fences_are_removed(self):
        reply, patch = chat._leggi_json(
            '```json\n{"reply": "Va bene", "prefs": {}}\n```', {}, "va bene"
        )
        self.assertEqual(reply["reply"], "Va bene")

    def test_an_unknown_key_is_ignored(self):
        _, patch = chat._leggi_json(
            json.dumps({"reply": "x", "prefs": {"drop_table": 1}}), {}, "x"
        )
        self.assertEqual(patch, {})

    def test_a_broken_answer_does_not_crash(self):
        reply, patch = chat._leggi_json("non sono json", {}, "ciao")
        self.assertTrue(reply.get("reply"))
        self.assertEqual(patch, {})


class MergeTests(unittest.TestCase):
    def test_the_departure_time_can_be_changed(self):
        from app import db, store

        watch = {
            "id": 999,
            "from_place": "Thiene",
            "to_place": "Trento",
            "prefs": {},
        }
        with db.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO watches(id, user_id, name, from_place, to_place, "
                "arrive_by, max_price, radius_km, passengers, sources, notify, interval_min, "
                "active, created_at, prefs) VALUES(999, 3, 'test', 'Thiene', 'Trento', "
                "'2026-10-22T09:00:00', 10, 5, 1, '[]', '[]', 60, 1, '2026-10-20T10:00:00', '{}')"
            )
        try:
            merge = questions_service.merge_prefs(
                watch, {"set_depart_after": "2026-10-22T07:00:00"}
            )
            self.assertEqual(
                merge["changes"].get("depart_after"), "2026-10-22T07:00:00"
            )
            salvata = store.get_watch(999)
            self.assertEqual(salvata.get("depart_after"), "2026-10-22T07:00:00")
        finally:
            with db.db() as conn:
                conn.execute("DELETE FROM watches WHERE id = 999")


class ChatPersistenceTests(unittest.TestCase):
    """La conversazione resta: e' salvata, non solo nella pagina."""

    WATCH_ID = 998

    def setUp(self):
        from app import db

        # La tabella della chat nasce con init_db: il database di sviluppo
        # puo' essere piu' vecchio (l'agente la crea all'avvio).
        db.init_db()
        with db.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO watches(id, user_id, name, from_place, to_place, "
                "arrive_by, max_price, radius_km, passengers, sources, notify, interval_min, "
                "active, created_at, prefs) VALUES(998, 3, 'test chat', 'Thiene', 'Trento', "
                "'2026-10-22T09:00:00', 10, 5, 1, '[]', '[]', 60, 1, '2026-10-20T10:00:00', '{}')"
            )
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        from app import db

        with db.db() as conn:
            conn.execute("DELETE FROM watches WHERE id = 998")

    def test_messages_are_saved_in_order(self):
        from app import store

        store.add_chat_message(998, "io", "Quanto costa?", user_id=3)
        store.add_chat_message(
            998, "agente", "9,99 EUR", ["budget 15.00 EUR"], user_id=3
        )
        messaggi = store.list_chat_messages(998)
        self.assertEqual([m["role"] for m in messaggi], ["io", "agente"])
        self.assertEqual(messaggi[1]["text"], "9,99 EUR")
        self.assertEqual(messaggi[1]["changes"], ["budget 15.00 EUR"])

    def test_the_history_can_be_cleared(self):
        from app import store

        store.add_chat_message(998, "io", "Ciao", user_id=3)
        self.assertEqual(store.clear_chat_messages(998), 1)
        self.assertEqual(store.list_chat_messages(998), [])

    def test_the_labels_are_readable(self):
        etichette = chat._etichette({"max_price": 15.0, "depart_after": "2026-10-22T09:00"})
        self.assertEqual(etichette[0], "budget 15.00 EUR")
        self.assertIn("partenza dopo 2026-10-22 09:00", etichette[1])


if __name__ == "__main__":
    unittest.main()
