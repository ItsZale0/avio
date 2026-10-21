"""Le cartelle: i raggruppamenti delle ricerche.

Una cartella e' un raggruppamento per viaggio o per contesto. Le ricerche
ci dentro si vedono nell'indice; se la cartella sparisce, le ricerche no.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import db, store
from app.timeutil import now_utc


class GroupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "cartelle.db"
        db.init_db()
        self.user_id = self._make_user("test_cartelle")
        self.other_id = self._make_user("test_altro")
        self.watch_id = self._make_watch(self.user_id)

    def tearDown(self):
        db.DB_PATH = self._old_path
        self._tmp.cleanup()

    def _make_user(self, username: str) -> int:
        with db.db() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, is_admin, created_at) VALUES (?,?,1,?)",
                (username, "x", now_utc().isoformat()),
            )
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            return int(row["id"])

    def _make_watch(self, user_id: int) -> int:
        with db.db() as conn:
            cursor = conn.execute(
                "INSERT INTO watches(user_id, from_place, to_place, arrive_by, next_run_at,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (
                    user_id,
                    "Milano",
                    "Roma",
                    now_utc().isoformat(),
                    now_utc().isoformat(),
                    now_utc().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def test_crea_cartella_e_la_assegna(self):
        gruppo = store.create_group("Viaggio a Trento", user_id=self.user_id)
        self.assertEqual(gruppo["name"], "Viaggio a Trento")
        self.assertEqual(gruppo["count"], 0)

        store.update_watch(
            self.watch_id,
            _patch(group_id=gruppo["id"]),
            user_id=self.user_id,
        )
        elenchi = store.list_groups(self.user_id)
        self.assertEqual(len(elenchi), 1)
        self.assertEqual(elenchi[0]["count"], 1)
        self.assertEqual(store.get_watch(self.watch_id, user_id=self.user_id)["group_id"], gruppo["id"])

    def test_il_nome_e_unico_per_utente(self):
        store.create_group("Lavoro", user_id=self.user_id)
        with self.assertRaises(ValueError):
            store.create_group("lavoro", user_id=self.user_id)
        # Un altro utente puo' usare lo stesso nome: le cartelle sono sue.
        altra = store.create_group("Lavoro", user_id=self.other_id)
        self.assertTrue(altra["id"])

    def test_rinomina_di_una_cartella_inesistente_non_fa_nulla(self):
        # Il nome vuoto e' un errore, la cartella che non c'e' semplicemente
        # non viene toccata (l'API risponde 404).
        with self.assertRaises(ValueError):
            store.rename_group(999, "   ", user_id=self.user_id)
        self.assertIsNone(store.rename_group(999, "X", user_id=self.user_id))
        self.assertIsNone(store.get_group(999, user_id=self.user_id))

    def test_eliminare_una_cartella_non_perde_le_ricerche(self):
        gruppo = store.create_group("Viaggio", user_id=self.user_id)
        store.update_watch(self.watch_id, _patch(group_id=gruppo["id"]), user_id=self.user_id)

        self.assertTrue(store.delete_group(gruppo["id"], user_id=self.user_id))
        ricerca = store.get_watch(self.watch_id, user_id=self.user_id)
        self.assertIsNotNone(ricerca)
        self.assertIsNone(ricerca["group_id"])
        self.assertEqual(store.list_groups(self.user_id), [])

    def test_le_cartelle_di_un_utente_non_leggono_le_altre(self):
        gruppo = store.create_group("Privata", user_id=self.other_id)
        self.assertIsNone(store.get_group(gruppo["id"], user_id=self.user_id))
        self.assertFalse(store.delete_group(gruppo["id"], user_id=self.user_id))
        self.assertEqual(store.list_groups(self.user_id), [])

    def test_rinomina(self):
        gruppo = store.create_group("Vecchio", user_id=self.user_id)
        rinominata = store.rename_group(gruppo["id"], "Nuovo nome", user_id=self.user_id)
        self.assertEqual(rinominata["name"], "Nuovo nome")
        with self.assertRaises(ValueError):
            store.rename_group(gruppo["id"], "   ", user_id=self.user_id)


def _patch(**kwargs):
    from app.schemas import WatchUpdate

    return WatchUpdate(**kwargs)
