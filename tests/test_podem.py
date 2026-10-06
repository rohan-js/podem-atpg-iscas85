"""PODEM tests.

Anchors:
  * c17 ground truth: exhaustive simulation of all 32 vectors decides,
    for every fault, whether it is testable.  PODEM's verdicts
    (detected/redundant) must match exactly, and every generated vector
    must be re-verified by the independent scalar engine.
  * c432 end-to-end through the ATPG driver with periodic fault
    dropping, engine cross-checks, and static compaction.
"""

import sys
import unittest
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atpg.atpg import run_atpg
from atpg.bench import Circuit
from atpg.faults import (
    collapse_faults,
    expand_fanout_branches,
    full_fault_list,
)
from atpg.podem import PodemEngine
from atpg.scalar_sim import ScalarSimulator
from atpg.simulate import PPSFPFaultSimulator

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks"


def load_expanded(name):
    ckt = Circuit.from_bench_file(BENCH_DIR / f"{name}.bench").build_graph()
    return expand_fanout_branches(ckt)


class TestPodemC17GroundTruth(unittest.TestCase):
    def test_podem_matches_exhaustive_truth(self):
        ckt = load_expanded("c17")
        faults = full_fault_list(ckt)
        collapsed, rep = collapse_faults(ckt, faults)

        # ground truth: exhaustive simulation of all 32 vectors
        ppsfp = PPSFPFaultSimulator(ckt, batch=32)
        pats = list(product((0, 1), repeat=len(ckt.inputs)))
        sig = ppsfp.run(pats, faults)
        testable = {f for f in faults if sig[f]}
        # every collapsed representative must be testable for 100% coverage
        for r in collapsed:
            self.assertIn(r, testable, f"collapsed fault {r} is redundant?!")

        # PODEM verdicts on every collapsed fault + scalar re-verification
        eng = PodemEngine(ckt)
        ss = ScalarSimulator(ckt)
        for f in collapsed:
            vec, status = eng.podem(f[0], f[1], backtrack_limit=10000)
            with self.subTest(fault=f):
                self.assertEqual(status, "detected")
                self.assertIsNotNone(vec)
                self.assertTrue(
                    ss.detects(f[0], f[1], vec),
                    f"scalar engine rejects PODEM vector for {f}")

    def test_podem_proves_redundancy_correctly(self):
        """Verdict sanity on a circuit with a known redundant fault.

        out = AND(a, NOT(a)) is constant 0: the output s-a-0 fault can
        never be activated (redundant), while s-a-1 is detected by every
        vector (good 0 vs faulty 1 at the PO).
        """
        toy = Circuit.from_bench_text("toy", "\n".join([
            "INPUT(1)", "OUTPUT(4)",
            "2 = NOT(1)", "3 = AND(1, 2)", "4 = BUFF(3)",
        ])).build_graph()
        x = expand_fanout_branches(toy)
        eng = PodemEngine(x)
        vec, status = eng.podem("4", 0, backtrack_limit=10000)
        self.assertEqual(status, "redundant")
        self.assertIsNone(vec)
        vec, status = eng.podem("4", 1, backtrack_limit=10000)
        self.assertEqual(status, "detected")
        # the scalar engine agrees with both verdicts
        ss = ScalarSimulator(x)
        for a in (0, 1):
            self.assertFalse(ss.detects("4", 0, (a,)))
            self.assertTrue(ss.detects("4", 1, (a,)))


class TestAtpgDriverC432(unittest.TestCase):
    def test_end_to_end_with_compaction(self):
        ckt = load_expanded("c432")
        faults = full_fault_list(ckt)
        collapsed, rep = collapse_faults(ckt, faults)
        res = run_atpg(ckt, collapsed, backtrack_limit=10000, batch=256,
                       seed=42, drop_every=32)

        # engine cross-checks inside run_atpg passed; verify results
        n_det = len(res["detected"])
        n_red = len(res["redundant"])
        n_abk = len(res["aborted"])
        self.assertEqual(n_det + n_red + n_abk, len(collapsed))
        # c432 contains 4 known random-pattern-resistant redundant faults
        # (undetected by 200k random patterns; both simulators agree).
        # Proving their redundancy needs full search-space exhaustion, so
        # within the default budget they surface as aborted -- allow a
        # small honest abort count, require near-full coverage otherwise.
        self.assertLessEqual(n_abk, 8, "unexpected aborts on c432")
        coverage = n_det / len(collapsed)
        self.assertGreater(coverage, 0.98,
                           f"unexpected low coverage {coverage:.4f}")

        # compacted vectors still achieve the same collapsed coverage
        self.assertLessEqual(res["raw_vector_count"], len(collapsed))
        self.assertLessEqual(len(res["vectors"]), res["raw_vector_count"])
        sim = PPSFPFaultSimulator(ckt, batch=256)
        det_c = sim.run(res["vectors"], collapsed)
        for f in res["detected"]:
            self.assertTrue(det_c[f], f"{f} lost coverage after compaction")

        # scalar-engine spot check of 25 random generated vectors
        import random
        rng = random.Random(3)
        ss = ScalarSimulator(ckt)
        by_fault = {}
        for f, mask in det_c.items():
            if mask:
                i = (mask & -mask).bit_length() - 1
                by_fault.setdefault(i, []).append(f)
        for vec_idx in rng.sample(sorted(by_fault), min(25, len(by_fault))):
            vec = res["vectors"][vec_idx]
            for f in by_fault[vec_idx][:3]:
                self.assertTrue(ss.detects(f[0], f[1], vec),
                                f"scalar rejects vector for {f}")

        # full-list coverage via the representative mapping: the property
        # guarantees a COUNT ordering (every detected representative covers
        # at least its own class), not a percentage ordering
        full = len(faults)
        det_full = sum(1 for f in faults if det_c[rep[f]])
        self.assertGreaterEqual(det_full, n_det)
        self.assertGreaterEqual(det_full / full, 0.96)


if __name__ == "__main__":
    unittest.main()
