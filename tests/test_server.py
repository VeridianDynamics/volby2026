import copy
import tempfile
import unittest
from pathlib import Path

from server import Store, normalize, mandates


def municipal():
    return dict(generovano="2026-10-10T18:28:02", prehled=[55, 1, 353, 41, 11.61, 40652, 11497, 28.28, 0, 11472, 10965, 95.58, 562708],
                vysledky=[[2, "Lidovci a Starostové", 133061, 23.65, 55]])


class AdapterTests(unittest.TestCase):
    def test_municipal_votes_are_not_ballots_or_candidate_counts(self):
        r = normalize(municipal(), "municipal")["rounds"][0]
        self.assertEqual((r["votes"], r["counted"], r["total"], r["turnout"]), (562708, 41, 353, 28.28))
        self.assertEqual(r["entries"][0]["share"], 23.65)

    def test_waiting_has_no_turnout(self):
        raw = municipal()
        raw["prehled"] = [55, 1, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        raw["vysledky"] = []
        r = normalize(raw, "municipal")["rounds"][0]
        self.assertIsNone(r["turnout"])
        self.assertEqual(r["entries"], [])

    def test_senate_rounds_do_not_mix(self):
        raw = dict(generovano="2026-10-10T18:15:01",
                   prehled=[[1, 123, 8, 6.5, 5428, 2030, 37.4, 1, 2000, 1947, 97.35],
                            [2, 123, 123, 100, 50000, 20000, 40, 0, 20000, 19000, 95]],
                   vysledky=[[1, "Candidate", "Coalition", "Party", "BEZPP", 762, 39.13, 11000, 57.89, True, True]])
        first, second = normalize(raw, "senate")["rounds"]
        self.assertEqual(first["entries"][0]["votes"], 762)
        self.assertEqual(second["entries"][0]["votes"], 11000)
        self.assertEqual(second["entries"][0]["share"], 57.89)
        self.assertEqual(first["votes"], 1947)


class PersistenceTests(unittest.TestCase):
    def test_brno_mandates_backfilled_without_new_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            store = Store(path)
            raw = council([4000, 3000, 2000, 1000], seats=55)
            raw["param"]["kodzastup"] = 582786
            store.save(dict(id="brno", kind="municipal"), raw)
            with store.connect() as db:
                db.execute("UPDATE snapshots SET normalized=json_remove(normalized, '$.mandates')")
            original = store.export("brno")
            store = Store(path)
            history = store.dashboard(60, 0)["sources"][1]["history"]
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["mandates"]["total"], 55)
            self.assertEqual(history[0]["mandates"]["majority"], 28)
            self.assertEqual(sum(p["minimum"] for p in history[0]["mandates"]["parties"]), 55)
            self.assertEqual(store.export("brno"), original)

    def test_old_jundrov_city_votes_are_archived_without_mixing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            store = Store(path)
            old = municipal()
            old["param"] = dict(kodzastup=582786, obec_mc=551171)
            store.save(dict(id="jundrov", kind="municipal"), old)
            store = Store(path)
            self.assertEqual(store.export("jundrov"), [])
            self.assertEqual(store.export("jundrov-city-votes")[0]["data"], old)
            new = council([4000, 3000, 2000, 1000])
            store.save(dict(id="jundrov", kind="municipal"), new)
            store = Store(path)
            self.assertEqual(len(store.export("jundrov")), 1)
            self.assertEqual(len(store.export("jundrov-city-votes")), 1)
            self.assertEqual(store.dashboard(60, 0)["sources"][0]["history"][0]["mandates"]["total"], 15)

    def test_dedup_restart_correction_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            source = dict(id="brno", kind="municipal")
            store = Store(path)
            raw = municipal()
            store.save(source, raw)
            store.save(source, raw)
            self.assertEqual(len(store.export("brno")), 1)
            store = Store(path)
            changed = copy.deepcopy(raw)
            changed["vysledky"][0][2] -= 1
            store.save(source, changed)
            store.save(source, raw)  # A reversion is also a new snapshot.
            store.fail("brno", "offline")
            self.assertEqual(len(store.export("brno")), 3)
            brno = store.dashboard(60, 0)["sources"][1]
            self.assertEqual(brno["status"]["error"], "offline")
            self.assertIsNotNone(brno["status"]["success"])
            store.save(source, raw)
            self.assertIsNone(store.dashboard(60, 0)["sources"][1]["status"]["error"])
            with self.assertRaises((KeyError, ValueError)):
                store.save(source, {"unexpected": "schema"})
            self.assertEqual(len(store.export("brno")), 3)


def council(votes, seats=15, candidates=None):
    candidates = candidates or [seats] * len(votes)
    return dict(param=dict(kodzastup=551171), generovano="2026-10-10T19:00:00",
                prehled=[seats, 1, 4, 1, 25, 1000, 500, 50, 0, 500, 500, 100, sum(votes)],
                vysledky=[[i+1, f"Party {i+1}", v, v/sum(votes)*100 if sum(votes) else 0, candidates[i]] for i, v in enumerate(votes)])


class MandateTests(unittest.TestCase):
    def test_fifteen_seat_dhondt_allocation(self):
        result = mandates(council([4000, 3000, 2000, 1000]))
        self.assertEqual([p["minimum"] for p in result["parties"]], [6, 5, 3, 1])
        self.assertEqual([p["maximum"] for p in result["parties"]], [6, 5, 3, 1])
        self.assertEqual(result["majority"], 8)
        self.assertFalse(result["tie"])

    def test_short_list_threshold_and_boundary(self):
        result = mandates(council([450, 450, 500, 8600], candidates=[13, 15, 15, 15]))
        self.assertEqual([p["qualified"] for p in result["parties"]], [True, False, True, True])

    def test_candidate_cap(self):
        result = mandates(council([900, 100], candidates=[1, 15]))
        self.assertEqual([p["minimum"] for p in result["parties"]], [1, 14])

    def test_threshold_reduced_to_qualify_two_lists(self):
        result = mandates(council([960, 40]))
        self.assertEqual(result["threshold"], 4)
        self.assertTrue(all(p["qualified"] for p in result["parties"]))

    def test_equal_quotients_favour_more_total_votes(self):
        result = mandates(council([100, 50], seats=2))
        self.assertEqual([p["minimum"] for p in result["parties"]], [2, 0])
        self.assertFalse(result["tie"])

    def test_lottery_at_cutoff_shows_range(self):
        result = mandates(council([100, 100]))
        self.assertTrue(result["tie"])
        self.assertEqual([(p["minimum"], p["maximum"]) for p in result["parties"]], [(7, 8), (7, 8)])

    def test_no_votes_means_waiting(self):
        self.assertEqual(mandates(council([0, 0]))["status"], "waiting")

    def test_official_allocation_supersedes_estimate(self):
        raw = council([100, 100])
        raw["zvoleno"] = True
        raw["vysledky"][0].extend([0, 0, 7])
        raw["vysledky"][1].extend([0, 0, 8])
        result = mandates(raw)
        self.assertEqual(result["status"], "official")
        self.assertEqual([p["minimum"] for p in result["parties"]], [7, 8])


if __name__ == "__main__":
    unittest.main()
