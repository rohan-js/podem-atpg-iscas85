"""Simulator tests: gate truth tables, c17 exhaustive ground truth, and
cross-validation of the bit-parallel engine against the scalar engine.
"""

import sys
import unittest
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atpg.bench import Circuit
from atpg.scalar_sim import ScalarSimulator
from atpg.simulate import PPSFPFaultSimulator, WordSimulator

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks"


def load(name):
    return Circuit.from_bench_file(BENCH_DIR / f"{name}.bench").build_graph()


def make_circuit(text, name="toy"):
    return Circuit.from_bench_text(name, text).build_graph()


class TestGateTruthTables(unittest.TestCase):
    def build_and_check(self, gtype, arity, expect):
        """expect: dict input-tuple -> output bit."""
        ins = "\n".join(f"INPUT({i})" for i in range(arity))
        outs = f"\nOUTPUT(99)\n99 = {gtype}({', '.join(str(i) for i in range(arity))})"
        ckt = make_circuit(f"{ins}\n{outs}")
        ws = WordSimulator(ckt)
        ss = ScalarSimulator(ckt)
        for combo, want in expect.items():
            with self.subTest(gate=gtype, inputs=combo):
                words, mask, n = ws.simulate_batch([combo])
                got_word = (words["99"] & mask) != 0
                self.assertEqual(int(got_word), want)
                self.assertEqual(ss.simulate(combo)["99"], want)

    def test_all_gate_types(self):
        self.build_and_check("AND", 2, {(0, 0): 0, (0, 1): 0, (1, 0): 0, (1, 1): 1})
        self.build_and_check("OR", 2, {(0, 0): 0, (0, 1): 1, (1, 0): 1, (1, 1): 1})
        self.build_and_check("NAND", 2, {(0, 0): 1, (0, 1): 1, (1, 0): 1, (1, 1): 0})
        self.build_and_check("NOR", 2, {(0, 0): 1, (0, 1): 0, (1, 0): 0, (1, 1): 0})
        self.build_and_check("XOR", 2, {(0, 0): 0, (0, 1): 1, (1, 0): 1, (1, 1): 0})
        self.build_and_check("XNOR", 2, {(0, 0): 1, (0, 1): 0, (1, 0): 0, (1, 1): 1})
        self.build_and_check("NOT", 1, {(0,): 1, (1,): 0})
        self.build_and_check("BUFF", 1, {(0,): 0, (1,): 1})
        # multi-input fan-in
        self.build_and_check("NAND", 3, {
            (0, 0, 0): 1, (0, 1, 1): 1, (1, 1, 1): 0, (1, 0, 1): 1})
        self.build_and_check("XOR", 3, {
            (0, 0, 0): 0, (1, 0, 0): 1, (1, 1, 0): 0, (1, 1, 1): 1})

    def test_batch_matches_single(self):
        """Bit-parallel batch must equal per-pattern simulation."""
        ckt = load("c432")
        ws = WordSimulator(ckt)
        ss = ScalarSimulator(ckt)
        pats = [tuple((k >> j) & 1 for j in range(len(ckt.inputs)))
                for k in range(64)]
        words, mask, n = ws.simulate_batch(pats)
        for i, pat in enumerate(pats):
            scalar = ss.simulate(pat)
            for net, val in scalar.items():
                self.assertEqual((words[net] >> i) & 1, val)


class TestC17Exhaustive(unittest.TestCase):
    """All 32 input vectors: word engine == scalar engine on every net."""

    def test_word_equals_scalar_everywhere(self):
        ckt = load("c17")
        ws = WordSimulator(ckt)
        ss = ScalarSimulator(ckt)
        pats = list(product((0, 1), repeat=len(ckt.inputs)))
        words, mask, n = ws.simulate_batch(pats)
        self.assertEqual(n, 32)
        for i, pat in enumerate(pats):
            scalar = ss.simulate(pat)
            for net, val in scalar.items():
                self.assertEqual((words[net] >> i) & 1, val)


class TestPPSFPVsScalar(unittest.TestCase):
    """Random patterns on c432: PPSFP detection == scalar detection."""

    def test_detection_agreement(self):
        import random

        rng = random.Random(42)
        ckt = load("c432")
        pats = [tuple(rng.randrange(2) for _ in ckt.inputs) for _ in range(48)]
        sites = list(ckt.gates) + list(ckt.inputs)[:10]
        faults = [(s, v) for s in sites for v in (0, 1)]
        ppsfp = PPSFPFaultSimulator(ckt, batch=16)
        det = ppsfp.run(pats, faults)
        ss = ScalarSimulator(ckt)
        for f in faults:
            site, stuck = f
            for i, pat in enumerate(pats):
                want = ss.detects(site, stuck, pat)
                got = bool((det[f] >> i) & 1)
                self.assertEqual(got, want, f"mismatch on {f} pattern {i}")


class TestPPSFPVsScalarExpanded(unittest.TestCase):
    """Differential check on the branch-expanded netlist.

    The original cross-validation ran on unexpanded circuits, which never
    exercised branch-BUFF fault sites.  Here the engines must agree
    pattern by pattern on the expanded circuit too, including c432's
    known random-pattern-resistant faults (0 detections for both).
    """

    def test_c17_exhaustive_on_expanded(self):
        from itertools import product

        x = load("c17")
        from atpg.faults import expand_fanout_branches

        x = expand_fanout_branches(x)
        pats = list(product((0, 1), repeat=len(x.inputs)))
        faults = [(n, v) for n in x.nets for v in (0, 1)]
        det = PPSFPFaultSimulator(x, batch=32).run(pats, faults)
        ss = ScalarSimulator(x)
        for f in faults:
            for i, pat in enumerate(pats):
                self.assertEqual(
                    bool((det[f] >> i) & 1), ss.detects(f[0], f[1], pat),
                    f"mismatch on {f} pattern {i}")

    def test_c432_branch_faults_sampled(self):
        import random

        from atpg.faults import expand_fanout_branches

        rng = random.Random(11)
        ckt = load("c432")
        x = expand_fanout_branches(ckt)
        pats = [tuple(rng.randrange(2) for _ in x.inputs) for _ in range(128)]
        branches = [(n, v) for n in x.nets for v in (0, 1) if "~" in n]
        hard = [("393~429.1", 1), ("102~259.1", 0)]
        faults = hard + rng.sample(branches, 8)
        det = PPSFPFaultSimulator(x, batch=128).run(pats, faults)
        ss = ScalarSimulator(x)
        for f in faults:
            for i, pat in enumerate(pats):
                self.assertEqual(
                    bool((det[f] >> i) & 1), ss.detects(f[0], f[1], pat),
                    f"mismatch on {f} pattern {i}")


if __name__ == "__main__":
    unittest.main()
