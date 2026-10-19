"""Test di autenticazione multi-utente e separazione dei dati.

I test girano su un database temporaneo: il `data/carpool.db` reale non
viene toccato.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import auth, db, store
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


class AuthTestCase(unittest.TestCase):
    """Base: database temporaneo con lo schema completo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "auth.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._old_path
        self._tmp.cleanup()

    def make_admin(self, username: str = "admin", password: str = "password-lunga-1") -> dict:
        return auth.create_user(username, password, is_admin=True, status=auth.STATUS_ACTIVE)

    def make_member(self, username: str = "mario", password: str = "password-lunga-2") -> dict:
        return auth.create_user(username, password, status=auth.STATUS_ACTIVE)


class PasswordTest(AuthTestCase):
    def test_hash_is_not_plaintext(self):
        digest = auth.hash_password("segreta-123")
        self.assertNotIn("segreta-123", digest)
        self.assertTrue(digest.startswith("$2b$"))

    def test_verify_accepts_right_password(self):
        digest = auth.hash_password("segreta-123")
        self.assertTrue(auth.verify_password("segreta-123", digest))

    def test_verify_rejects_wrong_password(self):
        digest = auth.hash_password("segreta-123")
        self.assertFalse(auth.verify_password("segreta-2", digest))

    def test_verify_with_empty_hash_is_false(self):
        self.assertFalse(auth.verify_password("qualsiasi", ""))

    def test_verify_with_garbage_hash_is_false(self):
        self.assertFalse(auth.verify_password("qualsiasi", "non-e-un-hash"))


class UserTest(AuthTestCase):
    def test_first_user_is_admin(self):
        user = self.make_admin()
        self.assertTrue(user["is_admin"])
        self.assertEqual(user["status"], auth.STATUS_ACTIVE)

    def test_username_is_normalized(self):
        user = auth.create_user("  Mario  ", "password-lunga-2")
        self.assertEqual(user["username"], "mario")
        self.assertIsNotNone(auth.get_user_by_username("MARIO"))

    def test_duplicate_username_is_rejected(self):
        self.make_admin("admin")
        with self.assertRaises(ValueError):
            auth.create_user("admin", "password-lunga-3")

    def test_short_password_is_rejected(self):
        with self.assertRaises(ValueError):
            auth.create_user("pippo", "corta")

    def test_pending_user_cannot_login(self):
        auth.create_user("maria", "password-lunga-2", status=auth.STATUS_PENDING)
        self.assertIsNone(auth.user_for_telegram_chat(""))  # nessun collegamento
        user = auth.get_user_by_username("maria")
        self.assertEqual(user["status"], auth.STATUS_PENDING)

    def test_admin_can_approve(self):
        auth.create_user("maria", "password-lunga-2", status=auth.STATUS_PENDING)
        maria = auth.get_user_by_username("maria")
        updated = auth.set_user_status(int(maria["id"]), auth.STATUS_ACTIVE)
        self.assertEqual(updated["status"], auth.STATUS_ACTIVE)

    def test_disabling_revokes_sessions(self):
        user = self.make_admin()
        token, _ = auth.create_session(int(user["id"]))
        self.assertIsNotNone(auth.resolve_session(token))
        auth.set_user_status(int(user["id"]), auth.STATUS_DISABLED)
        self.assertIsNone(auth.resolve_session(token))

    def test_public_user_hides_password(self):
        user = self.make_admin()
        public = auth.public_user(user)
        self.assertNotIn("password_hash", public)
        self.assertNotIn("password", str(public))


class SessionTest(AuthTestCase):
    def test_session_resolves_user(self):
        user = self.make_admin()
        token, session = auth.create_session(int(user["id"]))
        self.assertEqual(session["user_id"], int(user["id"]))
        resolved = auth.resolve_session(token)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["username"], "admin")

    def test_invalid_token_returns_none(self):
        self.assertIsNone(auth.resolve_session("token-inventato"))
        self.assertIsNone(auth.resolve_session(""))

    def test_revoked_session_is_rejected(self):
        user = self.make_admin()
        token, _ = auth.create_session(int(user["id"]))
        self.assertTrue(auth.revoke_session(token))
        self.assertIsNone(auth.resolve_session(token))

    def test_expired_session_is_rejected(self):
        user = self.make_admin()
        token, _ = auth.create_session(int(user["id"]))
        with db.db() as conn:
            conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                ("2000-01-01T00:00:00+00:00", auth._token_hash(token)),
            )
        self.assertIsNone(auth.resolve_session(token))

    def test_token_is_not_stored_in_plaintext(self):
        user = self.make_admin()
        token, _ = auth.create_session(int(user["id"]))
        with db.db() as conn:
            rows = [r["token_hash"] for r in conn.execute("SELECT token_hash FROM sessions")]
        self.assertNotIn(token, rows)
        for row in rows:
            self.assertNotEqual(row, token)


class DataSeparationTest(AuthTestCase):
    """Ogni utente vede solo le proprie ricerche, offerte e avvisi."""

    def test_watches_are_filtered_by_user(self):
        admin = self.make_admin()
        mario = self.make_member()
        store.create_watch(make_watch("Thiene", "Trento"), user_id=int(admin["id"]))
        store.create_watch(make_watch("Verona", "Bologna"), user_id=int(mario["id"]))

        self.assertEqual(len(store.list_watches(int(admin["id"]))), 1)
        self.assertEqual(len(store.list_watches(int(mario["id"]))), 1)
        self.assertEqual(store.list_watches(int(admin["id"]))[0]["from_place"], "Thiene")
        self.assertEqual(store.list_watches(int(mario["id"]))[0]["from_place"], "Verona")

    def test_get_watch_of_another_user_is_none(self):
        admin = self.make_admin()
        mario = self.make_member()
        watch = store.create_watch(make_watch(), user_id=int(admin["id"]))
        self.assertIsNotNone(store.get_watch(int(watch["id"]), user_id=int(admin["id"])))
        self.assertIsNone(store.get_watch(int(watch["id"]), user_id=int(mario["id"])))

    def test_delete_of_another_user_does_nothing(self):
        admin = self.make_admin()
        mario = self.make_member()
        watch = store.create_watch(make_watch(), user_id=int(admin["id"]))
        self.assertFalse(store.delete_watch(int(watch["id"]), user_id=int(mario["id"])))
        self.assertIsNotNone(store.get_watch(int(watch["id"]), user_id=int(admin["id"])))

    def test_update_of_another_user_does_nothing(self):
        from app.schemas import WatchUpdate

        admin = self.make_admin()
        mario = self.make_member()
        watch = store.create_watch(make_watch(), user_id=int(admin["id"]))
        store.update_watch(
            int(watch["id"]), WatchUpdate(name="cambia"), user_id=int(mario["id"])
        )
        unchanged = store.get_watch(int(watch["id"]), user_id=int(admin["id"]))
        self.assertNotEqual(unchanged["name"], "cambia")

    def test_alerts_are_filtered_by_user(self):
        admin = self.make_admin()
        mario = self.make_member()
        watch_a = store.create_watch(make_watch("Thiene", "Trento"), user_id=int(admin["id"]))
        watch_b = store.create_watch(make_watch("Verona", "Bologna"), user_id=int(mario["id"]))
        with db.db() as conn:
            conn.execute(
                "INSERT INTO alerts(watch_id, kind, channel, title, message, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (watch_a["id"], "digest", "web", "t1", "m1", "2026-01-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO alerts(watch_id, kind, channel, title, message, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (watch_b["id"], "digest", "web", "t2", "m2", "2026-01-01T00:00:00+00:00"),
            )
        self.assertEqual(len(store.list_alerts(user_id=int(admin["id"]))), 1)
        self.assertEqual(len(store.list_alerts(user_id=int(mario["id"]))), 1)
        self.assertEqual(store.list_alerts(user_id=int(admin["id"]))[0]["title"], "t1")

    def test_mark_alerts_read_only_touches_own(self):
        admin = self.make_admin()
        mario = self.make_member()
        watch_a = store.create_watch(make_watch("Thiene", "Trento"), user_id=int(admin["id"]))
        watch_b = store.create_watch(make_watch("Verona", "Bologna"), user_id=int(mario["id"]))
        with db.db() as conn:
            for watch_id in (watch_a["id"], watch_b["id"]):
                conn.execute(
                    "INSERT INTO alerts(watch_id, kind, channel, title, message, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (watch_id, "digest", "web", "t", "m", "2026-01-01T00:00:00+00:00"),
                )
        store.mark_alerts_read(None, user_id=int(admin["id"]))
        self.assertEqual(len(store.list_alerts(unread_only=True, user_id=int(admin["id"]))), 0)
        self.assertEqual(len(store.list_alerts(unread_only=True, user_id=int(mario["id"]))), 1)

    def test_deleting_watch_cascades_to_offers(self):
        admin = self.make_admin()
        watch = store.create_watch(make_watch(), user_id=int(admin["id"]))
        with db.db() as conn:
            conn.execute(
                "INSERT INTO offers(watch_id, fingerprint, source, mode, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?)",
                (watch["id"], "fp1", "flixbus", "bus", "2026-01-01T00:00:00+00:00",
                 "2026-01-01T00:00:00+00:00"),
            )
        store.delete_watch(int(watch["id"]), user_id=int(admin["id"]))
        with db.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) n FROM offers WHERE watch_id = ?", (watch["id"],)
            ).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_legacy_watches_go_to_first_admin(self):
        # Una ricerca senza user_id (creata prima del login) resta orfana...
        with db.db() as conn:
            conn.execute(
                "INSERT INTO watches(from_place, to_place, arrive_by, sources, notify, "
                "created_at) VALUES (?,?,?,?,?,?)",
                ("Thiene", "Trento", "2026-01-01T00:00:00+00:00", "[]", "[]",
                 "2026-01-01T00:00:00+00:00"),
            )
        admin = self.make_admin()
        db.init_db()  # la migrazione assegna le ricerche orfane
        with db.db() as conn:
            row = conn.execute("SELECT user_id FROM watches").fetchone()
        self.assertEqual(row["user_id"], int(admin["id"]))


class UserSettingsTest(AuthTestCase):
    def test_personal_settings_are_stored_per_user(self):
        from app.services import settings as app_settings

        admin = self.make_admin()
        mario = self.make_member()
        app_settings.update_user(int(admin["id"]), {"quiet_from": "23:00", "quiet_to": "07:00"})
        app_settings.update_user(int(mario["id"]), {"quiet_from": "22:00", "quiet_to": "06:00"})

        self.assertEqual(app_settings.get_user(int(admin["id"]), "quiet_from"), "23:00")
        self.assertEqual(app_settings.get_user(int(mario["id"]), "quiet_from"), "22:00")

    def test_global_settings_stay_shared(self):
        from app.services import settings as app_settings

        admin = self.make_admin()
        mario = self.make_member()
        app_settings.update({"ai_model_smart": "modello-comune"})
        self.assertEqual(app_settings.get_user(int(admin["id"]), "ai_model_smart"), "modello-comune")
        self.assertEqual(app_settings.get_user(int(mario["id"]), "ai_model_smart"), "modello-comune")

    def test_secrets_are_masked(self):
        from app.services import settings as app_settings

        admin = self.make_admin()
        app_settings.update_user(int(admin["id"]), {"smtp_password": "segretissima"})
        masked = app_settings.get_user_all(int(admin["id"]), mask=True)
        self.assertEqual(masked["smtp_password"], app_settings.MASK)

    def test_masked_value_is_not_saved(self):
        from app.services import settings as app_settings

        admin = self.make_admin()
        app_settings.update_user(int(admin["id"]), {"smtp_password": app_settings.MASK})
        self.assertEqual(app_settings.get_user(int(admin["id"]), "smtp_password"), "")


class PushPerUserTest(AuthTestCase):
    def test_subscriptions_are_per_user(self):
        from app.services import push

        admin = self.make_admin()
        mario = self.make_member()
        subscription = {
            "endpoint": "https://push.example/a",
            "keys": {"p256dh": "x" * 20, "auth": "y" * 12},
        }
        push.subscribe(subscription, user_id=int(admin["id"]))
        push.subscribe(
            {**subscription, "endpoint": "https://push.example/b"}, user_id=int(mario["id"])
        )
        self.assertEqual(push.count(user_id=int(admin["id"])), 1)
        self.assertEqual(push.count(user_id=int(mario["id"])), 1)
        self.assertEqual(push.count(), 2)

    def test_unsubscribe_only_own_device(self):
        from app.services import push

        admin = self.make_admin()
        mario = self.make_member()
        subscription = {
            "endpoint": "https://push.example/a",
            "keys": {"p256dh": "x" * 20, "auth": "y" * 12},
        }
        push.subscribe(subscription, user_id=int(admin["id"]))
        push.unsubscribe("https://push.example/a", user_id=int(mario["id"]))
        self.assertEqual(push.count(user_id=int(admin["id"])), 1)


class TelegramLinkTest(AuthTestCase):
    def test_chat_is_mapped_to_user(self):
        admin = self.make_admin()
        auth.set_telegram_chat(int(admin["id"]), "123456")
        found = auth.user_for_telegram_chat("123456")
        self.assertIsNotNone(found)
        self.assertEqual(found["username"], "admin")

    def test_pending_user_has_no_chat_mapping(self):
        auth.create_user("maria", "password-lunga-2", status=auth.STATUS_PENDING)
        auth.set_telegram_chat(
            int(auth.get_user_by_username("maria")["id"]), "999"
        )
        self.assertIsNone(auth.user_for_telegram_chat("999"))

    def test_unknown_chat_returns_none(self):
        self.assertIsNone(auth.user_for_telegram_chat("000"))
        self.assertIsNone(auth.user_for_telegram_chat(""))


if __name__ == "__main__":
    unittest.main()
