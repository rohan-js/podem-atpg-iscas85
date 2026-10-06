#!/usr/bin/env python3
"""One-command ATPG runner over the ISCAS-85 benchmarks.

    python tools/run_atpg.py --circuits all
    python tools/run_atpg.py --circuits c17,c432 --results-dir results

Per circuit: parse -> expand fanout branches -> full fault list ->
equivalence+dominance collapse -> PODEM ATPG (with periodic fault
dropping) -> reverse-order compaction -> vectors + coverage reports.
Every PODEM vector is re-verified by the independent bit-parallel fault
simulator; inconsistencies abort the run loudly.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from atpg.atpg import run_atpg  # noqa: E402
from atpg.bench import Circuit  # noqa: E402
from atpg.faults import collapse_faults, expand_fanout_branches, full_fault_list  # noqa: E402
from atpg.report import summarize_circuit, write_summary, write_vectors  # noqa: E402

ALL_CIRCUITS = ["c17", "c432", "c499", "c880", "c1355", "c1908",
                "c2670", "c3540", "c5315", "c6288", "c7552"]


def process(name, args, results_dir):
    t0 = time.perf_counter()
    ckt = Circuit.from_bench_file(REPO / "benchmarks" / f"{name}.bench")
    ckt.build_graph()
    expanded = expand_fanout_branches(ckt)
    faults = full_fault_list(expanded)
    t_c0 = time.perf_counter()
    collapsed, rep = collapse_faults(expanded, faults)
    t_collapse = time.perf_counter() - t_c0

    def log(msg):
        print(msg, flush=True)

    log(f"[{name}] {ckt.stats()} | nets+branches {len(expanded.nets)} | "
        f"faults {len(faults)} -> collapsed {len(collapsed)}")
    res = run_atpg(expanded, collapsed, backtrack_limit=args.backtrack_limit,
                   batch=args.batch, seed=args.seed, drop_every=args.drop_every,
                   log=log)
    m = summarize_circuit(ckt, expanded, faults, collapsed, rep, res,
                          t_collapse)
    m["t_parse_s"] = round(time.perf_counter() - t0 - m["t_atpg_s"]
                           - m["t_compact_s"] - t_collapse, 3)
    log(f"[{name}] coverage {m['coverage_collapsed_pct']}% collapsed / "
        f"{m['coverage_full_pct']}% full | {m['detected']} det / "
        f"{m['redundant']} red / {m['aborted']} abk | "
        f"{m['patterns']} patterns | {m['t_total_s']}s")

    write_vectors(expanded, res["vectors"],
                  results_dir / "vectors" / f"{name}.vectors.txt")
    return m, res


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--circuits", default="all",
                    help="comma-separated circuit names or 'all'")
    ap.add_argument("--backtrack-limit", type=int, default=10000)
    ap.add_argument("--batch", type=int, default=1024,
                    help="patterns per parallel-simulation word")
    ap.add_argument("--drop-every", type=int, default=64,
                    help="fault-simulate after this many new vectors")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--results-dir", default=str(REPO / "results"))
    args = ap.parse_args()

    if args.circuits.strip().lower() == "all":
        names = ALL_CIRCUITS
    else:
        names = [c.strip() for c in args.circuits.split(",") if c.strip()]
        for n in names:
            if n not in ALL_CIRCUITS:
                ap.error(f"unknown circuit {n}; choose from {ALL_CIRCUITS}")

    results_dir = Path(args.results_dir)
    (results_dir / "vectors").mkdir(parents=True, exist_ok=True)

    results = []
    for name in names:
        m, _ = process(name, args, results_dir)
        results.append(m)

    totals = write_summary(results, results_dir / "summary.md",
                           results_dir / "summary.json")
    print(f"\nTOTALS: collapsed coverage "
          f"{totals['weighted_coverage_collapsed_pct']}% "
          f"({totals['detected']}/{totals['faults_collapsed']}) | "
          f"full coverage {totals['weighted_coverage_full_pct']}% | "
          f"patterns {totals['patterns']} | "
          f"{totals['t_total_s']}s")
    print(f"wrote {results_dir / 'summary.md'} and summary.json")


if __name__ == "__main__":
    main()
