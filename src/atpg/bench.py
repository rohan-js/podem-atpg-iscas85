"""Parser for the ISCAS-85 '.bench' netlist format.

Format (one statement per line, '#' starts a comment):

    INPUT(1)              primary input
    OUTPUT(22)            primary output
    10 = NAND(1, 3)       gate: output net 10, function NAND, inputs 1 and 3

Supported gate functions: AND, OR, NAND, NOR, NOT, BUFF, XOR, XNOR.
Net names are kept as strings; fanout branches are NOT materialised here
(fault generation adds them explicitly, see faults.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

GATE_TYPES = ("AND", "OR", "NAND", "NOR", "NOT", "BUFF", "XOR", "XNOR")

_INPUT_RE = re.compile(r"^INPUT\(\s*(?P<net>[^)]+?)\s*\)$")
_OUTPUT_RE = re.compile(r"^OUTPUT\(\s*(?P<net>[^)]+?)\s*\)$")
_GATE_RE = re.compile(
    r"^(?P<out>[^=]+?)\s*=\s*(?P<type>[A-Za-z]+)\s*\((?P<args>.*)\)$"
)


@dataclass
class Gate:
    name: str
    gtype: str
    inputs: tuple


class Circuit:
    """A combinational circuit with explicit primary inputs/outputs."""

    def __init__(self, name):
        self.name = name
        self.inputs = []          # ordered primary input nets
        self.outputs = []         # ordered primary output nets
        self.gates = {}           # net name -> Gate (gate driving that net)

    # ---------------- parsing ----------------

    @classmethod
    def from_bench_text(cls, name, text):
        ckt = cls(name)
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = _INPUT_RE.match(line)
            if m:
                ckt.inputs.append(m.group("net"))
                continue
            m = _OUTPUT_RE.match(line)
            if m:
                ckt.outputs.append(m.group("net"))
                continue
            m = _GATE_RE.match(line)
            if m:
                out = m.group("out").strip()
                gtype = m.group("type").strip().upper()
                if gtype not in GATE_TYPES:
                    raise ValueError(
                        f"{name}: unsupported gate type '{gtype}' in: {line}"
                    )
                args = tuple(
                    a.strip() for a in m.group("args").split(",") if a.strip()
                )
                if not args:
                    raise ValueError(f"{name}: gate with no inputs: {line}")
                if gtype in ("NOT", "BUFF") and len(args) != 1:
                    raise ValueError(f"{name}: {gtype} must have 1 input: {line}")
                if out in ckt.gates or out in ckt.inputs:
                    raise ValueError(f"{name}: net '{out}' defined twice")
                ckt.gates[out] = Gate(out, gtype, args)
                continue
            raise ValueError(f"{name}: unparseable statement: {line}")
        ckt._validate()
        return ckt

    @classmethod
    def from_bench_file(cls, path):
        path = str(path)
        name = path.replace("\\", "/").rsplit("/", 1)[-1].split(".")[0]
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_bench_text(name, fh.read())

    # ---------------- validation + derived structures ----------------

    def _validate(self):
        defined = set(self.inputs) | set(self.gates)
        missing = []
        for g in self.gates.values():
            for inp in g.inputs:
                if inp not in defined:
                    missing.append((g.name, inp))
        if missing:
            raise ValueError(f"{self.name}: undefined nets: {missing[:5]}...")
        undriven = [o for o in self.outputs if o not in defined]
        if undriven:
            raise ValueError(f"{self.name}: POs not driven: {undriven}")
        if not self.outputs:
            raise ValueError(f"{self.name}: no primary outputs")

    def build_graph(self):
        """Compute fanout lists, levels and a topological order (PIs first).

        Idempotent; called by every consumer that needs traversal order.
        """
        self.fanout = {net: [] for net in self.nets}
        for g in self.gates.values():
            for inp in g.inputs:
                self.fanout[inp].append(g.name)

        # Kahn topological sort over gates; deterministic (input order kept).
        self.level = {}
        for pi in self.inputs:
            self.level[pi] = 0
        order = []
        pending = list(self.gates.values())
        remaining_deps = {
            g.name: sum(1 for inp in g.inputs if inp in self.gates)
            for g in pending
        }
        ready = [g.name for g in pending if remaining_deps[g.name] == 0]
        # PIs come first in topo order
        order.extend(self.inputs)
        while ready:
            nxt = ready.pop(0)
            order.append(nxt)
            lvl = self.level.get(nxt, 0) + 1
            for fan in self.fanout[nxt]:
                self.level[fan] = max(self.level.get(fan, 0), lvl)
                remaining_deps[fan] -= 1
                if remaining_deps[fan] == 0:
                    ready.append(fan)
        if len(order) != len(self.inputs) + len(self.gates):
            raise ValueError(f"{self.name}: combinational loop detected")
        self.topo = order
        return self

    # ---------------- convenience ----------------

    @property
    def nets(self):
        return list(self.inputs) + list(self.gates)

    def gate_count(self):
        return len(self.gates)

    def stats(self):
        return {
            "name": self.name,
            "inputs": len(self.inputs),
            "outputs": len(self.outputs),
            "gates": len(self.gates),
        }
