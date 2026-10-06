"""PODEM stuck-at ATPG (Path-Oriented Decision Making).

Implementation of the classic PODEM algorithm (Goel 1981) on the
branch-expanded circuit:

  * good/faulty machine values per net, 3-valued (0, 1, X); the pair
    (good, faulty) carries the D/D' information implicitly,
  * the fault site's faulty value is pinned to the stuck value,
  * event-driven implication: only gates in the fanout cone of a changed
    net are re-evaluated,
  * objective -> backtrace -> PI assignment -> implicit binary search
    over PI assignments,
  * X-path pruning: a branch is cut only when the difference provably
    cannot reach a primary output any more -- a line is a dead carrier
    exactly when both machines are known and agree on it (a line inside
    the fault cone with a forced faulty value but an undetermined good
    value is still a valid carrier, so pruning on faulty-machine X would
    be unsound),
  * SCOAP controllability guides backtrace input choice; D-frontier
    gates closest to a primary output are preferred,
  * a backtrack limit converts pathological faults into honest "aborted"
    outcomes (never silently dropped).

PODEM outcomes per fault:
    "detected"  a vector was generated (detection re-verified later by
                the independent bit-parallel fault simulator)
    "redundant" the search exhausted the decision space -> provably
                untestable under the stuck-at model
    "aborted"   the backtrack limit was hit; testability unknown
"""

from __future__ import annotations

import random
from collections import deque

from .bench import Circuit

ZERO, ONE, X = 0, 1, 2

# value that lets a difference propagate through the gate
_NONCTRL = {"AND": ONE, "NAND": ONE, "OR": ZERO, "NOR": ZERO,
            "XOR": ZERO, "XNOR": ZERO}


def eval3(gtype, vals):
    """3-valued gate evaluation (0/1/X)."""
    if gtype == "BUFF":
        return vals[0]
    if gtype == "NOT":
        v = vals[0]
        return X if v == X else 1 - v
    if gtype in ("AND", "NAND"):
        has_x = False
        for v in vals:
            if v == ZERO:
                return ONE if gtype == "NAND" else ZERO
            if v == X:
                has_x = True
        if has_x:
            return X
        return ZERO if gtype == "NAND" else ONE
    if gtype in ("OR", "NOR"):
        has_x = False
        for v in vals:
            if v == ONE:
                return ZERO if gtype == "NOR" else ONE
            if v == X:
                has_x = True
        if has_x:
            return X
        return ONE if gtype == "NOR" else ZERO
    if gtype in ("XOR", "XNOR"):
        acc = ZERO
        for v in vals:
            if v == X:
                return X
            acc ^= v
        return acc if gtype == "XOR" else 1 - acc
    raise ValueError(f"unknown gate type {gtype}")


def _controllability(circuit):
    """SCOAP combinational controllability (CC0, CC1) per net."""
    cc0, cc1 = {}, {}
    for net in circuit.topo:
        g = circuit.gates.get(net)
        if g is None:  # primary input
            cc0[net] = cc1[net] = 1
            continue
        t, ins = g.gtype, g.inputs
        if t in ("AND", "NAND"):
            # out=1 iff all inputs 1; out=0 iff some input 0
            cc1[net] = sum(cc1[i] for i in ins) + 1
            cc0[net] = min(cc0[i] for i in ins) + 1
        elif t in ("OR", "NOR"):
            # out=1 iff some input 1; out=0 iff all inputs 0
            cc1[net] = min(cc1[i] for i in ins) + 1
            cc0[net] = sum(cc0[i] for i in ins) + 1
        elif t == "NOT":
            cc0[net] = cc1[ins[0]] + 1
            cc1[net] = cc0[ins[0]] + 1
        elif t == "BUFF":
            cc0[net] = cc0[ins[0]] + 1
            cc1[net] = cc1[ins[0]] + 1
        elif t in ("XOR", "XNOR"):
            c0, c1 = cc0[ins[0]], cc1[ins[0]]
            for i in ins[1:]:
                a0, a1, b0, b1 = c0, c1, cc0[i], cc1[i]
                c0, c1 = min(a0 + b0, a1 + b1) + 1, min(a0 + b1, a1 + b0) + 1
            if t == "XOR":
                cc0[net], cc1[net] = c0, c1
            else:
                cc0[net], cc1[net] = c1, c0
        else:
            raise ValueError(f"unknown gate type {t}")
    return cc0, cc1


def _distance_to_po(circuit):
    """Minimum gate hops from each net to some primary output."""
    big = float("inf")
    dist = {}
    for net in reversed(circuit.topo):
        if net in circuit.outputs:
            dist[net] = 0
            continue
        best = big
        for g in circuit.fanout.get(net, ()):
            d = dist.get(g, big)
            if d < best:
                best = d
        dist[net] = best + 1 if best != big else big
    return dist


class Abort(Exception):
    """Raised when the backtrack limit is exhausted."""


class PodemEngine:
    """Single-fault PODEM over one circuit.  Reusable across faults."""

    def __init__(self, circuit: Circuit):
        self.c = circuit
        if not hasattr(circuit, "topo"):
            circuit.build_graph()
        self.inputs = circuit.inputs
        self.gates = circuit.gates
        self.fanout = circuit.fanout
        self.po_set = set(circuit.outputs)
        self.gates_topo = [n for n in circuit.topo if n in circuit.gates]
        self._cc0, self._cc1 = _controllability(circuit)
        self._d_po = _distance_to_po(circuit)
        self._pos = {net: i for i, net in enumerate(circuit.topo)}
        self.jitter = None  # optional Random for randomized restarts

    # ---------------- state handling ----------------

    def _init_state(self, site, stuck):
        self.site = site
        self.stuck = stuck
        self.gval = {net: X for net in self.c.nets}
        self.fval = {net: X for net in self.c.nets}
        self.fval[site] = stuck  # fault injection (constant)
        self.propagated = False
        # full initial implication in topo order
        for gname in self.gates_topo:
            g = self.gates[gname]
            self.gval[gname] = eval3(g.gtype, [self.gval[i] for i in g.inputs])
            nf = eval3(g.gtype, [self.fval[i] for i in g.inputs])
            if gname == site:
                nf = stuck
            self.fval[gname] = nf
        self.propagated = self._check_propagated()
        # the difference can only ever live inside the site's fanout cone
        start = self._pos[site]
        cone_set = set()
        stack = list(self.fanout.get(site, ()))
        while stack:
            g = stack.pop()
            if g in cone_set:
                continue
            cone_set.add(g)
            stack.extend(self.fanout.get(g, ()))
        self.cone = [n for n in self.c.topo[start + 1:] if n in cone_set]
        self.cone_set = cone_set

    def _check_propagated(self):
        for po in self.c.outputs:
            g, f = self.gval[po], self.fval[po]
            if g != X and f != X and g != f:
                return True
        return False

    def _imply(self, start_net):
        q = deque([start_net])
        while q:
            n = q.popleft()
            for gname in self.fanout.get(n, ()):
                g = self.gates[gname]
                ng = eval3(g.gtype, [self.gval[i] for i in g.inputs])
                nf = eval3(g.gtype, [self.fval[i] for i in g.inputs])
                if gname == self.site:
                    nf = self.stuck
                if ng != self.gval[gname] or nf != self.fval[gname]:
                    self.gval[gname] = ng
                    self.fval[gname] = nf
                    q.append(gname)
        # recompute (not OR) -- unassignment must be able to clear it
        self.propagated = self._check_propagated()

    def _assign(self, pi, val):
        self.gval[pi] = val
        self.fval[pi] = self.stuck if pi == self.site else val
        self._imply(pi)

    def _unassign(self, pi):
        self.gval[pi] = X
        self.fval[pi] = self.stuck if pi == self.site else X
        self._imply(pi)

    # ---------------- PODEM primitives ----------------

    def _d_frontier(self):
        """Cone gates where the difference can still be moved forward.

        A gate qualifies when some input already carries the difference
        (good != faulty, both known) and its output is neither dead
        (both machines known and equal -- fixed forever) nor already
        carrying the difference.  Note the output may be X in only one
        machine: a stuck value can force the faulty output while the
        good value is still undetermined, and that gate is a valid
        frontier element.
        """
        frontier = []
        gval, fval = self.gval, self.fval
        for gname in self.cone:
            gv, fv = gval[gname], fval[gname]
            if gv != X and fv != X:
                continue  # dead (equal) or already carrying (differ)
            g = self.gates[gname]
            for inp in g.inputs:
                gg, ff = gval[inp], fval[inp]
                if gg != X and ff != X and gg != ff:
                    frontier.append(gname)
                    break
        return frontier

    def _x_path_ok(self):
        """Sound necessary condition for propagation.

        A line can carry the difference unless both machines are known
        and agree on it (such a line is fixed forever).  The final PO is
        always a valid endpoint; every intermediate carrier must still be
        capable of holding the difference.
        """
        gg, ff = self.gval[self.site], self.fval[self.site]
        if not (gg != X and ff != X and gg != ff):
            return True  # fault not activated yet; nothing to prune on
        gval, fval = self.gval, self.fval
        reach = {}
        for net in reversed(self.cone):
            if net in self.po_set:
                reach[net] = True
                continue
            r = False
            for g in self.fanout.get(net, ()):
                if not reach.get(g):
                    continue
                gv, fv = gval[g], fval[g]
                if gv == X or fv == X or gv != fv:
                    r = True
                    break
            reach[net] = r
        for gname in self._d_frontier():
            if reach.get(gname):
                return True
        return False

    def _pick_x_input(self, gate, value):
        """Easiest-to-control X input for `value` (SCOAP, port tie-break)."""
        return self._pick_input(gate, value, self.gval)

    def _pick_fval_x_input(self, gate, value):
        """Same, but over the faulty machine's undetermined inputs."""
        return self._pick_input(gate, value, self.fval)

    def _pick_input(self, gate, value, vals):
        cands = [inp for inp in gate.inputs if vals[inp] == X]
        if not cands:
            return None
        if self.jitter is not None:
            return self.jitter.choice(cands)
        cc = self._cc1 if value == ONE else self._cc0
        return min(cands, key=lambda i: (cc[i], i))

    def _needed_faulty_value(self, gate, inp, gv_out):
        """Faulty value for `inp` that makes the gate output differ from the
        known good output (None when neither completion can)."""
        idx = gate.inputs.index(inp)
        base = [self.fval[i] for i in gate.inputs]
        for w in (ONE, ZERO):
            trial = list(base)
            trial[idx] = w
            out = eval3(gate.gtype, trial)
            if out != X and out != gv_out:
                return w
        return None

    def _objective(self):
        """(net, value, machine) objective, or None when the branch is dead.

        machine is 'good' for the classic objective or 'faulty' when the
        frontier gate's good output is already determined and propagation
        must complete through the faulty machine (a stuck value can force
        the good side of NAND-built XOR trees while the faulty side is
        still undetermined -- those gates have no good-machine X input to
        assign, and skipping them makes testable faults look redundant).
        """
        g = self.gval[self.site]
        if g == self.stuck:
            return None  # fault cannot be activated any more
        if g == X:
            return (self.site, 1 - self.stuck, "good")
        frontier = self._d_frontier()
        for gname in sorted(frontier,
                            key=lambda n: (self._d_po.get(n, 1 << 30), n)):
            gate = self.gates[gname]
            nc = _NONCTRL[gate.gtype]
            inp = self._pick_x_input(gate, nc)
            if inp is not None:
                return (inp, nc, "good")
            gv_out = self.gval[gname]
            if gv_out != X:
                for inp2 in gate.inputs:
                    if self.fval[inp2] == X:
                        w = self._needed_faulty_value(gate, inp2, gv_out)
                        if w is not None:
                            return (inp2, w, "faulty")
        return None

    def _backtrace(self, net, val):
        """Walk the objective down to a primary input (good machine)."""
        return self._walk(net, val, self.gval)

    def _backtrace_faulty(self, net, val):
        """Walk a faulty-machine objective down to a primary input.

        The PI assignment drives both machines; landing on the fault site
        is useless (its faulty value is pinned), so report a conflict.
        """
        pi, v = self._walk(net, val, self.fval)
        if pi is not None and pi == self.site:
            return None, None
        return pi, v

    def _walk(self, net, val, vals):
        while net in self.gates:
            g = self.gates[net]
            t = g.gtype
            if t in ("AND", "NAND", "OR", "BUFF"):
                nv = val
            elif t in ("NOR", "NOT"):
                nv = 1 - val
            else:  # XOR/XNOR: fixing one input to 0 keeps the search complete
                nv = ZERO
            chosen = self._pick_input(g, nv, vals)
            if chosen is None:
                return None, None
            net, val = chosen, nv
        return net, val

    # ---------------- search ----------------

    def podem(self, site, stuck, backtrack_limit=10000, fill_rng=None,
              jitter_rng=None):
        """Generate a test for (site, stuck).

        Returns (vector, status); vector is a PI-value list with X entries
        randomly filled (fill_rng) -- detection holds for any completion
        because every implied value was computed under 3-valued semantics.
        jitter_rng randomizes decision tie-breaking (randomized-restart
        ATPG for faults the deterministic search cannot crack).
        """
        self._init_state(site, stuck)
        self.jitter = jitter_rng
        self.backtracks = 0
        self.limit = backtrack_limit
        try:
            found = self._search()
        except Abort:
            return None, "aborted"
        if not found:
            return None, "redundant"
        vec = [self.gval[pi] for pi in self.inputs]
        rng = fill_rng or random.Random(42)
        vec = [v if v != X else rng.randrange(2) for v in vec]
        return vec, "detected"

    def _search(self):
        if self.propagated:
            return True
        obj = self._objective()
        if obj is None:
            return False
        net, val, machine = obj
        if machine == "faulty":
            pi, v = self._backtrace_faulty(net, val)
        else:
            pi, v = self._backtrace(net, val)
        if pi is None:
            return False
        for v in (val, 1 - val):
            if self.backtracks > self.limit:
                raise Abort()
            self._assign(pi, v)
            # detection wins immediately; only otherwise may the X-path
            # check prune the branch
            if self.propagated or (self._x_path_ok() and self._search()):
                return True
            self._unassign(pi)
            self.backtracks += 1
        return False
