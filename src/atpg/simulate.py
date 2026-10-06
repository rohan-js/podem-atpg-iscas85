"""Bit-parallel (parallel-pattern) logic and fault simulation.

Every signal value is a Python int whose bit i holds the value (0/1) that
the signal takes for pattern i of the current batch.  Gate evaluation is
one big-int bitwise operation, which runs at C speed, so a batch of up to
`batch` patterns is simulated as cheaply as a single pattern.

This module is the project's fast engine.  `scalar_sim.py` is a fully
independent naive engine kept for cross-validation in the test suite.
"""

from __future__ import annotations

from collections import deque

from .bench import Circuit


def eval_gate_word(gtype, words, mask):
    """Evaluate one gate over a batch of patterns (big-int words)."""
    if gtype == "NOT":
        return ~words[0] & mask
    if gtype == "BUFF":
        return words[0]
    if gtype == "AND":
        acc = words[0]
        for w in words[1:]:
            acc &= w
        return acc
    if gtype == "NAND":
        acc = words[0]
        for w in words[1:]:
            acc &= w
        return ~acc & mask
    if gtype == "OR":
        acc = words[0]
        for w in words[1:]:
            acc |= w
        return acc
    if gtype == "NOR":
        acc = words[0]
        for w in words[1:]:
            acc |= w
        return ~acc & mask
    if gtype == "XOR":
        acc = words[0]
        for w in words[1:]:
            acc ^= w
        return acc
    if gtype == "XNOR":
        acc = words[0]
        for w in words[1:]:
            acc ^= w
        return ~acc & mask
    raise ValueError(f"unknown gate type {gtype}")


class WordSimulator:
    """Good-machine, bit-parallel simulation of a combinational circuit."""

    def __init__(self, circuit: Circuit):
        self.c = circuit
        if not hasattr(circuit, "topo"):
            circuit.build_graph()

    def simulate_batch(self, patterns):
        """Simulate `patterns` (list of PI-value tuples in circuit order).

        Returns (words, mask, n) where words maps net -> big-int value word.
        """
        n = len(patterns)
        if n == 0:
            raise ValueError("empty pattern batch")
        mask = (1 << n) - 1
        words = {}
        for idx, pi in enumerate(self.c.inputs):
            w = 0
            for i, pat in enumerate(patterns):
                w |= (pat[idx] & 1) << i
            words[pi] = w
        for gname in self.c.topo:
            if gname in words:
                continue  # PI
            g = self.c.gates[gname]
            ins = [words[i] for i in g.inputs]
            words[gname] = eval_gate_word(g.gtype, ins, mask)
        return words, mask, n


class PPSFPFaultSimulator:
    """Parallel-pattern single-fault-propagation stuck-at fault simulation.

    For each fault and batch: inject the stuck-at value at the fault site
    and re-evaluate only the gates in its transitive fanout cone; a fault
    is detected by a pattern when any primary output differs from the
    good machine.
    """

    def __init__(self, circuit: Circuit, batch: int = 1024):
        self.c = circuit
        if not hasattr(circuit, "topo"):
            circuit.build_graph()
        self.batch = batch
        self._pos = {net: i for i, net in enumerate(circuit.topo)}
        self._ws = WordSimulator(circuit)

    def _cone(self, site):
        """Gates in the transitive fanout of `site`, in topo order."""
        seen = set()
        stack = list(self.c.fanout.get(site, ()))
        while stack:
            g = stack.pop()
            if g in seen:
                continue
            seen.add(g)
            for fan in self.c.fanout.get(g, ()):
                if fan not in seen:
                    stack.append(fan)
        return seen

    def simulate_fault(self, site, stuck, good_words, mask, n):
        """Detection word for one fault over the current batch."""
        fw = dict(good_words)
        fw[site] = mask if stuck else 0
        cone = self._cone(site)
        start = self._pos[site]
        topo = self.c.topo
        for gname in topo[start + 1:]:
            if gname not in cone:
                continue
            g = self.c.gates[gname]
            ins = [fw[i] for i in g.inputs]
            fw[gname] = eval_gate_word(g.gtype, ins, mask)
        det = 0
        for po in self.c.outputs:
            det |= fw[po] ^ good_words[po]
        return det

    def run(self, patterns, faults):
        """Simulate `faults` (list of (site, stuck)) against all patterns.

        Returns dict fault -> big-int bitmask (bit i set <=> detected by
        pattern i).  Faults are tuples so they can be dict keys.
        """
        detection = {f: 0 for f in faults}
        for start in range(0, len(patterns), self.batch):
            chunk = patterns[start:start + self.batch]
            good, mask, n = self.simulate_good(chunk)
            base = start
            for f in faults:
                site, stuck = f
                det = self.simulate_fault(site, stuck, good, mask, n)
                if det:
                    detection[f] |= det << base
        return detection

    # reuse good-machine code
    def simulate_good(self, chunk):
        return self._ws.simulate_batch(chunk)
