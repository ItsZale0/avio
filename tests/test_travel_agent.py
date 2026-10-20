"""Test del ragionamento da travel agent: tempo, cambi, priorita'.

Non chiamano la rete: verificano il punteggio, le domande di priorita' e
la scelta delle alternative.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.adapters.base import Offer, SearchContext
from app.ai import intelligence
from app.ai.planner import Candidate
from app.scoring import score_offers

ROME = timezone(timedelta(hours=2))
DEPART = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
ARRIVE = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)


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


def make_offer(
    *,
    source="trenitalia",
    external_id="x",
    price=20.0,
    duration_min=180,
    changes=0,
    depart=DEPART,
    arrive=ARRIVE,
):
    return Offer(
        source=source,
        external_id=external_id,
        mode="train",
        operator="Treno",
        depart_at=depart,
        arrive_at=arrive,
        price=price,
        pickup_name="Milano Centrale",
        dropoff_name="Bologna Centrale",
        pickup_lat=45.4863,
        pickup_lon=9.2044,
        dropoff_lat=44.5054,
        dropoff_lon=11.3426,
        duration_min=duration_min,
        details={"cambi": changes, "tappe": []},
    )


class DurationScoringTests(unittest.TestCase):
    def test_slower_offer_scores_lower(self):
        fast = make_offer(external_id="fast", price=20.0, duration_min=120)
        slow = make_offer(external_id="slow", price=20.0, duration_min=420)
        ranked = score_offers([fast, slow], make_ctx())
        self.assertGreater(
            ranked[fast.fingerprint]["score"], ranked[slow.fingerprint]["score"]
        )

    def test_fastest_badge_on_the_shortest_journey(self):
        fast = make_offer(external_id="fast", price=30.0, duration_min=120)
        slow = make_offer(external_id="slow", price=20.0, duration_min=420)
        ranked = score_offers([fast, slow], make_ctx())
        self.assertIn("fastest", ranked[fast.fingerprint]["badges"])
        self.assertNotIn("fastest", ranked[slow.fingerprint]["badges"])

    def test_duration_is_in_the_details(self):
        offer = make_offer(duration_min=200, changes=2)
        ranked = score_offers([offer], make_ctx())
        details = ranked[offer.fingerprint]["details"]
        self.assertEqual(details["durata_min"], 200)
        self.assertEqual(details["cambi"], 2)


class ChangesScoringTests(unittest.TestCase):
    def test_direct_beats_many_changes(self):
        direct = make_offer(external_id="direct", price=25.0, changes=0)
        messy = make_offer(external_id="messy", price=25.0, changes=3)
        ranked = score_offers([direct, messy], make_ctx())
        self.assertGreater(
            ranked[direct.fingerprint]["score"], ranked[messy.fingerprint]["score"]
        )

    def test_direct_badge(self):
        direct = make_offer(external_id="direct", changes=0)
        messy = make_offer(external_id="messy", changes=2)
        ranked = score_offers([direct, messy], make_ctx())
        self.assertIn("diretto", ranked[direct.fingerprint]["badges"])
        self.assertNotIn("diretto", ranked[messy.fingerprint]["badges"])


class PreferenceTests(unittest.TestCase):
    def test_prefer_fast_shifts_the_winner(self):
        cheap_slow = make_offer(external_id="cheap", price=10.0, duration_min=400)
        dear_fast = make_offer(external_id="dear", price=18.0, duration_min=110)

        normal = score_offers([cheap_slow, dear_fast], make_ctx())
        self.assertEqual(
            max(normal, key=lambda f: normal[f]["score"]), cheap_slow.fingerprint
        )

        fast = score_offers(
            [cheap_slow, dear_fast], make_ctx(prefs={"prefer_fast": True})
        )
        self.assertEqual(
            max(fast, key=lambda f: fast[f]["score"]), dear_fast.fingerprint
        )


class PriorityQuestionTests(unittest.TestCase):
    def _candidate(self, cid, price, duration):
        leg = make_offer(external_id=cid, price=price, duration_min=duration)
        return Candidate(
            id=cid,
            kind="single",
            legs=[leg],
            total_price=price,
            total_duration_min=duration,
            depart_at=DEPART,
            arrive_at=ARRIVE,
            estimated=False,
        )

    def test_question_appears_with_a_real_tradeoff(self):
        candidates = [
            self._candidate("c1", 12.0, 400),
            self._candidate("c2", 20.0, 120),
        ]
        questions = intelligence._priority_question(candidates)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["key"], "priorita")
        options = {o["id"]: o for o in questions[0]["options"]}
        self.assertFalse(options["economico"]["prefs"]["prefer_fast"])
        self.assertTrue(options["veloce"]["prefs"]["prefer_fast"])

    def test_no_question_without_a_tradeoff(self):
        candidates = [
            self._candidate("c1", 12.0, 400),
            self._candidate("c2", 13.0, 390),
        ]
        self.assertEqual(intelligence._priority_question(candidates), [])

    def test_no_question_with_a_single_candidate(self):
        self.assertEqual(intelligence._priority_question([self._candidate("c1", 12.0, 400)]), [])


class ProfileTests(unittest.TestCase):
    def _candidate(self, cid, price, duration, changes=0):
        leg = make_offer(external_id=cid, price=price, duration_min=duration, changes=changes)
        return Candidate(
            id=cid,
            kind="single",
            legs=[leg],
            total_price=price,
            total_duration_min=duration,
            depart_at=DEPART,
            arrive_at=ARRIVE,
            estimated=False,
        )

    def test_profiles_are_labeled(self):
        cheap = self._candidate("c1", 10.0, 400)
        fast = self._candidate("c2", 20.0, 100)
        easy = self._candidate("c3", 25.0, 200, changes=0)
        candidates = [cheap, fast, easy]
        self.assertEqual(intelligence._profile_of(cheap, candidates), "economico")
        self.assertEqual(intelligence._profile_of(fast, candidates), "veloce")

    def test_alternatives_cover_other_profiles(self):
        cheap = self._candidate("c1", 10.0, 400)
        fast = self._candidate("c2", 20.0, 100)
        chosen = cheap
        picked = intelligence._pick_alternatives([cheap, fast], chosen)
        self.assertIn(fast, picked)


class InterpretationTests(unittest.TestCase):
    def test_priority_answer_maps_to_prefs(self):
        import asyncio

        result = asyncio.run(
            intelligence.interpret_answer(
                {"key": "priorita", "text": "Cosa preferisci?", "options": []},
                "Preferisco arrivare prima",
            )
        )
        self.assertTrue(result["prefs"]["prefer_fast"])

    def test_priority_answer_economico(self):
        import asyncio

        result = asyncio.run(
            intelligence.interpret_answer(
                {"key": "priorita", "text": "Cosa preferisci?", "options": []},
                "meglio spendere meno",
            )
        )
        self.assertFalse(result["prefs"]["prefer_fast"])


if __name__ == "__main__":
    unittest.main()
