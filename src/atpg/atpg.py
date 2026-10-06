"""ATPG driver: PODEM generation with incremental fault dropping.

Flow per circuit:
  1. target the collapsed fault list in topological order,
  2. run PODEM per target; every `drop_every` generated vectors are
     fault-simulated (bit-parallel PPSFP) against the faults still
     undetected, which marks incidental detections so their targets are
     skipped,
  3. every PODEM vector is re-verified by the independent bit-parallel
     simulator -- a mismatch raises (engine cross-check),
  4. faults proven redundant must never be detected by any vector,
  5. aborted faults get one retry with a multiplied backtrack budget,
  6. reverse-order static compaction drops vectors that detect no fault
     uniquely; coverage is preserved and re-verified afterwards.

Coverage over the full fault list is derived through the collapse
representative mapping (fault f is detected iff its representative is),
which the test suite validates exhaustively on c17 and against large
random pattern sets on c432/c499/c880.
"""

from __future__ import annotations

import random
import time

from .podem import PodemEngine
from .simulate import PPSFPFaultSimulator


class EngineInconsistency(RuntimeError):
    """PODEM and the bit-parallel simulator disagree."""


def run_atpg(circuit, faults, *, backtrack_limit=10000, batch=1024,
             seed=42, drop_every=64, restarts=4, log=None):
    """Run ATPG.  Returns a result dict (statuses, vectors, detection...)."""
    t0 = time.perf_counter()
    eng = PodemEngine(circuit)
    sim = PPSFPFaultSimulator(circuit, batch=batch)
    fill = random.Random(seed)

    statuses = dict.fromkeys(faults, "undetected")
    vectors = []
    vec_set = set()
    det = {f: 0 for f in faults}

    def flush(new_vectors, generated):
        """Simulate new vectors against not-yet-resolved faults."""
        if not new_vectors:
            return
        offset = len(vectors)
        vectors.extend(new_vectors)
        targets = [f for f in faults
                   if statuses[f] in ("undetected", "generated", "aborted")]
        if not targets:
            return
        d = sim.run(new_vectors, targets)
        for f in targets:
            if d[f]:
                det[f] |= d[f] << offset
                statuses[f] = "detected"
        for f in generated:
            if det[f] == 0:
                raise EngineInconsistency(
                    f"PODEM vector for {f} was not confirmed by the "
                    f"bit-parallel fault simulator")

    def generate(targets, limit, jitter_seed=None):
        jrng = random.Random(jitter_seed) if jitter_seed is not None else None
        pending, gen_batch, still_aborted = [], [], []
        for f in targets:
            if statuses[f] in ("detected", "redundant"):
                continue
            vec, status = eng.podem(f[0], f[1], limit, fill_rng=fill,
                                    jitter_rng=jrng)
            if status == "detected":
                statuses[f] = "generated"
                gen_batch.append(f)
                t = tuple(vec)
                if t not in vec_set:
                    vec_set.add(t)
                    pending.append(t)
            elif status == "redundant":
                statuses[f] = "redundant"
            else:
                statuses[f] = "aborted"
                still_aborted.append(f)
            if len(pending) >= drop_every:
                flush(pending, gen_batch)
                pending, gen_batch = [], []
        flush(pending, gen_batch)
        return still_aborted

    t_atpg0 = time.perf_counter()
    aborted = generate(faults, backtrack_limit)
    attempt = 0
    while aborted and attempt < restarts:
        # randomized-restart ATPG: same backtrack budget, fresh decision
        # trajectory per attempt
        attempt += 1
        if log:
            log(f"  restart {attempt}: {len(aborted)} faults aborted, "
                f"retrying with jittered decisions")
        aborted = generate(
            [f for f in aborted if statuses[f] == "aborted"],
            backtrack_limit, jitter_seed=seed + attempt)
    t_atpg = time.perf_counter() - t_atpg0

    detected = [f for f in faults if statuses[f] == "detected"]
    redundant = [f for f in faults if statuses[f] == "redundant"]
    aborted_f = [f for f in faults if statuses[f] == "aborted"]

    # reverse-order static compaction on the pre-compaction detection masks
    t_cmp0 = time.perf_counter()
    kept_idx = _reverse_order_compact(vectors, det)
    vectors_c = [vectors[i] for i in kept_idx]
    # recompute detection over the compacted set (coverage must be intact)
    det_c = sim.run(vectors_c, faults) if vectors_c else det
    if vectors_c:
        for f in redundant:
            if det_c[f]:
                raise EngineInconsistency(
                    f"redundant fault {f} detected after compaction")
        for f in detected:
            if not det_c[f]:
                raise EngineInconsistency(
                    f"fault {f} lost detection during compaction")
    t_compact = time.perf_counter() - t_cmp0

    return {
        "statuses": statuses,
        "vectors": vectors_c,
        "raw_vector_count": len(vectors),
        "detection": det_c,
        "detected": detected,
        "redundant": redundant,
        "aborted": aborted_f,
        "t_atpg": t_atpg,
        "t_compact": t_compact,
        "t_total": time.perf_counter() - t0,
    }


def _reverse_order_compact(vectors, det):
    """Indices of vectors to keep (reverse-order greedy, coverage-safe)."""
    n = len(vectors)
    vec_faults = [[] for _ in range(n)]
    for f, mask in det.items():
        m = mask
        while m:
            lsb = m & -m
            i = lsb.bit_length() - 1
            if i < n:
                vec_faults[i].append(f)
            m ^= lsb
    cnt = {f: mask.bit_count() for f, mask in det.items()}
    keep = [False] * n
    for i in range(n - 1, -1, -1):
        fl = vec_faults[i]
        if not fl:
            continue  # detects nothing -> drop
        if all(cnt[f] > 1 for f in fl):
            for f in fl:
                cnt[f] -= 1
            continue  # drop
        keep[i] = True
    return [i for i in range(n) if keep[i]]
