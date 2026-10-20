"""Test dell'accesso all'app: password, chiave segreta e sessioni.

I test girano su un database temporaneo: il `data/carpool.db` reale non
viene toccato.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import access, auth, db, store
from app.schemas import WatchIn

ROME = timezone(timedelta(hours=2))


def make_watch(from_place: str = "Thiene", to_place: str = "Trento") -> WatchIn:
    arrive = (datetime.now(ROME) + timedelta(days=3)).replace(microsecond=0)
    return WatchIn(
        from_place=from_place,
        to_place=to_place,
        arrive_by=arrive,
        from_lat=45.68,
        from_lon=11.48,
        to_lat=46.10,
        to_lon=11.13,
    )


class _FakeRequest:
    """Richiesta minima per le dipendenze FastAPI."""

    def __init__(self, scheme: str = "http", headers: dict[str, str] | None = None):
        self.url = type("U", (), {"scheme": scheme})()
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.cookies: dict[str, str] = {}


class AccessTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "access.db"
        db.init_db()
        # Un account proprietario, come nell'istanza reale.
        self.owner = auth.create_user(
            "proprietario", "password-lunga-1", is_admin=True, status=auth.STATUS_ACTIVE
        )

    def tearDown(self):
        db.DB_PATH = self._old_path
        self._tmp.cleanup()


class PasswordTests(AccessTestCase):
    def test_no_password_means_not_locked(self):
        self.assertFalse(access.has_password())
        self.assertFalse(access.is_locked())

    def test_set_and_check_password(self):
        self.assertTrue(access.set_password("segreta-123"))
        self.assertTrue(access.has_password())
        self.assertTrue(access.is_locked())
        self.assertTrue(access.check_password("segreta-123"))

    def test_wrong_password_is_rejected(self):
        access.set_password("segreta-123")
        self.assertFalse(access.check_password("segreta-124"))
        self.assertFalse(access.check_password(""))

    def test_short_password_is_rejected(self):
        with self.assertRaises(ValueError):
            access.set_password("corta")

    def test_password_is_stored_hashed(self):
        access.set_password("segreta-123")
        stored = access.app_settings.get("access_password", "")
        self.assertNotIn("segreta-123", stored)
        self.assertTrue(stored.startswith("$2b$"))

    def test_empty_password_removes_the_lock(self):
        access.set_password("segreta-123")
        self.assertFalse(access.set_password(""))
        self.assertFalse(access.is_locked())

    def test_unlock_with_correct_password(self):
        access.set_password("segreta-123")
        token, error = access.unlock("segreta-123")
        self.assertIsNotNone(token)
        self.assertEqual(error, "")

    def test_unlock_with_wrong_password(self):
        access.set_password("segreta-123")
        token, error = access.unlock("sbagliata")
        self.assertIsNone(token)
        self.assertIn("non corretta", error)

    def test_unlock_without_password_creates_session(self):
        # Senza password l'app e' libera: non serve sbloccare.
        token, error = access.unlock("qualsiasi")
        self.assertEqual(error, "")
        self.assertIsNone(token)


class KeyTests(AccessTestCase):
    def test_new_key_is_returned_and_works(self):
        key = access.new_key()
        self.assertTrue(key)
        self.assertTrue(access.has_key())
        self.assertTrue(access.check_key(key))

    def test_key_is_stored_hashed(self):
        key = access.new_key()
        stored = access.app_settings.get("access_key", "")
        self.assertNotIn(key, stored)
        self.assertEqual(len(stored), 64)  # sha256 in esadecimale

    def test_wrong_key_is_rejected(self):
        access.new_key()
        self.assertFalse(access.check_key("chiave-sbagliata"))
        self.assertFalse(access.check_key(""))

    def test_clear_key(self):
        access.new_key()
        access.clear_key()
        self.assertFalse(access.has_key())

    def test_key_also_unlocks(self):
        access.set_password("segreta-123")
        key = access.new_key()
        token, error = access.unlock(key)
        self.assertIsNotNone(token)
        self.assertEqual(error, "")

    def test_each_key_is_different(self):
        first = access.new_key()
        second = access.new_key()
        self.assertNotEqual(first, second)
        self.assertFalse(access.check_key(first))


class UnlockFlowTests(AccessTestCase):
    def test_session_created_by_unlock_is_usable(self):
        access.set_password("segreta-123")
        token, _ = access.unlock("segreta-123")
        user = auth.resolve_session(token)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], int(self.owner["id"]))

    def test_changing_password_revokes_sessions(self):
        access.set_password("segreta-123")
        token, _ = access.unlock("segreta-123")
        self.assertIsNotNone(auth.resolve_session(token))
        access.set_password("nuova-456")
        self.assertIsNone(auth.resolve_session(token))

    def test_require_user_without_password_returns_owner(self):
        from app import deps

        user = deps.require_user(_FakeRequest())
        self.assertEqual(user["id"], int(self.owner["id"]))

    def test_require_user_with_password_needs_session(self):
        from app import deps
        from fastapi import HTTPException

        access.set_password("segreta-123")
        with self.assertRaises(HTTPException):
            deps.require_user(_FakeRequest())

    def test_require_user_with_session_passes(self):
        from app import deps

        access.set_password("segreta-123")
        token, _ = access.unlock("segreta-123")
        request = _FakeRequest()
        request.cookies[auth.COOKIE_NAME] = token
        user = deps.require_user(request)
        self.assertEqual(user["id"], int(self.owner["id"]))


class DataSeparationTests(AccessTestCase):
    """Con accesso disattivato le ricerche restano del proprietario unico."""

    def test_watches_belong_to_the_owner(self):
        store.create_watch(make_watch(), user_id=int(self.owner["id"]))
        self.assertEqual(len(store.list_watches(int(self.owner["id"]))), 1)

    def test_public_user_hides_password(self):
        public = auth.public_user(self.owner)
        self.assertNotIn("password_hash", public)
        self.assertNotIn("password", str(public))


class ProxyCookieTest(unittest.TestCase):
    """Il cookie di sessione deve avere Secure quando c'e' un reverse proxy."""

    def test_http_request_is_not_secure(self):
        from app import deps

        self.assertFalse(deps.is_secure_request(_FakeRequest("http")))

    def test_https_request_is_secure(self):
        from app import deps

        self.assertTrue(deps.is_secure_request(_FakeRequest("https")))

    def test_forwarded_proto_https_is_secure(self):
        from app import deps

        request = _FakeRequest("http", {"X-Forwarded-Proto": "https"})
        self.assertTrue(deps.is_secure_request(request))

    def test_forwarded_proto_http_is_not_secure(self):
        from app import deps

        request = _FakeRequest("http", {"X-Forwarded-Proto": "http"})
        self.assertFalse(deps.is_secure_request(request))


if __name__ == "__main__":
    unittest.main()
