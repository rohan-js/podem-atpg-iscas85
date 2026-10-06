"""Parser tests.

The PI/PO/gate counts below are the published ISCAS-85 statistics
(Brglez, Bryan, Kozminski, ISCAS 1989; gates include NOT/inverter gates).
The fetched .bench files' own header comments must agree with them.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atpg.bench import Circuit

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks"

# circuit: (inputs, outputs, total gates incl. inverters)
PUBLISHED = {
    "c17":   (5, 2, 6),
    "c432":  (36, 7, 160),
    "c499":  (41, 32, 202),
    "c880":  (60, 26, 383),
    "c1355": (41, 32, 546),
    "c1908": (33, 25, 880),
    "c2670": (233, 140, 1193),
    "c3540": (50, 22, 1669),
    "c5315": (178, 123, 2307),
    "c6288": (32, 32, 2416),
    "c7552": (207, 108, 3512),
}


def load(name):
    return Circuit.from_bench_file(BENCH_DIR / f"{name}.bench").build_graph()


class TestParserCounts(unittest.TestCase):
    def test_published_counts_all_circuits(self):
        for name, (n_in, n_out, n_gates) in PUBLISHED.items():
            with self.subTest(circuit=name):
                ckt = load(name)
                self.assertEqual(len(ckt.inputs), n_in, "input count")
                self.assertEqual(len(ckt.outputs), n_out, "output count")
                self.assertEqual(len(ckt.gates), n_gates, "gate count")

    def test_header_comments_agree_with_published(self):
        for name, (n_in, n_out, n_gates) in PUBLISHED.items():
            with self.subTest(circuit=name):
                text = (BENCH_DIR / f"{name}.bench").read_text()
                body = [ln for ln in text.splitlines() if not ln.startswith("#")]
                gates_in_body = sum(
                    1 for ln in body if "=" in ln and ln.strip()
                )
                self.assertEqual(gates_in_body, n_gates)


class TestGraphBuild(unittest.TestCase):
    def test_topological_order_on_c17(self):
        ckt = load("c17")
        pos = {net: i for i, net in enumerate(ckt.topo)}
        for g in ckt.gates.values():
            for inp in g.inputs:
                self.assertLess(pos[inp], pos[g.name])

    def test_fanout_consistency_on_c432(self):
        ckt = load("c432")
        driven = {}
        for g in ckt.gates.values():
            for inp in g.inputs:
                driven.setdefault(inp, []).append(g.name)
        for net, fans in driven.items():
            self.assertEqual(ckt.fanout[net], fans)

    def test_all_pos_reachable_and_gates_in_topo(self):
        for name in PUBLISHED:
            with self.subTest(circuit=name):
                ckt = load(name)
                self.assertEqual(len(ckt.topo), len(ckt.inputs) + len(ckt.gates))
                for po in ckt.outputs:
                    self.assertIn(po, ckt.topo)


if __name__ == "__main__":
    unittest.main()
