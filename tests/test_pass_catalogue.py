"""Il catalogo degli abbonamenti: completo, con le condizioni vere."""

from __future__ import annotations

import unittest

from app.services import passes as passes_service


class CatalogueTests(unittest.TestCase):
    def test_the_catalogue_is_wide(self):
        # Non un abbonamento generico: decine di tipi reali.
        self.assertGreaterEqual(len(passes_service.PASS_TYPES), 50)

    def test_every_region_has_a_railway_pass(self):
        for regione in (
            "lombardia", "piemonte", "veneto", "emilia", "toscana", "lazio",
            "campania", "calabria", "puglia", "sicilia", "sardegna", "umbria",
            "marche", "abruzzo", "fvg", "trentino", "alto_adige", "valle_aosta",
            "liguria",
        ):
            chiave = f"trenitalia_{regione}"
            self.assertIn(chiave, passes_service.PASS_TYPES, regione)

    def test_the_integrated_passes_are_there(self):
        for chiave in (
            "altoadigemobilita", "euregio_ticket", "trentino_provinciale",
            "guest_card_trentino", "ivol_lombardia", "aur_piemonte",
            "salta_su_emilia", "umbria_vai", "fvg_unico", "toscana_mobilita",
        ):
            self.assertIn(chiave, passes_service.PASS_TYPES, chiave)

    def test_the_city_passes_are_there(self):
        for chiave in (
            "atm_milano", "atac_roma", "gtt_torino", "actv_venezia",
            "tper_bologna", "metrocitta_napoli", "amt_genova", "autolinee_firenze",
            "actr_pisa", "amt_verona", "moovie_padova", "sms_bergamo",
            "atp_palermo", "amt_catania", "amt_cagliari", "asu_foggia",
        ):
            self.assertIn(chiave, passes_service.PASS_TYPES, chiave)

    def test_the_rail_passes_are_there(self):
        for chiave in ("trenitalia_pass", "interrail", "carta_plus_lombardia", "italo_carnet"):
            self.assertIn(chiave, passes_service.PASS_TYPES, chiave)

    def test_altoadigemobilita_covers_the_brenner_line_to_trento(self):
        # Il punto chiave: la linea del Brennero arriva a Trento, quindi
        # l'abbonamento altoadigemobilita copre anche quella tratta.
        dati = passes_service.PASS_TYPES["altoadigemobilita"]
        self.assertIn("trento", dati["area"])
        self.assertIn("Brennero", dati["nota"])

    def test_every_pass_has_a_name_and_a_type(self):
        for chiave, dati in passes_service.PASS_TYPES.items():
            self.assertTrue(dati.get("nome"), chiave)
            self.assertIn(dati.get("tipo"), ("regione", "citta", "ferrovia", "altro"), chiave)
            self.assertTrue(dati.get("copre"), chiave)


class RecognitionTests(unittest.TestCase):
    def test_recognises_the_written_name(self):
        casi = {
            "ho l'abbonamento alto adige mobilita": "altoadigemobilita",
            "ho IVOL": "ivol_lombardia",
            "abbonamento ATAC Roma": "atac_roma",
            "la euregio ticket": "euregio_ticket",
            "abbonamento urbano di Venezia": "actv_venezia",
            "abbonamento regionale del piemonte": "trenitalia_piemonte",
        }
        for testo, atteso in casi.items():
            self.assertEqual(passes_service.trova_per_testo(testo), atteso, testo)

    def test_does_not_match_a_generic_word_inside_another(self):
        # "urbano" dentro "abbonamento urbano di Venezia" non deve far
        # vincere il pass generico.
        self.assertNotEqual(
            passes_service.trova_per_testo("abbonamento urbano di Venezia"), "urbano"
        )

    def test_unknown_text(self):
        self.assertIsNone(passes_service.trova_per_testo("il mio gatto si chiama pingu"))


if __name__ == "__main__":
    unittest.main()
