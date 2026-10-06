"""Fault-list and collapsing tests.

The load-bearing property: for every full fault f and its collapsed
representative r, every test that detects r must also detect f
(signature(r) is a subset of signature(f)).  That is exactly what makes
100% coverage of the collapsed list imply 100% coverage of the full list.
On c17 this is checked EXHAUSTIVELY (all 32 input vectors); on c432/c499/
c880 against large random pattern sets (c499 covers the XOR rules).

The equivalence and dominance tables are additionally validated rule by
rule against simulated signatures.
"""

import sys
import unittest
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atpg.bench import Circuit
from atpg.faults import (
    _DOMINATED,
    _EQUIV,
    collapse_faults,
    expand_fanout_branches,
    full_fault_list,
)
from atpg.simulate import PPSFPFaultSimulator, WordSimulator

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks"


def load_expanded(name):
    ckt = Circuit.from_bench_file(BENCH_DIR / f"{name}.bench").build_graph()
    return expand_fanout_branches(ckt)


def load_plain(name):
    return Circuit.from_bench_file(BENCH_DIR / f"{name}.bench").build_graph()


class TestExpandBranches(unittest.TestCase):
    def test_gate_inputs_are_fanout_one_after_expansion(self):
        for name in ("c17", "c432", "c880"):
            with self.subTest(circuit=name):
                x = load_expanded(name)
                for g in x.gates.values():
                    if g.gtype == "BUFF":
                        continue  # branch buffers may hang off stems
                    for inp in g.inputs:
                        self.assertEqual(
                            len(x.fanout[inp]), 1,
                            f"non-BUFF gate {g.name} input {inp} has fanout "
                            f"{x.fanout[inp]}")

    def test_expansion_preserves_good_machine(self):
        import random
        rng = random.Random(1)
        for name in ("c17", "c432", "c880"):
            with self.subTest(circuit=name):
                orig = load_plain(name)
                x = load_expanded(name)
                pats = [tuple(rng.randrange(2) for _ in orig.inputs)
                        for _ in range(16)]
                w1, _, _ = WordSimulator(orig).simulate_batch(pats)
                w2, _, _ = WordSimulator(x).simulate_batch(pats)
                for po in orig.outputs:
                    self.assertEqual(w1[po], w2[po])

    def test_c17_expansion_counts(self):
        x = load_expanded("c17")
        # 5 PIs + 6 gate outputs + 6 branch BUFFs (nets 3, 11, 16 fan out 2x)
        # = 17 nets; POs 22/23 have no other consumer, so no PO branches
        self.assertEqual(len(x.nets), 17)
        self.assertEqual(len(full_fault_list(x)), 34)


class TestCollapseProperties(unittest.TestCase):
    def _signatures(self, ckt, faults, patterns):
        ppsfp = PPSFPFaultSimulator(ckt, batch=64)
        det = ppsfp.run(patterns, faults)
        return det

    def _check_rep_property(self, ckt, patterns):
        faults = full_fault_list(ckt)
        collapsed, rep = collapse_faults(ckt, faults)
        self.assertTrue(set(collapsed) <= set(faults))
        self.assertEqual(set(rep), set(faults))
        sig = self._signatures(ckt, faults, patterns)
        for f in faults:
            r = rep[f]
            # every test for the representative detects f
            self.assertEqual(
                sig[r] & ~sig[f], 0,
                f"rep {r} detects a pattern that misses covered fault {f}")
        return len(faults), len(collapsed)

    def test_c17_exhaustive(self):
        ckt = load_expanded("c17")
        pats = list(product((0, 1), repeat=len(ckt.inputs)))
        self.assertEqual(len(pats), 32)
        full, collapsed = self._check_rep_property(ckt, pats)
        self.assertEqual(full, 34)
        self.assertLess(collapsed, full)

    def test_c432_random(self):
        import random
        rng = random.Random(7)
        ckt = load_expanded("c432")
        pats = [tuple(rng.randrange(2) for _ in ckt.inputs) for _ in range(256)]
        full, collapsed = self._check_rep_property(ckt, pats)
        self.assertLess(collapsed, full)

    def test_c499_random_xors(self):
        import random
        rng = random.Random(13)
        ckt = load_expanded("c499")
        pats = [tuple(rng.randrange(2) for _ in ckt.inputs) for _ in range(256)]
        self._check_rep_property(ckt, pats)

    def test_c880_random_mixed_gates(self):
        import random
        rng = random.Random(21)
        ckt = load_expanded("c880")
        pats = [tuple(rng.randrange(2) for _ in ckt.inputs) for _ in range(256)]
        self._check_rep_property(ckt, pats)


class TestCollapseRulesAgainstSignatures(unittest.TestCase):
    """Validate every _EQUIV / _DOMINATED entry against simulated truth."""

    def _sig(self, ckt, site, stuck, patterns):
        ppsfp = PPSFPFaultSimulator(ckt, batch=128)
        return ppsfp.run(patterns, [(site, stuck)])[(site, stuck)]

    def _rule_check(self, name):
        ckt = load_expanded(name)
        import random
        rng = random.Random(99)
        pats = [tuple(rng.randrange(2) for _ in ckt.inputs) for _ in range(128)]
        for g in ckt.gates.values():
            # stem-fed branch BUFFs take the stem fault as their "input",
            # which is not a local pin fault -- rules do not apply there
            if g.gtype == "BUFF" and len(ckt.fanout[g.inputs[0]]) > 1:
                continue
            for in_val, out_val in _EQUIV[g.gtype]:
                s_in = self._sig(ckt, g.inputs[0], in_val, pats)
                s_out = self._sig(ckt, g.name, out_val, pats)
                self.assertEqual(
                    s_in, s_out,
                    f"{name} {g.name} {g.gtype}: in s-a-{in_val} not "
                    f"equivalent to out s-a-{out_val}")
            for in_val, out_val in _DOMINATED[g.gtype]:
                s_in = self._sig(ckt, g.inputs[0], in_val, pats)
                s_out = self._sig(ckt, g.name, out_val, pats)
                self.assertEqual(
                    s_in & ~s_out, 0,
                    f"{name} {g.name} {g.gtype}: tests for in s-a-{in_val} "
                    f"do not all detect out s-a-{out_val}")

    def test_rules_on_c17(self):
        self._rule_check("c17")

    def test_rules_on_c880(self):
        self._rule_check("c880")

    def test_rules_on_c499_xors(self):
        self._rule_check("c499")


if __name__ == "__main__":
    unittest.main()
