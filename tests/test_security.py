"""Prove per le correzioni di sicurezza: limiti di tentativi, ruoli, cookie.

Copre i punti emersi dall'audit:
- lo sblocco non puo' essere martellato a forza bruta;
- le impostazioni che cambiano l'accesso sono da amministratore;
- il cookie di sessione non viaggia cross-site (CSRF);
- lo stato pubblico dell'accesso non rivela chi sei.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import access, auth, db, deps, main


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "sicurezza.db"
        db.init_db()
        self.client = TestClient(main.app)
        # Un amministratore con password, e un membro senza.
        with db.db() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, is_admin, status, created_at) "
                "VALUES (?,?,?,?,?)",
                ("capo", auth.hash_password("segreta-123"), 1, auth.STATUS_ACTIVE, "2026-01-01T00:00:00"),
            )
            conn.execute(
                "INSERT INTO users(username, password_hash, is_admin, status, created_at) "
                "VALUES (?,?,?,?,?)",
                ("membro", auth.hash_password("segreta-123"), 0, auth.STATUS_ACTIVE, "2026-01-01T00:00:00"),
            )
        access.set_password("segreta-123")

    def tearDown(self):
        db.DB_PATH = self._old_path
        self._tmp.cleanup()

    # ------------------------------------------------------------------ #
    # Sblocco: niente forza bruta
    # ------------------------------------------------------------------ #
    def test_tentativi_falliti_vengono_bloccati(self):
        for _ in range(deps.TENTATIVI_MAX):
            risposta = self.client.post("/api/auth/unlock", json={"password": "sbagliata"})
            self.assertEqual(risposta.status_code, 401)
        # Il tentativo dopo il tetto non arriva nemmeno al controllo della password.
        risposta = self.client.post("/api/auth/unlock", json={"password": "sbagliata"})
        self.assertEqual(risposta.status_code, 429)
        # E la password giusta non sblocca piu' finche' non passa il tempo.
        risposta = self.client.post("/api/auth/unlock", json={"password": "segreta-123"})
        self.assertEqual(risposta.status_code, 429)

    def test_sblocco_corretto_azzera_i_giorni(self):
        for _ in range(deps.TENTATIVI_MAX - 1):
            self.client.post("/api/auth/unlock", json={"password": "sbagliata"})
        risposta = self.client.post("/api/auth/unlock", json={"password": "segreta-123"})
        self.assertEqual(risposta.status_code, 200)
        # Dopo il successo il conto riparte: un utente legittimo non resta fuori.
        for _ in range(deps.TENTATIVI_MAX - 1):
            self.client.post("/api/auth/unlock", json={"password": "sbagliata"})
        risposta = self.client.post("/api/auth/unlock", json={"password": "segreta-123"})
        self.assertEqual(risposta.status_code, 200)

    # ------------------------------------------------------------------ #
    # Ruoli: l'accesso si tocca solo da amministratore
    # ------------------------------------------------------------------ #
    def _sessione(self, username: str) -> str:
        with db.db() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
        token, _ = auth.create_session(int(row["id"]))
        return token

    def test_membro_non_puo_cambiare_la_password(self):
        token = self._sessione("membro")
        risposta = self.client.post(
            "/api/auth/access", json={"password": "nuova-password-1"}, cookies={auth.COOKIE_NAME: token}
        )
        self.assertEqual(risposta.status_code, 403)

    def test_membro_non_puo_cancellare_la_chiave(self):
        access.new_key()
        token = self._sessione("membro")
        risposta = self.client.delete("/api/auth/key", cookies={auth.COOKIE_NAME: token})
        self.assertEqual(risposta.status_code, 403)
        self.assertTrue(access.has_key())

    def test_amministratore_puo_gestire_la_chiave(self):
        token = self._sessione("capo")
        risposta = self.client.delete("/api/auth/key", cookies={auth.COOKIE_NAME: token})
        self.assertEqual(risposta.status_code, 200)
        self.assertFalse(access.has_key())

    # ------------------------------------------------------------------ #
    # Cookie: niente SameSite=None, altrimenti il CSRF passa
    # ------------------------------------------------------------------ #
    def test_cookie_di_sessione_e_httponly_e_lax(self):
        risposta = self.client.post("/api/auth/unlock", json={"password": "segreta-123"})
        self.assertEqual(risposta.status_code, 200)
        header = risposta.headers.get("set-cookie", "")
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=lax", header.replace("samesite", "SameSite"))
        self.assertNotIn("SameSite=none", header.replace("samesite", "SameSite"))

    # ------------------------------------------------------------------ #
    # Dati pubblici: lo stato non rivela l'utente a chi non e' dentro
    # ------------------------------------------------------------------ #
    def test_stato_pubblico_non_rivela_l_utente(self):
        risposta = self.client.get("/api/auth/status")
        self.assertEqual(risposta.status_code, 200)
        corpo = risposta.json()
        self.assertIsNone(corpo.get("user"))
        self.assertTrue(corpo["locked"])

    def test_stato_con_sessione_rivela_l_utente(self):
        token = self._sessione("capo")
        risposta = self.client.get("/api/auth/status", cookies={auth.COOKIE_NAME: token})
        self.assertEqual(risposta.status_code, 200)
        self.assertEqual(risposta.json()["user"]["username"], "capo")

    # ------------------------------------------------------------------ #
    # IP reale: gli header inoltrati non si credono da soli
    # ------------------------------------------------------------------ #
    def test_client_ip_usa_l_header_inoltrato_solo_da_proxy_locale(self):
        class FakeClient:
            host = "203.0.113.9"

        class FakeRequest:
            client = FakeClient()
            headers = {"x-forwarded-for": "198.51.100.7, 203.0.113.9"}

        # Connessione diretta: l'header non e' attendibile.
        self.assertEqual(deps.client_ip(FakeRequest()), "203.0.113.9")

        class FakeLocal(FakeRequest):
            client = FakeClient()
            client.host = "127.0.0.1"

        # Dietro il proxy locale: vale l'ultimo valore (quello riscritto da Nginx).
        self.assertEqual(deps.client_ip(FakeLocal()), "203.0.113.9")


if __name__ == "__main__":
    unittest.main()
