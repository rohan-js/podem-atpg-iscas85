"""Naive single-pattern scalar simulator — the independent reference engine.

Deliberately simple and slow: recomputes the whole netlist for every
pattern with pure-Python recursion and no word-level tricks.  Used by the
test suite to cross-validate `simulate.py` (exhaustive c17 ground truth
and random-pattern agreement on larger circuits).
"""

from __future__ import annotations

from .bench import Circuit


def _eval_scalar(gtype, vals):
    if gtype == "NOT":
        return 1 - vals[0]
    if gtype == "BUFF":
        return vals[0]
    if gtype == "AND":
        acc = vals[0]
        for v in vals[1:]:
            acc &= v
        return acc
    if gtype == "NAND":
        acc = vals[0]
        for v in vals[1:]:
            acc &= v
        return 1 - acc
    if gtype == "OR":
        acc = vals[0]
        for v in vals[1:]:
            acc |= v
        return acc
    if gtype == "NOR":
        acc = vals[0]
        for v in vals[1:]:
            acc |= v
        return 1 - acc
    if gtype == "XOR":
        acc = vals[0]
        for v in vals[1:]:
            acc ^= v
        return acc
    if gtype == "XNOR":
        acc = vals[0]
        for v in vals[1:]:
            acc ^= v
        return 1 - acc
    raise ValueError(f"unknown gate type {gtype}")


class ScalarSimulator:
    def __init__(self, circuit: Circuit):
        self.c = circuit
        if not hasattr(circuit, "topo"):
            circuit.build_graph()

    def simulate(self, pattern):
        """One pattern (PI values in circuit order) -> {net: 0/1}."""
        if len(pattern) != len(self.c.inputs):
            raise ValueError("pattern width mismatch")
        vals = dict(zip(self.c.inputs, (int(p) & 1 for p in pattern)))
        for gname in self.c.topo:
            if gname in vals:
                continue
            g = self.c.gates[gname]
            vals[gname] = _eval_scalar(g.gtype, [vals[i] for i in g.inputs])
        return vals

    def detects(self, site, stuck, pattern):
        """True iff the stuck-at fault (site, stuck) is detected by pattern.

        Full independent fault simulation: faulty machine = fresh scalar
        evaluation with the site forced to `stuck`.
        """
        if len(pattern) != len(self.c.inputs):
            raise ValueError("pattern width mismatch")
        good = self.simulate(pattern)
        # faulty machine, computed independently from the good machine:
        # pre-setting the site before the topo walk keeps the stuck value
        # (the loop skips nets that already carry a value)
        fvals = dict(zip(self.c.inputs, (int(p) & 1 for p in pattern)))
        fvals[site] = stuck
        for gname in self.c.topo:
            if gname in fvals:
                continue  # PIs and the fault site itself
            g = self.c.gates[gname]
            fvals[gname] = _eval_scalar(g.gtype, [fvals[i] for i in g.inputs])
        for po in self.c.outputs:
            if good[po] != fvals[po]:
                return True
        return False
