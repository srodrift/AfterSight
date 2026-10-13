"""
AFTERSIGHT automated tests (stdlib unittest — no extra installs).

    python -m unittest discover -s tests -v

What's covered (all offline, no API keys):
  * Temporal state engine — the 6 deterministic tests from the app, plus backward compatibility
  * Open-ended retrieval (memory_search.search_events) over captions, objects, colors, places, time
  * Auto Memory change detection (memory_search.meaningful_change) — duplicate suppression
  * Self-hosted HydraDB adapter (graph_store) against a mock that enforces the documented OpenCypher subset
  * Voice helpers (format handling)

Real-service checks are separate: `python graph_store.py --selftest` (your HydraDB server) and
tests/e2e_browser.py (headless browser with fake camera/mic + stubbed model).
"""
import ast
import os
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import memory_search  # noqa: E402
import graph_store  # noqa: E402
import voice  # noqa: E402


def _load_engine():
    """Pull the pure engine + seed data out of app.py without running the Streamlit UI."""
    src = open(os.path.join(ROOT, "app.py")).read()
    tree = ast.parse(src)
    want_fn = {"at", "ob", "fmt", "compute_state", "mem_text"}
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in want_fn) or
            (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("NOW", "SEED") for t in n.targets))]
    ns = {"datetime": datetime, "timedelta": timedelta}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "app_engine", "exec"), ns)
    return ns


E = _load_engine()
SEED, cs, ob, NOW = E["SEED"], E["compute_state"], E["ob"], E["NOW"]


def ev(objs, place="kitchen fridge", hours=1, **extra):
    return dict(id=f"t-{place}-{hours}", ts=(NOW + timedelta(hours=hours)).isoformat(), place=place,
                caption="test", objects=objs, **extra)


class StateEngine(unittest.TestCase):
    def test_latest_observation_wins(self):
        self.assertEqual(cs(SEED)["eggs"]["count"], 2)

    def test_estimate_corrected_by_observation(self):
        self.assertTrue(any("corrected" in n for n in cs(SEED)["eggs"]["notes"]))

    def test_usage_produces_estimate(self):
        s = cs(SEED + [ev([ob("eggs", "used", 2)])])["eggs"]
        self.assertEqual((s["count"], s["basis"]), (0, "estimated"))

    def test_purchase_does_not_change_home_inventory(self):
        self.assertEqual(cs(SEED + [ev([ob("eggs", "purchased", 12)], "store")])["eggs"]["count"], 2)

    def test_keys_latest_place(self):
        self.assertIn("hallway", cs(SEED)["keys"]["place"])

    def test_laptop_last_seen(self):
        self.assertIn("office", cs(SEED)["laptop"]["place"])

    def test_rich_fields_are_ignored_by_engine(self):
        rich = ev([dict(ob("mug"), attributes="yellow", position="in right hand")], "desk",
                  interactions=["holding a mug"])
        self.assertEqual(cs(SEED + [rich])["mug"]["place"], "desk")

    def test_mem_text_backward_compatible(self):
        old = {"id": "x", "ts": NOW.isoformat(), "place": "p", "caption": "c",
               "objects": [{"name": "keys", "event": "observed", "count": 1}]}
        self.assertIn("keys", E["mem_text"](old))
        rich = dict(old, image="i.jpg", scene="s", details=["d"],
                    objects=[{"name": "mug", "event": "observed", "count": 1, "attributes": "yellow"}])
        t = E["mem_text"](rich)
        self.assertIn("yellow", t)
        self.assertIn("[photo retained]", t)


class Retrieval(unittest.TestCase):
    def top(self, q, k=3):
        return [e["id"] for e in memory_search.search_events(q, SEED, k)]

    def test_object_question(self):
        self.assertEqual(self.top("Where did I leave my keys?")[0], "seed-09")

    def test_color_question(self):
        self.assertEqual(self.top("What was the yellow thing on my monitor?")[0], "seed-11")

    def test_place_question(self):
        self.assertEqual(self.top("what did you see in my bedroom")[0], "seed-10")

    def test_text_detail(self):
        self.assertEqual(self.top("when is the dentist")[0], "seed-11")

    def test_recency_intent(self):
        latest = max(SEED, key=lambda e: e["ts"])["id"]
        self.assertEqual(self.top("what was the last thing I saw?")[0], latest)

    def test_bounded_fallback_when_nothing_matches(self):
        hits = memory_search.search_events("zzzz qqqq", SEED, k=8)
        self.assertTrue(0 < len(hits) <= 6)

    def test_agent_actions_excluded(self):
        acts = SEED + [dict(id="a1", ts=NOW.isoformat(), place="AFTERSIGHT agent", caption="approved keys", objects=[])]
        self.assertNotIn("a1", [e["id"] for e in memory_search.search_events("keys", acts, 10)])


class AutoMemoryChangeDetection(unittest.TestCase):
    def setUp(self):
        self.state = cs(SEED)

    def test_duplicate_is_suppressed(self):
        self.assertEqual(memory_search.meaningful_change([{"name": "eggs", "count": 2}], "kitchen fridge", self.state), [])

    def test_place_variant_is_not_a_change(self):
        self.assertEqual(memory_search.meaningful_change([{"name": "eggs", "count": 2}], "fridge", self.state), [])

    def test_count_change_detected(self):
        self.assertEqual(memory_search.meaningful_change([{"name": "eggs", "count": 0}], "kitchen fridge", self.state),
                         ["eggs: 2 → 0"])

    def test_move_detected(self):
        r = memory_search.meaningful_change([{"name": "keys", "count": 1}], "bedroom nightstand", self.state)
        self.assertTrue(r and "moved" in r[0])

    def test_new_object_detected(self):
        self.assertEqual(memory_search.meaningful_change([{"name": "yellow mug"}], "desk", self.state), ["new: yellow mug"])


class SelfHostedHydraDBAdapter(unittest.TestCase):
    """graph_store against a mock enforcing HydraDB's documented OpenCypher subset (tests/mock_hydradb.py)."""
    PORT = 18477

    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_hydradb.py"), str(cls.PORT)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.environ.update(HYDRA_GRAPH_URL=f"http://127.0.0.1:{cls.PORT}",
                          HYDRA_GRAPH_TOKEN="local-development-token-32-bytes")
        for _ in range(50):
            if graph_store.health()[0]:
                break
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        os.environ.pop("HYDRA_GRAPH_URL", None)

    def test_health(self):
        self.assertEqual(graph_store.health(), (True, "connected"))

    def test_write_is_idempotent_and_readable(self):
        e = {"id": "unit-1", "ts": NOW.isoformat(), "place": "desk", "caption": "yellow mug",
             "objects": [{"name": "unit mug", "event": "observed", "count": 1}]}
        graph_store.write_events([e])
        graph_store.write_events([e])
        rows = [r for r in graph_store.recall(["unit mug"]) if r["memory_id"] == "unit-1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 1)

    def test_seed_roundtrip_and_ordering(self):
        graph_store.write_events(SEED)
        rows = graph_store.recall(["eggs"])
        self.assertEqual(rows[0]["quantity"], 2)                 # newest first
        self.assertEqual([r["event"] for r in rows[:2]], ["observed", "used"])

    def test_question_object_matching(self):
        self.assertEqual(graph_store.objects_in_question("where's my key?", ["keys", "laptop"]), ["keys"])

    def test_bad_token_is_reported(self):
        os.environ["HYDRA_GRAPH_TOKEN"] = "wrong"
        try:
            ok, msg = graph_store.health()
            self.assertFalse(ok)
            self.assertIn("401", msg)
        finally:
            os.environ["HYDRA_GRAPH_TOKEN"] = "local-development-token-32-bytes"


class Voice(unittest.TestCase):
    def test_extension_mapping(self):
        self.assertEqual(voice._ext("audio/webm;codecs=opus"), "webm")
        self.assertEqual(voice._ext("audio/mp4"), "mp4")
        self.assertEqual(voice._ext(""), "webm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
