"""A plan sums whatever metrics the provider recorded, per verdict, and counts what is missing."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.graph.graph import contract_violations
from clew.questions.impact import plan_cost


def graph():
    def task(h, **extra):
        return {"hash": h, "name": h, "process": h, "container": "img", "status": "COMPLETED",
                "script": "run", "workdir": f"/w/{h}", **extra}
    return {
        "tasks": {
            "a/1": task("a/1", metrics={"wall_s": 10.0, "kwh": 0.5}),
            "b/2": task("b/2", metrics={"wall_s": 2.5}),
            "c/3": task("c/3"),
        },
        "edges": [], "outputs": {},
    }


def decided(*pairs):
    return [(h, {}, {"action": a, "rule": "R", "because": ""}) for h, a in pairs]


class TestPlanCost(unittest.TestCase):
    def test_sums_each_metric_per_verdict_and_counts_the_gaps(self):
        cost = plan_cost(graph(), decided(("a/1", "REGENERATE"), ("b/2", "REGENERATE"),
                                          ("c/3", "REGENERATE")))
        bucket = cost["by_action"]["REGENERATE"]
        self.assertEqual(bucket["tasks"], 3)
        self.assertEqual(bucket["metrics"], {"wall_s": 12.5, "kwh": 0.5})
        self.assertEqual(bucket["missing"], {"wall_s": 1, "kwh": 2})

    def test_verdicts_are_kept_apart(self):
        cost = plan_cost(graph(), decided(("a/1", "REGENERATE"), ("b/2", "QUARANTINE")))
        self.assertEqual(cost["by_action"]["REGENERATE"]["metrics"], {"wall_s": 10.0, "kwh": 0.5})
        self.assertEqual(cost["by_action"]["QUARANTINE"]["metrics"], {"wall_s": 2.5})

    def test_the_engine_names_no_metric(self):
        import clew.questions.impact as module
        import clew.graph.graph as graph_module
        for word in ("duration_s", "cpus", "price", "memory_gb"):
            self.assertNotIn(word, Path(module.__file__).read_text())
            self.assertNotIn(word, Path(graph_module.__file__).read_text())

    def test_the_contract_accepts_numbers_and_refuses_the_rest(self):
        g = graph()
        self.assertEqual(contract_violations(g), [])
        g["tasks"]["a/1"]["metrics"] = {"wall_s": "10"}
        self.assertTrue(any("metrics" in p for p in contract_violations(g)))
        g["tasks"]["a/1"]["metrics"] = {"wall_s": -1}
        self.assertTrue(any("metrics" in p for p in contract_violations(g)))


if __name__ == "__main__":
    unittest.main()
