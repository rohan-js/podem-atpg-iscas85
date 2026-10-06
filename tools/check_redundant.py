#!/usr/bin/env python3
"""Falsify PODEM 'redundant' verdicts with random patterns.

A redundant verdict means PODEM exhausted its decision space, so NO
vector can detect the fault.  Any random-pattern detection therefore
proves a search bug.  Run per circuit:

    python tools/check_redundant.py c1355 [pattern_count]
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from atpg.bench import Circuit  # noqa: E402
from atpg.faults import collapse_faults, expand_fanout_branches, full_fault_list  # noqa: E402
from atpg.podem import PodemEngine  # noqa: E402
from atpg.simulate import PPSFPFaultSimulator  # noqa: E402


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "c432"
    n_pats = int(sys.argv[2]) if len(sys.argv) > 2 else 2048

    ckt = Circuit.from_bench_file(REPO / "benchmarks" / f"{name}.bench").build_graph()
    x = expand_fanout_branches(ckt)
    faults = full_fault_list(x)
    collapsed, rep = collapse_faults(x, faults)

    eng = PodemEngine(x)
    rng = random.Random(42)
    pats = [tuple(rng.randrange(2) for _ in x.inputs) for _ in range(n_pats)]

    redundant = []
    for f in collapsed:
        vec, status = eng.podem(f[0], f[1], backtrack_limit=10000)
        if status == "redundant":
            redundant.append(f)
    print(f"PODEM claims redundant: {len(redundant)}/{len(collapsed)}", flush=True)
    if not redundant:
        print("nothing to falsify")
        return

    sim = PPSFPFaultSimulator(x, batch=1024)
    det = sim.run(pats, redundant)
    falsified = [f for f in redundant if det[f]]
    print(f"falsified by {n_pats} random patterns: {len(falsified)}")
    for f in falsified[:10]:
        print("  FALSIFIED:", f)
    if not falsified:
        print("sample redundant claims:", redundant[:10])


if __name__ == "__main__":
    main()
