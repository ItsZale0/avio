"""Un database di prova, per i test che leggono o scrivono dati.

Prima alcuni test scrivevano sul database di sviluppo e si appoggiavano
all'utente con id 3 che stava li' per caso: su un PC pulito (o senza quei
dati) fallivano. Ora ogni test ha il suo database, in una cartella
temporanea, e non tocca i dati veri.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

# L'utente che i test si aspettano di trovare nel database.
UTENTE_DI_PROVA = 3


def prepara_db_di_prova(test: unittest.TestCase) -> None:
    """Da chiamare in setUp(): database vuoto con un utente attivo."""
    from app import db

    cartella = tempfile.TemporaryDirectory()
    percorso_precedente = db.DB_PATH
    db.DB_PATH = Path(cartella.name) / "prova.db"
    db.init_db()
    with db.db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users(id, username, password_hash, is_admin, "
            "status, created_at) VALUES(?, 'test', 'x', 1, 'active', "
            "'2026-01-01T00:00:00')",
            (UTENTE_DI_PROVA,),
        )

    def ripristina():
        db.DB_PATH = percorso_precedente
        cartella.cleanup()

    test.addCleanup(ripristina)


class BaseDbTest(unittest.TestCase):
    """Un TestCase che ha gia' un database di prova pronto."""

    def setUp(self):
        prepara_db_di_prova(self)
