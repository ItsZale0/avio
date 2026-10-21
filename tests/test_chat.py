"""La sezione "Chiedi": l'agente risponde e applica le richieste."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from app.ai import chat
from app.services import questions as questions_service

ROME = timezone(timedelta(hours=2))


class BaseDbTest(unittest.TestCase):
    """Un database di prova con un utente, e niente dipendenze dai dati locali.

    Prima questi test scrivevano sul database di sviluppo e si appoggiavano
    all'utente con id 3 che stava li' per caso: su un PC pulito (o senza
    quei dati) fallivano. Ora sono autonomi.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        from app import db

        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "test-chat.db"
        db.init_db()
        with db.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users(id, username, password_hash, is_admin, "
                "status, created_at) VALUES(3, 'test', 'x', 1, 'active', "
                "'2026-01-01T00:00:00')"
            )
        self.addCleanup(self._ripristina)

    def _ripristina(self):
        from app import db

        db.DB_PATH = self._old_path
        self._tmp.cleanup()


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


class MergeTests(BaseDbTest):
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
                merge["changes"].get("depart_after"), "2026-10-22T05:00:00+00:00"
            )
            salvata = store.get_watch(999)
            # In UTC, come tutto il resto: 07:00 in Italia = 05:00 UTC.
            self.assertEqual(salvata.get("depart_after"), "2026-10-22T05:00:00+00:00")
        finally:
            with db.db() as conn:
                conn.execute("DELETE FROM watches WHERE id = 999")


class ChatPersistenceTests(BaseDbTest):
    """La conversazione resta: e' salvata, non solo nella pagina."""

    WATCH_ID = 998

    def setUp(self):
        super().setUp()
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


class WindowWarningTests(BaseDbTest):
    """Partenza dopo l'arrivo: la ricerca resterebbe vuota, meglio dirlo."""

    def setUp(self):
        super().setUp()
        from app import db

        db.init_db()

    def _watch(self, partenza: str, arrivo: str) -> int:
        from app import db

        with db.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO watches(id, user_id, name, from_place, to_place, "
                "arrive_by, depart_after, max_price, radius_km, passengers, sources, notify, "
                "interval_min, active, created_at, prefs) VALUES(997, 3, 'test finestra', "
                "'Thiene', 'Trento', ?, ?, 14, 5, 1, '[]', '[]', 60, 1, "
                "'2026-10-20T10:00:00', '{}')",
                (arrivo, partenza),
            )
        self.addCleanup(self._cleanup)
        return 997

    def _cleanup(self):
        from app import db

        with db.db() as conn:
            conn.execute("DELETE FROM watches WHERE id = 997")

    def test_la_finestra_vuota_viene_segnalata(self):
        self._watch("2026-10-22T10:30:00+02:00", "2026-10-22T09:00:00+02:00")
        avviso = chat._avviso_finestra(997, 3)
        self.assertIn("finestra e' vuota", avviso)
        self.assertIn("10:30", avviso)

    def test_una_finestra_valida_non_produce_avvisi(self):
        self._watch("2026-10-22T07:00:00+02:00", "2026-10-22T11:00:00+02:00")
        self.assertEqual(chat._avviso_finestra(997, 3), "")

    def test_senza_orari_non_si_dice_niente(self):
        self._watch("", "")
        self.assertEqual(chat._avviso_finestra(997, 3), "")


class TimeDisplayTests(unittest.TestCase):
    """L'agente deve leggere l'ora italiana, non l'UTC del database."""

    def test_utc_diventa_ora_italiana(self):
        self.assertEqual(chat._fmt("2026-10-22T09:00:00+00:00"), "2026-10-22 11:00")

    def test_un_orario_senza_fuso_e_gia_locale(self):
        self.assertEqual(chat._fmt("2026-10-22T09:00"), "2026-10-22 09:00")

    def test_vuoto(self):
        self.assertEqual(chat._fmt(None), "-")


class LimitePartenzaTests(BaseDbTest):
    """Il limite di partenza vale al minuto, anche quando il piano e' comodo."""

    def setUp(self):
        super().setUp()

    def test_un_treno_che_parte_prima_del_limite_viene_scartato(self):
        from datetime import datetime, timezone

        from app.adapters.base import Offer, SearchContext
        from app.services.search import _filter_offers

        giorno = datetime.now(timezone.utc) + timedelta(days=2)
        limite = giorno.replace(hour=5, minute=30, second=0, microsecond=0)  # 07:30 Roma
        ctx = SearchContext(
            watch_id=1,
            from_place="Thiene",
            to_place="Trento",
            from_lat=45.70,
            from_lon=11.47,
            to_lat=46.07,
            to_lon=11.12,
            depart_after=limite,
            arrive_by=limite + timedelta(hours=3),
            radius_km=10,
            passengers=1,
            max_price=50,
        )

        def offer(minuti):
            partenza = limite + timedelta(minutes=minuti)
            return Offer(
                source="trenitalia",
                external_id=f"t{minuti}",
                mode="treno",
                operator="Trenitalia",
                depart_at=partenza,
                arrive_at=partenza + timedelta(hours=2),
                price=13.23,
                pickup_name="Thiene",
                pickup_lat=45.700667,
                pickup_lon=11.475408,
                dropoff_name="Trento",
                dropoff_lat=46.072113,
                dropoff_lon=11.119547,
                duration_min=120,
            )

        self.assertEqual(_filter_offers([offer(-9)], ctx), [])  # 07:21
        self.assertEqual(len(_filter_offers([offer(1)], ctx)), 1)  # 07:31
        self.assertEqual(len(_filter_offers([offer(0)], ctx)), 1)  # 07:30

    def _watch_with_plan(self, limite: str = "2026-10-22T07:30") -> int:
        from app import db

        db.init_db()
        with db.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO watches(id, user_id, name, from_place, to_place, "
                "arrive_by, depart_after, max_price, radius_km, passengers, sources, notify, "
                "interval_min, active, created_at, prefs) VALUES(996, 3, 'test limite', "
                "'Thiene', 'Trento', '2026-10-22T09:00:00+00:00', ?, 20, 5, 1, '[]', "
                "'[]', 60, 1, '2026-10-20T10:00:00', '{}')",
                (limite,),
            )
            conn.execute("UPDATE plans SET is_current = 0 WHERE watch_id = 996")
            conn.execute(
                "INSERT INTO plans(watch_id, run_id, kind, title, total_price, steps, "
                "created_at, is_current, depart_at, arrive_at, walk_out_min, walk_in_min) "
                "VALUES(996, NULL, 'cheapest', 'Treno regionale', 13.23, '[]', "
                "'2026-10-20T20:08:21', 1, '2026-10-22T07:21:00+02:00', "
                "'2026-10-22T09:50:00+02:00', 28, 0)"
            )
        self.addCleanup(self._cleanup)
        return 996

    def _cleanup(self):
        from app import db

        with db.db() as conn:
            conn.execute("DELETE FROM plans WHERE watch_id = 996")
            conn.execute("DELETE FROM watches WHERE id = 996")

    def test_il_piano_fuori_limite_viene_segnalato(self):
        self._watch_with_plan()
        avviso = chat._avviso_piano(996, 3)
        self.assertIn("07:21", avviso)
        self.assertIn("06:53", avviso)  # 28 minuti a piedi prima del treno
        self.assertIn("prima del limite", avviso)

    def test_un_piano_nei_limiti_non_produce_avvisi(self):
        self._watch_with_plan(limite="2026-10-22T07:00")
        self.assertEqual(chat._avviso_piano(996, 3), "")

    def test_il_contesto_mostra_il_limite_in_ora_italiana(self):
        self._watch_with_plan()
        contesto = chat._contesto(chat.get_watch(996))
        self.assertEqual(contesto["ricerca"]["partenza_minima"], "2026-10-22 07:30")
        self.assertEqual(contesto["piano"]["uscita_da_casa"], "2026-10-22 06:53")

    def test_senza_piano_il_limite_si_toglie(self):
        from app import db

        self._watch_with_plan()
        with db.db() as conn:
            conn.execute("UPDATE plans SET is_current = 0 WHERE watch_id = 996")
        cambi, nota = chat._allinea_il_limite("vai con quello", 996, 3)
        self.assertIn("depart_after", cambi)
        self.assertIsNone(cambi["depart_after"])
        self.assertIn("tolto il limite", nota)

    def test_accettando_il_piano_il_limite_si_allinea(self):
        self._watch_with_plan()
        cambi, nota = chat._allinea_il_limite("vai con quello", 996, 3)
        self.assertEqual(cambi.get("depart_after"), "2026-10-22T05:21:00+00:00")
        self.assertIn("07:21", nota)  # ora locale, non UTC
        self.assertEqual(chat._avviso_piano(996, 3), "")

    def test_una_domanda_normale_non_tocca_il_limite(self):
        self._watch_with_plan()
        self.assertEqual(chat._allinea_il_limite("quanto costa?", 996, 3), ({}, ""))

    def test_cambiare_un_limite_azzera_subito_il_piano(self):
        """Il piano vecchio non deve restare a schermo mentre si ricalcola."""
        from app.services import questions as questions_service

        self._watch_with_plan()
        self.assertIsNotNone(chat._piano_migliore(996))
        questions_service.merge_prefs(chat.get_watch(996), {"set_depart_after": "2026-10-22T08:00"})
        self.assertIsNone(chat._piano_migliore(996))

    def test_il_limite_appena_messo_avvisa_delle_opzioni_escluse(self):
        from app import db

        self._watch_with_plan()
        with db.db() as conn:
            conn.execute("DELETE FROM offers WHERE watch_id = 996")
            conn.execute(
                "INSERT INTO offers(watch_id, fingerprint, source, mode, operator, depart_at, "
                "arrive_at, price, score, first_seen, last_seen, active) VALUES(996, 'fp', "
                "'trenitalia', 'treno', 'Trenitalia', '2026-10-22T04:21:00+00:00', "
                "'2026-10-22T08:29:00+00:00', 11.9, 10, '2026-10-20T10:00:00', "
                "'2026-10-20T10:00:00', 1)"
            )
        avviso = chat._avviso_limite_nuovo(996, {"depart_after": "2026-10-22T05:30:00+00:00"})
        self.assertIn("06:21", avviso)  # l'offerta che resta fuori, in ora locale
        self.assertIn("07:30", avviso)  # il nuovo limite, in ora locale
        with db.db() as conn:
            conn.execute("DELETE FROM offers WHERE watch_id = 996")


if __name__ == "__main__":
    unittest.main()
