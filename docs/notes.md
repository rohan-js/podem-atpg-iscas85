# Engineering notes — PODEM ATPG and parallel-pattern fault simulation

This file records the design decisions, the bugs found on the way, and
the reasoning behind every non-obvious choice, so the numbers in
`results/summary.md` can be interpreted (and reproduced) without
reverse-engineering the code.

## 1. Netlist and fault model

* `.bench` netlists are parsed into a DAG (netlist names kept as
  strings; ISCAS-85 names are numeric).
* The `.bench` format has **no explicit fanout stems/branches**, so
  `faults.expand_fanout_branches` rewrites the netlist into an
  equivalent one where every multi-consumer net drives one BUFF gate per
  consumer (including one per primary-output connection). After
  expansion, a fault site is simply a net and the classic stem/branch
  accounting falls out: c432 expands to exactly 432 nets -> 864 full
  faults, matching the classic ISCAS-85 fault counts.
* Good-machine behaviour is bit-for-bit unchanged by the expansion
  (tested).

## 2. Fault collapsing — the rules and their proof obligations

Equivalence (union-find) and dominance rules per gate type. The
dominance direction matters and is easy to get backwards:

* A fault may be **dropped** (covered by another) only when *every test
  for the kept fault also detects the dropped fault*, i.e.
  `tests(kept) ⊆ tests(dropped)`. Then any deterministic ATPG test for
  the kept fault forces both faulty machines to agree from the kept
  fault's site downstream, so the dropped fault is detected by the same
  vector.
* For an AND gate, an input s-a-1 has test set `{in=0, others=1}` while
  the output s-a-1 has test set `{some input 0}` — the input test set is
  the subset, so **the output fault is dropped, the input fault kept**
  (this is the checkpoint theorem direction: testing checkpoints
  suffices). Getting this backwards silently produces collapsed sets
  whose 100% coverage does NOT imply full coverage.
* Equivalence examples: NAND `in s-a-0 ≡ out s-a-1` (both have test set
  "all inputs 1"), NOR `in s-a-1 ≡ out s-a-0`.

**Stem faults are kept** (never dropped against a branch fault): forcing
all branches of a stem is not the same fault as forcing one branch, and
a single branch's test set does not cover the stem's.

*Rule validation:* `tests/test_faults.py` validates every `_EQUIV` and
`_DOMINATED` entry against simulated detection signatures, and checks
the representative-mapping property (every test for a representative
detects every fault it covers) exhaustively on c17 and against 256
random patterns on c432/c499/c880. That property is what lets the
runner report full-list coverage through the representative mapping
without simulating the full list.

*Bug found during development:* applying gate-level rules to branch
BUFFs whose input is a multi-branch stem chained unrelated classes
together (a stem fault forces *all* sibling branches, so it is not a
local pin fault of the buffer). The `stem_input` guard excludes those
gates; before the guard, c17's classes merged into nonsense like
"PI 2 s-a-0 covered by PI 3 s-a-1".

## 3. PODEM — pair-based 5-valued logic

PODEM is implemented with two 3-valued state arrays (good machine,
faulty machine) instead of the classic 5-valued alphabet. The pair
(good, faulty) carries D/D' implicitly: a line carries the difference
when both values are known and differ. The fault site's faulty value is
pinned to the stuck value throughout.

* Event-driven implication: assigning/unassigning a PI re-evaluates only
  the affected fanout cone.
* Objective -> backtrace -> PI assignment -> implicit binary search with
  a backtrack limit; exhausted search = "redundant" (proven untestable),
  limit hit = "aborted" (honest unknown). Aborted faults go through
  randomized-restart ATPG: up to 4 further attempts with jittered
  decision tie-breaking (same backtrack budget, fresh search trajectory
  per attempt) before being reported aborted.
* Heuristics: SCOAP controllability chooses which X input to set during
  backtrace; the D-frontier gate nearest a primary output is preferred.

### The three bugs that matter (all found by differential testing)

1. **D-frontier definition.** The textbook frontier is "gate with a
   D/D' input and X output". In the pair framework, a stuck value can
   *force* the faulty output while the good output is still undetermined
   (stuck-0 into a NAND forces the faulty output to 1). Such a gate —
   pair (good=X, faulty=1) — is a valid frontier element, but a literal
   "output must be X in both machines" condition excludes it. With that
   wrong condition PODEM reported 338/449 c432 faults as "redundant";
   2048 random patterns falsified 335 of those verdicts immediately.
   The fixed frontier accepts any gate with a difference-carrying input
   whose output is neither dead nor already carrying. After the fix:
   0 false-redundant on c432.
2. **X-path pruning.** Pruning on "path of faulty-machine-X lines"
   is unsound for the same reason (a cone line with a forced faulty
   value and undetermined good value is a legal carrier). The sound
   condition: a line is a *dead carrier* exactly when both machines are
   known and agree; prune a branch when no difference-carrying input
   chain of non-dead lines reaches a primary output. A related
   ordering bug — checking X-path before checking whether the
   difference had already reached a PO — made faults whose difference
   lands directly on a PO look redundant; detection is now checked
   first.
3. **Missing faulty-machine objective (c1355).** c1355 implements XOR
   as NAND trees, which creates frontier gates whose good output is
   already determined while the faulty output is still undetermined —
   and whose inputs are all good-machine-known (e.g. a NAND with one
   D-carrying input (0,1) and one (good=1, faulty=X)). Such a gate has
   no good-machine X input to assign, so the classic objective
   returned None and the branch died: PODEM declared PI-1 s-a-1
   *redundant after 2 backtracks* — on a fault the equivalent circuit
   c499 solves instantly and that random patterns detect. The
   objective now falls back to a **faulty-machine objective**: pick an
   input whose faulty value is undetermined, compute the faulty value
   that makes the gate output differ from the known good output
   (brute-forced over both completions), and backtrace that objective
   down the faulty machine to a PI (never landing on the pinned fault
   site). The enforced cross-check in the ATPG driver
   ("redundant faults must never be detected") is what surfaced this —
   the engine fixed itself loudly instead of publishing a wrong 100%.
   `tools/check_redundant.py` generalizes the random-pattern
   falsification to any circuit.

### X-fill correctness

PODEM returns vectors with unassigned PIs (X). Any completion preserves
detection because every implied value was computed under 3-valued
semantics (a value implied with X inputs holds for all completions).
X entries are filled with seeded random bits (`--seed`, default 42) to
diversify incidental detection. The bit-parallel simulator re-verifies
every PODEM vector; a mismatch aborts the run (`EngineInconsistency`).

## 4. Parallel-pattern fault simulation (PPSFP)

Each signal is a Python big-int with one bit per pattern (batches of
1024). Gate evaluation is one bitwise operation per gate per batch;
fault simulation injects the stuck value at the site and re-evaluates
only the site's fanout cone. Detection = any PO word differs from the
good machine. The good machine and the fault engine are validated
against a deliberately naive scalar simulator (`scalar_sim.py`):
exhaustively on c17, and on random patterns for c432 faults.

## 5. ATPG loop and compaction

* Faults targeted in topological order; after every 64 generated
  vectors the new vectors are fault-simulated against all unresolved
  faults, marking incidental detections so their targets are skipped
  (classic periodic fault dropping).
* Cross-checks enforced at run time: every PODEM vector must be
  confirmed by the bit-parallel simulator; a fault proven redundant must
  never be detected; compaction must not lose any detection.
* Reverse-order static compaction: walking vectors last-to-first, a
  vector is dropped when every fault it detects is detected by at least
  one other retained vector (per-fault retained-detector counters).
  Coverage is re-verified after compaction.

## 6. Interpreting the results

* **faults (full)** counts both values on every net of the
  branch-expanded circuit (stems + branches). **faults (collapsed)** is
  the ATPG target set after equivalence + dominance collapsing.
* **coverage (collapsed)** = detected / collapsed; **coverage (full)** is
  derived through the collapse representative mapping (validated in the
  test suite, section 2).
* **redundant** = PODEM exhausted the search space (proof of
  untestability). **aborted** = backtrack budget exhausted twice; the
  fault may or may not be testable. Nothing is silently dropped.
* Coverage expectations: c17 and c432 are fully testable; several larger
  ISCAS-85 circuits (notably c6288) contain genuinely redundant faults
  under the stuck-at model, so 100% is not the expected headline there —
  the per-circuit detected/redundant/aborted split is the honest result.

## 7. Reproducing

```
python -m unittest discover -s tests -t .     # full validation suite
python tools/run_atpg.py --circuits all       # full run, writes results/
python tools/run_atpg.py --circuits c17,c432  # quick smoke run
```

Deterministic: fault order, heuristics and X-fill are seeded (default
seed 42); reruns reproduce the same vectors and coverage.
