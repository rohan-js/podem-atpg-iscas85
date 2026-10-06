"""Report writers: summary.json / summary.md and test-vector files."""

from __future__ import annotations

import json


def summarize_circuit(orig, expanded, faults, collapsed, rep, res,
                      t_collapse):
    """One circuit's metrics dict from the run_atpg result."""
    det_full = sum(1 for f in faults if res["detection"][rep[f]])
    n_col = len(collapsed)
    return {
        "circuit": orig.name,
        "inputs": len(orig.inputs),
        "outputs": len(orig.outputs),
        "gates": len(orig.gates),
        "nets_expanded": len(expanded.nets),
        "faults_full": len(faults),
        "faults_collapsed": n_col,
        "detected": len(res["detected"]),
        "redundant": len(res["redundant"]),
        "aborted": len(res["aborted"]),
        "coverage_collapsed_pct": round(100.0 * len(res["detected"]) / n_col,
                                        2) if n_col else 100.0,
        "coverage_full_pct": round(100.0 * det_full / len(faults), 2),
        "patterns_raw": res["raw_vector_count"],
        "patterns": len(res["vectors"]),
        "t_collapse_s": round(t_collapse, 3),
        "t_atpg_s": round(res["t_atpg"], 3),
        "t_compact_s": round(res["t_compact"], 3),
        "t_total_s": round(res["t_total"], 3),
    }


def write_summary(results, path_md, path_json):
    """results: list of per-circuit metrics dicts (summary_circuit form)."""
    tot_col = sum(r["faults_collapsed"] for r in results)
    tot_det = sum(r["detected"] for r in results)
    tot_full = sum(r["faults_full"] for r in results)
    tot_det_full = sum(
        round(r["coverage_full_pct"] / 100.0 * r["faults_full"])
        for r in results)
    totals = {
        "circuits": len(results),
        "faults_collapsed": tot_col,
        "detected": tot_det,
        "weighted_coverage_collapsed_pct": round(100.0 * tot_det / tot_col, 2)
        if tot_col else 100.0,
        "faults_full": tot_full,
        "detected_full": tot_det_full,
        "weighted_coverage_full_pct": round(100.0 * tot_det_full / tot_full, 2)
        if tot_full else 100.0,
        "patterns": sum(r["patterns"] for r in results),
        "t_total_s": round(sum(r["t_total_s"] for r in results), 3),
    }

    with open(path_json, "w", encoding="utf-8") as fh:
        json.dump({"totals": totals, "circuits": results}, fh, indent=2)

    lines = [
        "# ISCAS-85 Stuck-At ATPG Results (PODEM + parallel-pattern fault simulation)",
        "",
        f"Weighted collapsed-fault coverage: **{totals['weighted_coverage_collapsed_pct']}%**"
        f" ({tot_det}/{tot_col} collapsed faults)"
        f" | full-list coverage: **{totals['weighted_coverage_full_pct']}%**"
        f" ({tot_det_full}/{tot_full})",
        "",
        "| circuit | PI | PO | gates | faults (full) | faults (collapsed) | detected | redundant | aborted | coverage (collapsed) | coverage (full) | patterns | ATPG s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['circuit']} | {r['inputs']} | {r['outputs']} | "
            f"{r['gates']} | {r['faults_full']} | {r['faults_collapsed']} | "
            f"{r['detected']} | {r['redundant']} | {r['aborted']} | "
            f"{r['coverage_collapsed_pct']}% | {r['coverage_full_pct']}% | "
            f"{r['patterns']} | {r['t_atpg_s']} |")
    lines += [
        "",
        "Fault model: stuck-at on every net of the branch-expanded circuit",
        "(stems and fanout branches are separate sites).  Collapsed via",
        "equivalence + dominance; full-list coverage derived through the",
        "collapse representative mapping validated in the test suite.",
        "Redundant = PODEM proved untestable.  Aborted = backtrack limit hit",
        "on the deterministic pass and on all randomized-restart attempts.",
        "",
    ]
    with open(path_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return totals


def write_vectors(circuit, vectors, path):
    """One pattern per line, PI order = the netlist's INPUT order."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# {circuit.name}: {len(vectors)} test vectors "
                 f"(PI order: {' '.join(circuit.inputs)})\n")
        for vec in vectors:
            fh.write(" ".join(str(v) for v in vec) + "\n")
