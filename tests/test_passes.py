"""Test degli abbonamenti: il viaggio gia' pagato.

Un abbonamento regionale copre i regionali ma non le Frecce; se e' scaduto
non copre niente; la tratta coperta costa 0 euro ma il prezzo del biglietto
resta visibile.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.ai import intelligence
from app.ai.planner import build_candidates
from app.services import passes as passes_service

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
ARRIVE = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def make_ctx(prefs=None, max_price=None):
    return SearchContext(
        watch_id=1,
        from_place="Milano Centrale",
        to_place="Bologna Centrale",
        from_lat=45.4863,
        from_lon=9.2044,
        to_lat=44.5054,
        to_lon=11.3426,
        depart_after=DEPART,
        arrive_by=ARRIVE,
        radius_km=30,
        passengers=1,
        max_price=max_price,
        prefs=prefs or {},
    )


def make_train(price=20.0, treni="Regionale", external_id="t1", depart=DEPART):
    return Offer(
        source="trenitalia",
        external_id=external_id,
        mode="train",
        operator="Regionale",
        depart_at=depart,
        arrive_at=depart + timedelta(hours=4),
        price=price,
        pickup_name="Milano Centrale",
        dropoff_name="Bologna Centrale",
        pickup_lat=45.4863,
        pickup_lon=9.2044,
        dropoff_lat=44.5054,
        dropoff_lon=11.3426,
        duration_min=240,
        # Le tappe contano: il treno esce dalla Lombardia a Piacenza, e senza
        # quella tappa la copertura viene stimata sul segmento intero.
        details={
            "cambi": 0,
            "treni": treni,
            "tappe": [{"da": "Milano Centrale", "a": "Piacenza Centrale"}],
        },
    )


def make_bus(price=15.0, external_id="b1"):
    return Offer(
        source="flixbus",
        external_id=external_id,
        mode="bus",
        operator="FlixBus",
        depart_at=DEPART,
        arrive_at=DEPART + timedelta(hours=3),
        price=price,
        pickup_name="Milano",
        dropoff_name="Bologna",
        pickup_lat=45.4863,
        pickup_lon=9.2044,
        dropoff_lat=44.5054,
        dropoff_lon=11.3426,
        duration_min=180,
        details={"cambi": 0, "tappe": []},
    )


class NormalizeTests(unittest.TestCase):
    def test_string_list_is_accepted(self):
        result = passes_service.normalize_passes(["trenitalia_regionale"])
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["nome"], "Abbonamento regionale Trenitalia (quello di casa)"
        )

    def test_unknown_types_are_dropped(self):
        self.assertEqual(passes_service.normalize_passes(["inesistente"]), [])

    def test_json_string_is_parsed(self):
        result = passes_service.normalize_passes('[{"tipo": "urbano"}]')
        self.assertEqual(len(result), 1)

    def test_expiry_is_parsed(self):
        result = passes_service.normalize_passes(
            [{"tipo": "urbano", "valido_alla": "31/12/2026"}]
        )
        self.assertEqual(result[0]["valido_alla"], date(2026, 12, 31))


class CoverageTests(unittest.TestCase):
    def test_regional_pass_covers_a_regional_train(self):
        covered = passes_service.pass_covers(
            {"tipo": "trenitalia_regionale"}, make_train(), date(2026, 10, 22)
        )
        self.assertTrue(covered)

    def test_regional_pass_does_not_cover_a_frecciarossa(self):
        covered = passes_service.pass_covers(
            {"tipo": "trenitalia_regionale"},
            make_train(treni="Frecciarossa"),
            date(2026, 10, 22),
        )
        self.assertFalse(covered)

    def test_full_pass_covers_the_frecciarossa(self):
        covered = passes_service.pass_covers(
            {"tipo": "trenitalia_tutti"},
            make_train(treni="Frecciarossa"),
            date(2026, 10, 22),
        )
        self.assertTrue(covered)

    def test_train_pass_does_not_cover_the_bus(self):
        covered = passes_service.pass_covers(
            {"tipo": "trenitalia_tutti"}, make_bus(), date(2026, 10, 22)
        )
        self.assertFalse(covered)

    def test_expired_pass_covers_nothing(self):
        covered = passes_service.pass_covers(
            {"tipo": "trenitalia_regionale", "valido_alla": "2026-10-01"},
            make_train(),
            date(2026, 10, 22),
        )
        self.assertFalse(covered)


class ApplyTests(unittest.TestCase):
    def test_fully_covered_offer_has_zero_net_cost(self):
        offer = make_train(price=22.4)
        passes_service.apply_passes([{"tipo": "trenitalia_tutti"}], offer)
        self.assertEqual(offer.net_cost, 0.0)
        # Il prezzo reale resta: sono due cose diverse.
        self.assertEqual(offer.price, 22.4)
        self.assertIn("pass", offer.details)

    def test_not_covered_offer_keeps_the_price(self):
        offer = make_train(price=22.4, treni="Frecciarossa")
        passes_service.apply_passes([{"tipo": "trenitalia_regionale"}], offer)
        self.assertIsNone(offer.net_cost)

    def test_a_regional_pass_covers_only_its_own_region(self):
        # Un abbonamento regionale copre la parte di viaggio nella sua
        # regione: il resto si paga.
        offer = make_train(price=20.0)
        passes_service.apply_passes([{"tipo": "trenitalia_lombardia"}], offer)
        self.assertIsNotNone(offer.net_cost)
        self.assertLess(offer.net_cost, 20.0)
        self.assertGreater(offer.net_cost, 0.0)
        self.assertIn("pass_quota", offer.details)


class PlannerTests(unittest.TestCase):
    def test_covered_trip_wins_over_the_cheaper_paid_one(self):
        regional = make_train(price=22.4, external_id="reg")
        bus = make_bus(price=9.9, external_id="bus")
        passes = [{"tipo": "trenitalia_tutti", "nome": "Abbonamento Trenitalia (tutti)"}]
        for offer in (regional, bus):
            passes_service.apply_passes(passes, offer)

        candidates = build_candidates([regional, bus], make_ctx(max_price=15))
        self.assertTrue(candidates)
        best = candidates[0]
        self.assertEqual(best.legs[0].external_id, "reg")
        self.assertEqual(best.net_total, 0.0)
        self.assertTrue(best.covered)
        self.assertEqual(best.effective_cost, 0.0)

    def test_a_partly_covered_trip_still_beats_a_full_price_one(self):
        # Copre meta': il treno resta piu' conveniente del bus, ma non e' gratis.
        regional = make_train(price=20.0, external_id="reg")
        bus = make_bus(price=15.0, external_id="bus")
        for offer in (regional, bus):
            passes_service.apply_passes([{"tipo": "trenitalia_lombardia"}], offer)

        candidates = build_candidates([regional, bus], make_ctx(max_price=15))
        self.assertTrue(candidates)
        best = candidates[0]
        self.assertEqual(best.legs[0].external_id, "reg")
        self.assertLess(best.net_total, 20.0)
        self.assertTrue(best.covered)

    def test_without_passes_the_cheapest_wins(self):
        regional = make_train(price=22.4, external_id="reg")
        bus = make_bus(price=9.9, external_id="bus")
        candidates = build_candidates([regional, bus], make_ctx(max_price=15))
        self.assertEqual(candidates[0].legs[0].external_id, "bus")
        self.assertFalse(candidates[0].covered)


class QuestionTests(unittest.TestCase):
    def test_pass_question_is_proposed(self):
        questions = intelligence._pass_question({})
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["key"], "abbonamenti")
        ids = {o["id"] for o in questions[0]["options"]}
        self.assertIn("trenitalia_regionale", ids)
        self.assertIn("none", ids)

    def test_no_question_if_passes_are_known(self):
        questions = intelligence._pass_question(
            {"passes": [{"tipo": "urbano"}]}
        )
        self.assertEqual(questions, [])

    def test_answer_adds_the_pass_to_the_preferences(self):
        from app.services import questions as questions_service

        watch = {"id": 1, "prefs": {}}
        merged = questions_service.merge_prefs(
            watch, {"add_pass": "trenitalia_regionale"}
        )
        self.assertEqual(len(merged["prefs"]["passes"]), 1)
        self.assertEqual(merged["prefs"]["passes"][0]["tipo"], "trenitalia_regionale")

    def test_answer_can_clear_the_passes(self):
        from app.services import questions as questions_service

        watch = {"id": 1, "prefs": {"passes": [{"tipo": "urbano"}]}}
        merged = questions_service.merge_prefs(watch, {"clear_passes": True})
        self.assertEqual(merged["prefs"]["passes"], [])

    def test_free_text_is_understood(self):
        import asyncio

        result = asyncio.run(
            intelligence.interpret_answer(
                {"key": "abbonamenti", "text": "Hai un abbonamento?", "options": []},
                "ho l'abbonamento annuale Trenitalia",
            )
        )
        self.assertEqual(result["prefs"]["add_pass"], "trenitalia_tutti")


if __name__ == "__main__":
    unittest.main()
