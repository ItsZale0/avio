"""API delle notifiche push: stato e canale sulle ricerche.

Le push si attivano dal telefono, ma il server deve sapere due cose: quanti
dispositivi sono iscritti e quante ricerche hanno davvero il canale `push`.
Senza il secondo dato un utente attiva le notifiche e non riceve nulla.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import access, auth, db, main
from app.timeutil import now_utc


class PushApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "push-api.db"
        db.init_db()
        with db.db() as conn:
            cursor = conn.execute(
                "INSERT INTO users(username, password_hash, is_admin, status, created_at) "
                "VALUES (?,?,0,?,?)",
                ("telefono", "x", auth.STATUS_ACTIVE, now_utc().isoformat()),
            )
            self.user_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO watches(user_id, from_place, to_place, arrive_by, notify, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    self.user_id,
                    "Bressanone",
                    "Rovereto",
                    "2026-12-01T18:00:00+00:00",
                    '["web","telegram"]',
                    now_utc().isoformat(),
                ),
            )
        # Con la chiave d'accesso impostata le API chiedono la sessione:
        # e' la configurazione vera dell'app. Va impostata prima di creare
        # la sessione, perche' un cambio di password chiude le sessioni.
        access.set_password("segreta-123")
        token, _ = auth.create_session(self.user_id)
        self.cookies = {auth.COOKIE_NAME: token}
        self.client = TestClient(main.app)

    def tearDown(self):
        db.DB_PATH = self._old_path
        self._tmp.cleanup()

    def test_lo_stato_dice_quante_ricerche_usano_le_push(self):
        risposta = self.client.get("/api/push/status", cookies=self.cookies)
        self.assertEqual(risposta.status_code, 200)
        data = risposta.json()
        self.assertEqual(data["watches"], 1)
        self.assertEqual(data["watches_push"], 0)
        self.assertTrue(data["public_key"])

    def test_aggiungere_il_canale_push_alle_ricerche(self):
        risposta = self.client.post("/api/push/watches", cookies=self.cookies)
        self.assertEqual(risposta.status_code, 200)
        data = risposta.json()
        self.assertEqual(data["changed"], 1)
        self.assertEqual(data["watches_push"], 1)

        # La seconda volta non cambia niente: nessun doppione.
        ancora = self.client.post("/api/push/watches", cookies=self.cookies).json()
        self.assertEqual(ancora["changed"], 0)
        self.assertEqual(ancora["watches_push"], 1)

        with db.db() as conn:
            row = conn.execute(
                "SELECT notify FROM watches WHERE user_id = ?", (self.user_id,)
            ).fetchone()
        canali = db.load_json(row["notify"], [])
        self.assertEqual(canali, ["web", "telegram", "push"])

    def test_senza_sessione_non_si_cambia_niente(self):
        risposta = self.client.post("/api/push/watches")
        self.assertIn(risposta.status_code, (401, 403))
        with db.db() as conn:
            row = conn.execute("SELECT notify FROM watches").fetchone()
        self.assertNotIn("push", db.load_json(row["notify"], []))


if __name__ == "__main__":
    unittest.main()
