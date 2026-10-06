"""Stuck-at fault sites and fault collapsing.

Fault model
-----------
The '.bench' format has no explicit fanout stems/branches, so
`expand_fanout_branches` rewrites the netlist into an equivalent one in
which every net drives at most one sink: nets with multiple consumers get
BUFF gates inserted, one per consumer (including one per primary-output
connection).  After expansion a fault site is simply a net, and the
classic stem/branch accounting falls out naturally:

  full fault list = every net x {s-a-0, s-a-1}

Collapsing applies the standard equivalence rules (e.g. an input s-a-0 of
a NAND is equivalent to its output s-a-1) merged by union-find, followed
by dominance rules that drop the gate output fault covered by an input
fault (e.g. every test detecting an input s-a-1 of an AND gate also
detects the output s-a-1, so the output fault needs no test of its own).
Stem faults of multi-branch nets are kept: forcing all branches can mask
a single branch's effect, so no per-branch test set covers a stem fault.
ATPG targets the collapsed set; reports give coverage over both the
collapsed and the full lists.
"""

from __future__ import annotations

from .bench import Circuit, Gate

# equivalence rules: gate input fault value -> equivalent output fault value
_EQUIV = {
    "AND":  [(0, 0)],
    "NAND": [(0, 1)],
    "OR":   [(1, 1)],
    "NOR":  [(1, 0)],
    "NOT":  [(0, 1), (1, 0)],
    "BUFF": [(0, 0), (1, 1)],
    "XOR":  [],
    "XNOR": [],
}
# dominance rules: (kept input fault value, dropped output fault value).
# Valid because tests(kept) is a subset of tests(dropped): every test that
# detects the input fault forces both faulty machines to agree at the gate
# output, so downstream behaviour is identical and the output fault is
# detected by the same vector.  Examples (verified from exact test sets):
#   AND,  in s-a-1: tests {in=0, others=1}  subset of out s-a-1 tests
#                                        {out good = 0} = {some input 0}
#   NAND, in s-a-1: tests {in=0, others=1}  subset of out s-a-0 tests
#                                        {out good = 1} = {some input 0}
#   OR,   in s-a-0: tests {in=1, others=0}  subset of out s-a-0 tests
#                                        {out good = 1} = {some input 1}
#   NOR,  in s-a-0: tests {in=1, others=0}  subset of out s-a-1 tests
#                                        {out good = 0} = {some input 1}
_DOMINATED = {
    "AND":  [(1, 1)],
    "NAND": [(1, 0)],
    "OR":   [(0, 0)],
    "NOR":  [(0, 1)],
    "NOT":  [],
    "BUFF": [],
    "XOR":  [],
    "XNOR": [],
}


def expand_fanout_branches(circuit: Circuit) -> Circuit:
    """Return an equivalent circuit where every net drives at most one sink.

    Multi-consumer nets keep their name and drive one BUFF per consumer;
    the BUFF outputs are the explicit fanout branches.  Primary-output
    connections are branches like any other.
    """
    out = Circuit(f"{circuit.name}+branches")
    out.inputs = list(circuit.inputs)

    old_fanout = circuit.fanout  # net -> consuming gate names
    for gname, g in circuit.gates.items():
        new_inputs = []
        for pin, inp in enumerate(g.inputs):
            n_consumers = len(old_fanout.get(inp, [])) + (
                1 if inp in circuit.outputs else 0
            )
            if n_consumers > 1:
                branch = f"{inp}~{gname}.{pin}"
                out.gates[branch] = Gate(branch, "BUFF", (inp,))
                new_inputs.append(branch)
            else:
                new_inputs.append(inp)
        out.gates[gname] = Gate(gname, g.gtype, tuple(new_inputs))

    # primary outputs: branch them off too when the net has other consumers
    for po in circuit.outputs:
        n_consumers = len(old_fanout.get(po, [])) + 1
        if n_consumers > 1:
            branch = f"{po}~PO"
            out.gates[branch] = Gate(branch, "BUFF", (po,))
            out.outputs.append(branch)
        else:
            out.outputs.append(po)

    out._validate()
    out.build_graph()
    return out


def full_fault_list(circuit: Circuit):
    """[(net, stuck)] for every net of an expanded circuit."""
    return [(net, v) for net in circuit.nets for v in (0, 1)]


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        p = self.parent.setdefault(x, x)
        while p != self.parent[p]:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        self.parent[x] = p
        return p

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def collapse_faults(circuit: Circuit, faults):
    """Equivalence + dominance collapsing.

    Returns (collapsed, representative) where:
      collapsed      -- the fault subset ATPG must target (sorted in net
                        topological order), and
      representative -- dict mapping every input fault to the collapsed
                        fault that covers it.
    """
    uf = _UnionFind()
    for f in faults:
        uf.find(f)

    pos = {net: i for i, net in enumerate(circuit.topo)}

    def key(f):
        site, stuck = f
        return (pos[site], stuck)

    def stem_input(g):
        """True for a branch BUFF whose input is a multi-branch stem.

        The stem fault forces every sibling branch, so it is NOT a local
        pin fault of this buffer and gate-level equivalence/dominance
        must not be applied across it.
        """
        return (
            g.gtype == "BUFF"
            and len(circuit.fanout.get(g.inputs[0], ())) > 1
        )

    # 1) equivalence merging
    for g in circuit.gates.values():
        if stem_input(g):
            continue
        rules = _EQUIV[g.gtype]
        for in_val, out_val in rules:
            for inp in g.inputs:
                uf.union((inp, in_val), (g.name, out_val))

    # 2) dominance: the gate output fault is dropped; every test for the
    #    kept input fault also detects it (tests(kept) subset of tests(dropped))
    root = {f: uf.find(f) for f in faults}
    dominated_by = {}  # rep -> set of dominating reps
    for g in circuit.gates.values():
        if stem_input(g):
            continue
        for in_val, out_val in _DOMINATED[g.gtype]:
            kept = root[(g.inputs[0], in_val)]
            dropped = root[(g.name, out_val)]
            if kept == dropped:
                continue
            dominated_by.setdefault(dropped, set()).add(kept)

    # dominance is transitive; a class is kept only if nothing dominates it
    kept_roots = {
        root[f]
        for f in faults
        if not dominated_by.get(root[f])
    }

    # canonical representative per kept class: earliest net, then stuck=0
    best = {}
    for f in faults:
        r = root[f]
        if r in kept_roots:
            cur = best.get(r)
            if cur is None or key(f) < key(cur):
                best[r] = f

    collapsed = sorted(best.values(), key=key)

    # map every fault to the collapsed fault covering it:
    #   same class           -> the class representative
    #   dominated class      -> a dominating representative (transitively)
    representative = {}
    for f in faults:
        r = root[f]
        if r in best:
            representative[f] = best[r]
    for r in dominated_by:
        # walk the dominance DAG to the nearest kept ancestor
        frontier = [r]
        seen = set()
        target = None
        while frontier and target is None:
            nxt = []
            for node in frontier:
                if node in seen:
                    continue
                seen.add(node)
                for dom in dominated_by.get(node, ()):
                    if dom in kept_roots:
                        target = dom
                        break
                    nxt.append(dom)
                if target is not None:
                    break
            frontier = nxt
        if target is None:  # pragma: no cover - dominance is acyclic
            raise RuntimeError("dominance graph has no kept ancestor")
        representative_rep = best[target]
        # every fault in class r maps to that representative
        for f in faults:
            if root[f] == r:
                representative[f] = representative_rep

    return collapsed, representative


def fault_summary(circuit: Circuit):
    """(n_nets, full_fault_count) helper for reports."""
    return len(circuit.nets), 2 * len(circuit.nets)
